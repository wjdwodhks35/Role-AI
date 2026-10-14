import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from role_ai.artifacts import ArtifactStore, canonical
from role_ai.config import Settings
from role_ai.main import create_app
from role_ai.models import AutoApprovalPolicy, DatasetSnapshot, User


@pytest.fixture
def approval_app(tmp_path):
    model = tmp_path / "base"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"fixture-only")
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'approval.db'}",
        storage_dir=str(tmp_path / "artifacts"), training_base_model_path=str(model),
        training_base_model_revision="a" * 40, training_enabled=True,
        registration_enabled=True, demo_enabled=True, default_provider="mock",
        bootstrap_email="owner@example.test", bootstrap_password="safe-owner-password-123",
        openai_enabled=True, openai_api_key="fake-approval-key", openai_model="gpt-test",
        openai_input_usd_per_million=Decimal("1"), openai_output_usd_per_million=Decimal("2"),
        openai_daily_budget_usd=Decimal("1"), openai_request_budget_usd=Decimal("0.25"),
    )
    calls, state = [], {"case": "pass"}
    holder = {}
    def handler(request):
        calls.append(request)
        if state["case"] == "timeout":
            raise httpx.ReadTimeout("fake-approval-key")
        if state["case"] == "disable":
            with holder["app"].state.database_factory() as db:
                policy = db.scalar(select(AutoApprovalPolicy))
                policy.enabled = False
                policy.revision += 1
                db.commit()
        if state["case"] == "mutate":
            with holder["app"].state.database_factory() as db:
                item = db.scalar(select(DatasetSnapshot))
                item.revision += 1
                db.commit()
        decision = {"verdict": "reject" if state["case"] == "reject" else "pass",
                    "reason": "fixture review", "risks": ["risk"] if state["case"] == "risk" else []}
        text = "bad JSON" if state["case"] == "bad_json" else json.dumps(decision)
        return httpx.Response(200, json={
            "model": "gpt-test", "status": "completed",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}],
        })
    app = create_app(settings, openai_transport=httpx.MockTransport(handler))
    holder["app"] = app
    with TestClient(app) as client:
        data = client.post("/api/auth/login", json={
            "email": "owner@example.test", "password": "safe-owner-password-123"}).json()
        client.headers["X-CSRF-Token"] = data["csrf_token"]
        uid, sid = data["user"]["id"], str(uuid4())
        manifest, data_hash = ArtifactStore(settings).create(uid, sid, [
            {"prompt": f"Question {i}", "completion": f"Answer {i}"} for i in range(30)
        ], {"type": "huggingface", "license": "mit", "collection": "autonomous_bounded_prefix"})
        reviews = {stage: {"verdict": "pass", "reason": "local fixture",
                           "provider": "ollama", "data_hash": data_hash}
                   for stage in ["teacher", "reviewer", "orchestrator"]}
        with app.state.database_factory() as db:
            db.add(DatasetSnapshot(id=sid, user_id=uid, name="collected",
                                   manifest=canonical(manifest), data_hash=data_hash,
                                   reviews=canonical(reviews), state="reviewed"))
            db.commit()
        yield client, settings, calls, state, sid


def enable(client, **extra):
    return client.put("/api/learning/auto-approval", json={
        "enabled": True, "allow_paid": True, "license_reviewed": True,
        "privacy_reviewed": True, "model_license_reviewed": True, **extra
    })


def evaluate(client, sid):
    return client.post("/api/learning/auto-approval/" + sid + "/evaluate")


def test_auto_policy_default_off_and_scope(approval_app):
    client, _, calls, _, sid = approval_app
    policy = client.get("/api/learning/auto-approval").json()
    assert not policy["enabled"] and not policy["automatic_enqueue"]
    assert evaluate(client, sid).status_code == 409
    assert calls == []


def test_auto_policy_requires_consent_and_configuration(approval_app):
    client, settings, calls, _, _ = approval_app
    assert enable(client, allow_paid=False).status_code == 422
    assert enable(client, privacy_reviewed=False).status_code == 422
    assert enable(client, max_steps=101).status_code == 422
    settings.openai_enabled = False
    assert enable(client).status_code == 503
    assert calls == []


def test_auto_policy_owner_and_csrf(approval_app):
    client, _, calls, _, sid = approval_app
    client.headers.pop("X-CSRF-Token")
    assert enable(client).status_code == 403
    response = client.post("/api/auth/register", json={
        "email": "other@example.test", "password": "safe-test-password-123"})
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert client.get("/api/learning/auto-approval").status_code == 403
    uid = response.json()["user"]["id"]
    with client.app.state.database_factory() as db:
        db.get(User, uid).is_owner = True
        db.commit()
    assert evaluate(client, sid).status_code == 404
    assert client.get("/api/learning/auto-approval/history").json() == []
    assert calls == []


def test_gpt_full_selected_data_approval_no_training(approval_app):
    client, _, calls, _, sid = approval_app
    assert enable(client, target_skill="professor", max_steps=15).status_code == 200
    result = evaluate(client, sid)
    assert result.status_code == 200 and result.json()["state"] == "approved"
    body = json.loads(calls[0].content)
    data = json.loads(body["input"][1]["content"])
    assert len(data["train_samples"]) + len(data["eval_samples"]) == 30
    assert body["store"] is False and body["tools"] == []
    item = client.get("/api/learning/datasets").json()[0]
    assert item["state"] == "approved" and item["approval_hash"]
    assert item["reviews"]["orchestrator"]["provider"] == "openai"
    assert item["plan"]["target_skill"] == "professor" and item["plan"]["max_steps"] == 15
    assert client.get("/api/learning/jobs").json() == []
    assert client.get("/api/learning/auto-approval/history").json()[0]["detail"]["sample_count"] == 30


@pytest.mark.parametrize("case", ["reject", "risk", "bad_json", "timeout"])
def test_gpt_reject_risk_and_failures_no_approval(approval_app, case):
    client, _, calls, state, sid = approval_app
    assert enable(client).status_code == 200
    state["case"] = case
    result = evaluate(client, sid).json()
    assert result["state"] == ("rejected" if case in {"reject", "risk"} else "failed")
    item = client.get("/api/learning/datasets").json()[0]
    assert item["approval_hash"] is None and item["state"] == "reviewed"
    assert evaluate(client, sid).status_code == 409
    assert len(calls) == 1


def test_auto_approval_budget_checked_before_request(approval_app):
    client, settings, calls, _, sid = approval_app
    settings.openai_request_budget_usd = Decimal("0.000001")
    assert enable(client).status_code == 200
    assert evaluate(client, sid).json()["state"] == "failed"
    assert calls == []


@pytest.mark.parametrize("case", ["disable", "mutate"])
def test_auto_approval_race_revocation_and_data_revision(approval_app, case):
    client, _, calls, state, sid = approval_app
    assert enable(client).status_code == 200
    state["case"] = case
    assert evaluate(client, sid).json()["state"] == "cancelled"
    assert client.get("/api/learning/datasets").json()[0]["approval_hash"] is None
    assert len(calls) == 1


def test_auto_policy_model_and_budget_change_blocked(approval_app):
    client, settings, calls, _, sid = approval_app
    assert enable(client).status_code == 200
    settings.openai_daily_budget_usd = Decimal("2")
    assert evaluate(client, sid).status_code == 409
    assert enable(client).status_code == 200
    from pathlib import Path
    (Path(settings.training_base_model_path) / "model.safetensors").write_bytes(b"changed weights")
    assert evaluate(client, sid).status_code == 409
    assert calls == []


def test_auto_policy_disable_revokes_queued_approval(approval_app):
    client, _, _, _, sid = approval_app
    assert enable(client).status_code == 200
    assert evaluate(client, sid).json()["state"] == "approved"
    assert client.post("/api/learning/datasets/" + sid + "/enqueue").status_code == 201
    response = client.put("/api/learning/auto-approval", json={"enabled": False})
    assert response.status_code == 200 and not response.json()["enabled"]
    item = client.get("/api/learning/datasets").json()[0]
    assert item["approval_hash"] is None and item["state"] == "reviewed"
    assert client.get("/api/learning/jobs").json()[0]["state"] == "cancelled"
    assert client.get("/api/learning/auto-approval/history").json()[0]["state"] == "revoked"


def test_auto_approval_concurrent_request_single_paid_call(approval_app):
    client, _, calls, _, sid = approval_app
    assert enable(client).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: evaluate(client, sid).status_code, range(2)))
    assert sorted(statuses) == [200, 409] and len(calls) == 1


def test_auto_collection_invokes_enabled_gpt_policy(approval_app):
    from test_collection import prepare
    from role_ai.collection import run_one
    client, settings, calls, _, _ = approval_app
    settings.hf_download_enabled = True
    hf, _ = prepare(client)
    assert enable(client).status_code == 200
    assert client.post("/api/learning/collection", json={
        "topic": "python", "max_runs": 1}).status_code == 201
    result = asyncio.run(run_one(settings, client.app.state.database_factory,
                                client.app.state.providers, hf))
    assert result["auto_approval"]["state"] == "approved"
    assert result["training"] == "not_started" and len(calls) == 1
    assert client.get("/api/learning/jobs").json() == []


@pytest.mark.parametrize("case", ["manual", "oversized", "local_reject"])
def test_auto_approval_scope_and_local_review_blocks(approval_app, case):
    client, _, calls, _, sid = approval_app
    assert enable(client).status_code == 200
    with client.app.state.database_factory() as db:
        item = db.get(DatasetSnapshot, sid)
        manifest, reviews = json.loads(item.manifest), json.loads(item.reviews)
        if case == "manual":
            manifest["source"].pop("collection")
        elif case == "oversized":
            manifest["received_rows"] = 31
        else:
            reviews["teacher"]["verdict"] = "reject"
        item.manifest, item.reviews = canonical(manifest), canonical(reviews)
        db.commit()
    assert evaluate(client, sid).status_code == 409 and calls == []


def test_auto_approval_tampered_artifact_blocked(approval_app):
    client, settings, calls, _, sid = approval_app
    assert enable(client).status_code == 200
    uid = client.get("/api/auth/me").json()["user"]["id"]
    path = ArtifactStore(settings).path(uid, sid) / "train.jsonl.gz"
    path.write_bytes(b"tampered")
    assert evaluate(client, sid).status_code == 409 and calls == []
