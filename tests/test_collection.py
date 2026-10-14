import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import select

from role_ai.collection import run_one
from role_ai.huggingface import HuggingFaceClient
from role_ai.models import CollectionPlan, DatasetSnapshot, TrainingJob, User
from role_ai.providers import OllamaProvider


@pytest.fixture
def owner(tmp_path):
    from fastapi.testclient import TestClient
    from role_ai.config import Settings
    from role_ai.main import create_app
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'collection.db'}",
        storage_dir=str(tmp_path / "collected"), hf_download_enabled=True,
        registration_enabled=True, demo_enabled=True, default_provider="mock",
        allowed_origins=["http://testserver"],
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/auth/register", json={
            "email": "owner@example.test", "password": "safe-test-password-123"})
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        uid = response.json()["user"]["id"]
        with client.app.state.database_factory() as db:
            db.get(User, uid).is_owner = True
            db.commit()
        yield client


def prepare(client, bad_pick=False, cloud=False, license_id="mit", reject=False):
    settings = client.app.state.settings
    calls = []
    metadata = {"sha": "a" * 40, "cardData": {"license": license_id},
                "siblings": [{"rfilename": "train.jsonl"}]}
    def hf_handler(request):
        calls.append(("hf", request.url.path))
        if request.url.path == "/api/datasets":
            return httpx.Response(200, json=[{"id": "org/python"}])
        if "/resolve/" in request.url.path:
            records = [{"instruction": f"Question {i}", "answer": f"Answer {i}"} for i in range(35)]
            return httpx.Response(200, content="\n".join(json.dumps(r) for r in records) + "\n")
        return httpx.Response(200, json=metadata)
    def local_handler(request):
        calls.append(("local", request.url.path))
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"],
                                           "remote_host": "https://ollama.com" if cloud else ""})
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        if "데이터셋 ID" in system:
            decision = {"repo_id": "other/invented" if bad_pick else "org/python"}
        elif "JSONL 파일 하나" in system or "실제 문자열 필드" in system:
            decision = {"filename": "train.jsonl", "prompt_field": "instruction",
                        "completion_field": "answer"}
        else:
            decision = {"verdict": "reject" if reject else "pass",
                        "reason": "test local review", "risks": []}
        return httpx.Response(200, json={"message": {"content": json.dumps(decision)},
                                       "model": settings.ollama_model})
    client.app.state.providers.providers["ollama"] = OllamaProvider(
        settings, httpx.MockTransport(local_handler))
    return HuggingFaceClient(settings, httpx.MockTransport(hf_handler)), calls


def create(client, **extra):
    return client.post("/api/learning/collection", json={"topic": "python", "max_runs": 1, **extra})


def run(client, hf, now=None):
    return asyncio.run(run_one(client.app.state.settings, client.app.state.database_factory,
                               client.app.state.providers, hf, now))


def test_collection_create_pause_resume_delete(owner):
    response = create(owner, max_runs=3)
    assert response.status_code == 201
    pid = response.json()["id"]
    path = "/api/learning/collection/" + pid
    assert not owner.post(path + "/pause").json()["enabled"]
    assert owner.post(path + "/resume").json()["enabled"]
    assert owner.get("/api/learning/collection").json()[0]["topic"] == "python"
    assert owner.delete(path).status_code == 204
    assert owner.get("/api/learning/collection").json() == []


def test_collection_owner_network_and_csrf(signed_in, owner):
    owner.app.state.settings.hf_download_enabled = False
    assert create(owner).status_code == 403
    owner.app.state.settings.hf_download_enabled = True
    owner.headers.pop("X-CSRF-Token")
    assert create(owner).status_code == 403
    uid = owner.get("/api/auth/me").json()["user"]["id"]
    with owner.app.state.database_factory() as db:
        db.get(User, uid).is_owner = False
        db.commit()
    assert owner.get("/api/learning/collection").status_code == 403


@pytest.mark.parametrize("body", [
    {"topic": " "}, {"interval_hours": 0}, {"max_runs": 31},
])
def test_collection_input_limits(owner, body):
    assert create(owner, **body).status_code == 422


def test_collection_plan_quota_and_foreign_access(owner):
    ids = [create(owner).json()["id"] for _ in range(5)]
    assert create(owner).status_code == 409
    assert owner.post("/api/learning/collection/missing/pause").status_code == 404
    response = owner.post("/api/auth/register", json={
        "email": "second@example.test", "password": "safe-test-password-123"})
    owner.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    uid = response.json()["user"]["id"]
    with owner.app.state.database_factory() as db:
        db.get(User, uid).is_owner = True
        db.commit()
    assert owner.get("/api/learning/collection").json() == []
    assert owner.post("/api/learning/collection/" + ids[0] + "/pause").status_code == 404


def test_collection_autonomous_import_and_local_reviews(owner):
    hf, calls = prepare(owner)
    pid = create(owner).json()["id"]
    result = run(owner, hf)
    assert result["state"] == "collected" and result["training"] == "not_started"
    assert result["reviews"] == {"teacher": "pass", "reviewer": "pass", "orchestrator": "pass"}
    item = owner.get("/api/learning/datasets").json()[0]
    assert item["state"] == "reviewed" and item["approval_hash"] is None
    assert item["manifest"]["received_rows"] == 30
    assert item["manifest"]["source"]["revision"] == "a" * 40
    assert item["manifest"]["source"]["prompt_field"] == "instruction"
    assert all(r["provider"] == "ollama" for r in item["reviews"].values())
    assert owner.get("/api/learning/jobs").json() == []
    assert len(owner.get("/api/learning/datasets/" + item["id"] + "/samples").json()["train"]) > 0
    plan = owner.get("/api/learning/collection").json()[0]
    assert plan["id"] == pid and plan["state"] == "completed" and not plan["enabled"]
    assert owner.post("/api/learning/collection/" + pid + "/resume").status_code == 409
    assert run(owner, hf) is None
    assert all("/api/chat" in path or kind == "hf" or path == "/api/show" for kind, path in calls)


@pytest.mark.parametrize("failure", ["invented", "cloud", "license"])
def test_collection_rejects_untrusted_candidates_and_license(owner, failure):
    hf, calls = prepare(owner, bad_pick=failure == "invented",
                        cloud=failure == "cloud", license_id="unknown" if failure == "license" else "mit")
    create(owner)
    result = run(owner, hf)
    assert result["state"] == "failed"
    assert owner.get("/api/learning/datasets").json() == []
    assert owner.get("/api/learning/jobs").json() == []
    assert not any("/resolve/" in path for _, path in calls)


def test_collection_review_reject_no_training(owner):
    hf, _ = prepare(owner, reject=True)
    create(owner)
    result = run(owner, hf)
    assert result["reviews"] == {"teacher": "reject"}
    with owner.app.state.database_factory() as db:
        assert db.scalar(select(TrainingJob)) is None
        item = db.scalar(select(DatasetSnapshot))
        assert item.approval_hash is None


def test_collection_schedule_pause_and_dedup(owner):
    hf, _ = prepare(owner)
    pid = create(owner, max_runs=3).json()["id"]
    start = time.time() + 2
    assert run(owner, hf, start)["state"] == "collected"
    assert run(owner, hf, start + 1) is None
    assert run(owner, hf, start + 86401)["state"] == "skipped"
    assert len(owner.get("/api/learning/datasets").json()) == 1
    owner.post("/api/learning/collection/" + pid + "/pause")
    assert run(owner, hf, start + 172802) is None


def test_collection_interrupted_worker_recovery(owner):
    hf, _ = prepare(owner)
    pid = create(owner, max_runs=2).json()["id"]
    with owner.app.state.database_factory() as db:
        item = db.get(CollectionPlan, pid)
        item.state, item.runs = "running", 1
        db.commit()
    assert owner.delete("/api/learning/collection/" + pid).status_code == 409
    assert run(owner, hf) is None
    plan = owner.get("/api/learning/collection").json()[0]
    assert plan["state"] == "waiting" and plan["history"][0]["state"] == "failed"
