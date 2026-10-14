import asyncio
import json

import pytest

from role_ai.artifacts import ArtifactStore, canonical
from role_ai.models import DatasetSnapshot, TrainingJob
from role_ai.providers import ModelReply, ProviderUnavailable
from role_ai.quality import benchmark_result, full_review
from role_ai.training import model_fingerprint
from test_learning import (
    approval, learning_app as _shared_learning_app, payload, queued, reviewed, staged,
)

learning_app = _shared_learning_app


def test_full_review_covers_train_and_eval(learning_app):
    client, _, calls = learning_app
    record = staged(client)
    response = client.post("/api/learning/datasets/" + record["id"] + "/review",
                           json={"stage": "teacher"})
    assert response.status_code == 200
    result = response.json()["reviews"]["teacher"]
    assert result["reviewed_rows"] == 12 and result["pass_rate"] == 1
    assert sum(r["split"] == "eval" for r in result["sample_results"]) == 2
    assert calls == []


@pytest.mark.parametrize("failure", ["reject", "risk", "broken", "simulated", "model_change"])
def test_full_review_rejects_or_fails_closed(failure):
    class Reviewer:
        index = 0
        async def complete(self, messages):
            self.index += 1
            return ModelReply(
                "invalid" if failure == "broken" else json.dumps({
                    "verdict": "reject" if failure == "reject" and self.index == 2 else "pass",
                    "reason": "fixture", "risks": ["privacy"] if failure == "risk" and self.index == 2 else [],
                }), "ollama", str(self.index) if failure == "model_change" else "fixture",
                simulated=failure == "simulated")
    reviewer = Reviewer()
    args = (reviewer, "teacher", payload()["samples"][:2], payload()["samples"][2:3],
            {"source": {}}, "a" * 64)
    if failure in {"broken", "simulated", "model_change"}:
        with pytest.raises(ValueError):
            asyncio.run(full_review(*args))
    else:
        result = asyncio.run(full_review(*args))
        assert result["verdict"] == "reject" and result["reviewed_rows"] == 3


def test_review_error_does_not_store_partial_pass(learning_app):
    client, _, _ = learning_app
    class Reviewer:
        index = 0
        async def complete(self, messages):
            self.index += 1
            if self.index == 4:
                raise ProviderUnavailable("offline")
            return ModelReply('{"verdict":"pass","reason":"fixture","risks":[]}', "ollama", "fixture")
    client.app.state.providers.providers["ollama"] = Reviewer()
    record = staged(client)
    assert client.post("/api/learning/datasets/" + record["id"] + "/review",
                       json={"stage": "teacher"}).status_code == 503
    assert client.get("/api/learning/datasets").json()[0]["reviews"] == {}


def test_old_preview_cannot_approve(learning_app):
    client, _, _ = learning_app
    record = reviewed(client, staged(client))
    with client.app.state.database_factory() as db:
        item = db.get(DatasetSnapshot, record["id"])
        data = json.loads(item.reviews)
        data["teacher"]["scope"] = "three training samples"
        item.reviews = canonical(data)
        db.commit()
    assert approval(client, record).status_code == 409


def suite(client, cases=None):
    response = client.post("/api/learning/benchmarks", json={
        "name": "fixed", "cases": cases or [
            {"prompt": "Unique evaluation A", "expected": "alpha"},
            {"prompt": "Unique evaluation B", "expected": "beta"},
        ]})
    assert response.status_code == 201, response.text
    return response.json()


def test_benchmark_registration_and_immutable_plan(learning_app):
    client, _, _ = learning_app
    fixed = suite(client)
    record = reviewed(client, staged(client))
    result = approval(client, record, benchmark_id=fixed["id"])
    assert result.status_code == 200
    assert result.json()["plan"]["benchmark"]["digest"] == fixed["digest"]
    assert client.get("/api/learning/benchmarks").json()[0]["cases"] == fixed["cases"]


@pytest.mark.parametrize("cases", [
    [{"prompt": " ", "expected": "x"}, {"prompt": "b", "expected": "y"}],
    [{"prompt": "A", "expected": "x"}, {"prompt": " a ", "expected": "y"}],
    [{"prompt": "a", "expected": "x"}],
])
def test_benchmark_limits_and_duplicates(learning_app, cases):
    client, _, _ = learning_app
    assert client.post("/api/learning/benchmarks",
                       json={"name": "invalid", "cases": cases}).status_code == 422


def test_benchmark_leakage_and_unknown_suite(learning_app):
    client, _, _ = learning_app
    record = reviewed(client, staged(client))
    fixed = suite(client, [{"prompt": "Question number 0", "expected": "anything"},
                           {"prompt": "independent", "expected": "anything"}])
    assert approval(client, record, benchmark_id=fixed["id"]).status_code == 422
    assert approval(client, record, benchmark_id="missing").status_code == 404


def test_benchmark_exact_match_and_response_validation():
    cases = [{"prompt": "a", "expected": "Ａ B"}, {"prompt": "b", "expected": "beta"}]
    result = benchmark_result(cases, ["a   b", "wrong"])
    assert result["score"] == 0.5 and result["cases"][0]["pass"]
    with pytest.raises(ValueError):
        benchmark_result(cases, ["one"])


def test_dashboard_no_worker_or_percent_claim(learning_app):
    client, _, _ = learning_app
    _, job = queued(client)
    result = client.get("/api/learning/dashboard").json()
    assert result["counts"]["queued"] == 1 and result["entries"][0]["id"] == job["id"]
    assert result["worker_connectivity"] == "not_measured" and result["progress_percent"] is None


def candidate(client, settings):
    record, job = queued(client)
    uid = client.get("/api/auth/me").json()["user"]["id"]
    adapter = ArtifactStore(settings).path(uid, record["id"]) / ("run-" + job["id"]) / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "model.safetensors").write_bytes(b"synthetic-adapter-never-real-GPU")
    report = {"execution": "gpu_sft_qlora", "activation": "not_activated",
              "adapter_fingerprint": model_fingerprint(adapter),
              "data_hash": record["data_hash"], "baseline_eval_loss": 2.0, "trained_eval_loss": 1.0,
              "benchmark": {"baseline": {"score": 0.5}, "trained": {"score": 1.0}}}
    with client.app.state.database_factory() as db:
        row = db.get(TrainingJob, job["id"])
        row.state, row.report = "evaluated", canonical(report)
        db.get(DatasetSnapshot, record["id"]).state = "evaluated"
        db.commit()
    return job["id"], adapter


def choose(client, jid, revision):
    return client.post("/api/learning/versions/select", json={"job_id": jid, "revision": revision})


def test_candidate_selection_rollback_and_cas(learning_app):
    client, settings, _ = learning_app
    first, _ = candidate(client, settings)
    second, _ = candidate(client, settings)
    selected = choose(client, first, 0)
    assert selected.status_code == 200 and not selected.json()["serving_changed"]
    assert choose(client, second, 0).status_code == 409
    assert choose(client, second, 1).status_code == 200
    rollback = client.post("/api/learning/versions/rollback", json={"revision": 2})
    assert rollback.status_code == 200 and rollback.json()["job_id"] == first
    assert rollback.json()["revision"] == 3


@pytest.mark.parametrize("failure", ["tamper", "regression", "missing_benchmark", "fake"])
def test_candidate_validation_blocks_bad_versions(learning_app, failure):
    client, settings, _ = learning_app
    jid, adapter = candidate(client, settings)
    with client.app.state.database_factory() as db:
        row = db.get(TrainingJob, jid)
        report = json.loads(row.report)
        if failure == "tamper":
            (adapter / "model.safetensors").write_bytes(b"changed")
        elif failure == "regression":
            report["trained_eval_loss"] = 3.0
        elif failure == "missing_benchmark":
            report.pop("benchmark")
        else:
            report["execution"] = "test_fake_trainer"
        row.report = canonical(report)
        db.commit()
    assert choose(client, jid, 0).status_code == 409
    assert client.get("/api/learning/versions").json()["selection"]["job_id"] is None


def test_rollback_revalidates_previous_files(learning_app):
    client, settings, _ = learning_app
    first, adapter = candidate(client, settings)
    second, _ = candidate(client, settings)
    assert choose(client, first, 0).status_code == 200
    assert choose(client, second, 1).status_code == 200
    (adapter / "model.safetensors").write_bytes(b"tampered")
    assert client.post("/api/learning/versions/rollback", json={"revision": 2}).status_code == 409
    assert client.get("/api/learning/versions").json()["selection"]["job_id"] == second


def test_lifecycle_owner_and_csrf(learning_app):
    client, _, _ = learning_app
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/learning/benchmarks",
                       json={"name": "x", "cases": []}).status_code == 403
    data = client.post("/api/auth/register", json={
        "email": "other@example.test", "password": "safe-guest-password-123"}).json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    for path in ["benchmarks", "versions", "dashboard"]:
        assert client.get("/api/learning/" + path).status_code == 403
