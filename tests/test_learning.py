import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from role_ai.artifacts import ArtifactStore, normalize
from role_ai.config import Settings
from role_ai.main import create_app
from role_ai.models import TrainingJob
from role_ai.providers import ModelReply
from role_ai.training import ResourcesBusy, run_one


@pytest.fixture
def learning_app(tmp_path):
    model = tmp_path / "base"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"fixture-only-not-real-weights")
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'learning.db'}",
        storage_dir=str(tmp_path / "artifacts"), training_base_model_path=str(model),
        training_base_model_revision="a" * 40, training_enabled=True,
        registration_enabled=True, demo_enabled=True, default_provider="mock",
        bootstrap_email="owner@example.test", bootstrap_password="safe-owner-password-123",
        openai_enabled=True, openai_api_key="fake-learning-secret", openai_model="gpt-test",
        openai_input_usd_per_million=Decimal("1"), openai_output_usd_per_million=Decimal("2"),
        openai_daily_budget_usd=Decimal("1"), openai_request_budget_usd=Decimal("0.25"),
    )
    calls = []
    decision = {"verdict": "pass", "reason": "fixture review only", "risks": []}
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={
            "model": "gpt-test", "status": "completed",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": json.dumps(decision)}]}],
        })
    app = create_app(settings, openai_transport=httpx.MockTransport(response))
    class LocalReviewer:
        async def complete(self, messages):
            return ModelReply(json.dumps(decision), "ollama", "fixture-only")
    app.state.providers.providers["ollama"] = LocalReviewer()
    with TestClient(app) as client:
        data = client.post("/api/auth/login", json={
            "email": "owner@example.test", "password": "safe-owner-password-123"}).json()
        client.headers["X-CSRF-Token"] = data["csrf_token"]
        yield client, settings, calls


def payload(license_id="mit"):
    return {
        "name": "small selected dataset", "license": license_id,
        "provenance": "synthetic test data",
        "samples": [{"prompt": f"Question number {i}", "completion": f"Answer number {i}"}
                    for i in range(12)],
    }


def staged(client, license_id="mit"):
    response = client.post("/api/learning/datasets", json=payload(license_id))
    assert response.status_code == 201, response.text
    return response.json()


def reviewed(client, record):
    for stage in ["teacher", "reviewer", "orchestrator"]:
        response = client.post("/api/learning/datasets/" + record["id"] + "/review",
                               json={"stage": stage, "allow_paid": stage == "orchestrator"})
        assert response.status_code == 200, response.text
    return response.json()


def approval(client, record, **kwargs):
    return client.post("/api/learning/datasets/" + record["id"] + "/approve", json={
        "data_hash": record["data_hash"], "license_reviewed": True,
        "privacy_reviewed": True, "model_license_reviewed": True, **kwargs,
    })


def queued(client):
    record = reviewed(client, staged(client))
    assert approval(client, record).status_code == 200
    response = client.post("/api/learning/datasets/" + record["id"] + "/enqueue")
    assert response.status_code == 201
    return record, response.json()


def test_documents_compression_split_and_purge(learning_app):
    client, settings, calls = learning_app
    record = staged(client)
    manifest = record["manifest"]
    assert manifest["train_rows"] == 10 and manifest["eval_rows"] == 2
    uid = client.get("/api/auth/me").json()["user"]["id"]
    store = ArtifactStore(settings)
    train, evaluation, _ = store.read(uid, record["id"], record["data_hash"])
    assert not ({r["prompt"] for r in train} & {r["prompt"] for r in evaluation})
    assert (store.path(uid, record["id"]) / "train.jsonl.gz").exists()
    assert (store.path(uid, record["id"]) / "dataset-card.md").exists()
    assert "synthetic test data" in client.get(
        "/api/learning/datasets/" + record["id"] + "/card").text
    assert calls == []
    assert client.delete("/api/learning/datasets/" + record["id"]).status_code == 204
    assert not store.path(uid, record["id"]).exists()


def test_normalization_dedup_pii_and_conflicts():
    rows = payload()["samples"]
    rows += [rows[0], {"prompt": "contact a@b.example", "completion": "unsafe"}]
    train, evaluation, removed = normalize(rows)
    assert removed == 2 and len(train) + len(evaluation) == 12
    with pytest.raises(ValueError):
        normalize(rows + [{"prompt": rows[0]["prompt"], "completion": "conflicting"}])


def test_quota_and_path_validation(learning_app):
    client, settings, _ = learning_app
    settings.artifact_quota_bytes = 1
    assert client.post("/api/learning/datasets", json=payload()).status_code == 422
    assert not list(ArtifactStore(settings).root.glob(".tmp-*"))
    with pytest.raises(ValueError):
        ArtifactStore(settings).path("../escape", "bad-id")


def test_reviews_and_user_approval_are_both_required(learning_app):
    client, _, calls = learning_app
    record = staged(client)
    assert approval(client, record).status_code == 409
    assert client.post("/api/learning/datasets/" + record["id"] + "/enqueue").status_code == 409
    assert client.post("/api/learning/datasets/" + record["id"] + "/review",
                       json={"stage": "orchestrator"}).status_code == 503
    assert calls == []
    quote = client.post("/api/learning/datasets/" + record["id"] + "/review/quote")
    assert quote.status_code == 200 and calls == []
    reviewed(client, record)
    assert approval(client, record, privacy_reviewed=False).status_code == 422
    result = approval(client, record).json()
    assert result["state"] == "approved" and result["plan"]["method"] == "sft_qlora"
    assert result["plan"]["target_skill"] == "developer"
    assert result["approval_hash"]


def test_unknown_license_blocks_approval(learning_app):
    client, _, _ = learning_app
    record = reviewed(client, staged(client, "unknown"))
    assert approval(client, record).status_code == 422


def test_new_review_invalidates_previous_approval(learning_app):
    client, _, _ = learning_app
    record = reviewed(client, staged(client))
    assert approval(client, record).status_code == 200
    assert client.post("/api/learning/datasets/" + record["id"] + "/review",
                       json={"stage": "teacher"}).json()["approval_hash"] is None
    assert client.post("/api/learning/datasets/" + record["id"] + "/enqueue").status_code == 409


def test_enqueue_once_and_cancel_consumes_approval(learning_app):
    client, _, _ = learning_app
    record, job = queued(client)
    path = "/api/learning/datasets/" + record["id"]
    assert client.post(path + "/enqueue").status_code == 409
    assert client.delete(path).status_code == 409
    assert client.post("/api/learning/jobs/" + job["id"] + "/cancel").status_code == 200
    assert client.post(path + "/enqueue").status_code == 409
    assert client.delete(path).status_code == 204


def test_training_disabled_blocks_enqueue_and_worker(learning_app):
    client, settings, _ = learning_app
    record = reviewed(client, staged(client))
    assert approval(client, record).status_code == 200
    settings.training_enabled = False
    assert client.post("/api/learning/datasets/" + record["id"] + "/enqueue").status_code == 403
    with pytest.raises(ValueError):
        run_one(settings)


def test_worker_contract_does_not_activate_model(learning_app):
    client, settings, _ = learning_app
    _, job = queued(client)
    invoked = []
    def simulated(plan, train, evaluation, output):
        invoked.append(plan)
        assert len(train) == 10 and len(evaluation) == 2
        return {"execution": "test_fake_trainer", "activation": "not_activated"}
    result = run_one(settings, trainer=simulated)
    assert result["job_id"] == job["id"] and result["state"] == "evaluated"
    assert invoked and result["report"]["execution"] == "test_fake_trainer"
    assert client.get("/api/learning/jobs").json()[0]["state"] == "evaluated"
    assert run_one(settings, trainer=simulated) is None


@pytest.mark.parametrize("tamper", ["dataset", "model"])
def test_tampered_artifact_blocks_training(learning_app, tamper):
    client, settings, _ = learning_app
    record, _ = queued(client)
    if tamper == "model":
        from pathlib import Path
        (Path(settings.training_base_model_path) / "model.safetensors").write_bytes(b"changed")
    else:
        uid = client.get("/api/auth/me").json()["user"]["id"]
        path = ArtifactStore(settings).path(uid, record["id"]) / "manifest.json"
        data = json.loads(path.read_text())
        data["excluded_rows"] += 1
        path.write_text(json.dumps(data))
    def forbidden(*args):
        pytest.fail("No trainer call is allowed after tampering")
    assert run_one(settings, trainer=forbidden)["state"] == "failed"


def test_resource_block_retry_and_failure(learning_app):
    client, settings, _ = learning_app
    _, job = queued(client)
    def blocked(*args):
        raise ResourcesBusy("GPU occupied")
    assert run_one(settings, trainer=blocked)["state"] == "blocked"
    assert client.post("/api/learning/jobs/" + job["id"] + "/retry").status_code == 200
    def broken(*args):
        raise RuntimeError("private-file-secret")
    result = run_one(settings, trainer=broken)
    assert result["state"] == "failed"
    assert "private-file-secret" not in json.dumps(result)


def test_nonowner_cannot_manage_learning(learning_app):
    client, _, _ = learning_app
    record = staged(client)
    signed = client.post("/api/auth/register", json={
        "email": "guest@example.test", "password": "safe-guest-password-123"}).json()
    client.headers["X-CSRF-Token"] = signed["csrf_token"]
    assert client.get("/api/learning/datasets").status_code == 403
    assert client.delete("/api/learning/datasets/" + record["id"]).status_code == 403


def test_recover_interrupted_job_requires_new_approval(learning_app):
    client, _, _ = learning_app
    _, job = queued(client)
    with client.app.state.database_factory() as db:
        record = db.get(TrainingJob, job["id"])
        record.state = "running"
        db.commit()
    result = client.post("/api/learning/jobs/" + job["id"] + "/recover")
    assert result.status_code == 200 and result.json()["state"] == "failed"
    assert client.get("/api/learning/datasets").json()[0]["approval_hash"] is None
