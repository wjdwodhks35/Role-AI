"""Real Git/checkpoints with scripted model and runner; Docker is verified separately in CI."""

import json
import os
import subprocess

import httpx
import pytest

from role_ai.coding_agent import (
    CodingAgent, CodingAction, LocalCodingModel, ModelBusy, parse_reply, runtime_identity,
)
from role_ai.coding_jobs import CodingJobError, CodingJobStore, JobBusy, TaskBudget, TaskCreate, checksum, decode_files
from role_ai.coding_project import cumulative_diff
from role_ai.config import Settings


@pytest.fixture
def coding(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "calc.py").write_text("answer = 0\n")
    (repo / "test_calc.py").write_text("import unittest\nfrom calc import answer\nclass TestAnswer(unittest.TestCase):\n def test_answer(self): self.assertEqual(answer, 42)\n")
    (repo / "qa.xlsx").write_bytes(b"PK\x00\xfffixture")
    env = {"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base")
    settings = Settings(_env_file=None, coding_enabled=True, coding_root=str(tmp_path / "jobs"),
                        coding_repositories={"fixture": str(repo)}, storage_dir=str(tmp_path / "artifacts"),
                        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
                        coding_image="python:3.12-slim@sha256:" + "a" * 64)
    # Preserve the explicit-test legacy protocol; new approval defaults have separate coverage.
    body = TaskCreate(repository_id="fixture", project_id="project", request="Fix answer to 42",
                      allowed_files=("calc.py",), approved=True, record_strategy=True,
                      budget=TaskBudget(token_budget=131072), model={"automatic_tests": False})
    store = CodingJobStore(settings.coding_root)
    state = store.create("owner", body, git("rev-parse", "HEAD"), checksum(str(repo)),
                         runtime_hash=runtime_identity(settings, "fixture"))
    return settings, store, state, repo, git


class ScriptModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.prompts = []

    def complete(self, state, messages):
        self.prompts.append(messages)
        return json.dumps(next(self.actions))


class FixtureRunner:
    def __init__(self):
        self.reconciled = []

    def run(self, state, name, directory):
        passed = decode_files(state["files"])["calc.py"] == b"answer = 42\n"
        return {"status": "passed" if passed else "failed", "reason": "tests", "exit_code": 0 if passed else 1,
                "count": 1, "output": "one test passed" if passed else "FAIL: test_answer AssertionError",
                "revision": state["revision"], "container": name}

    def reconcile(self, pending):
        self.reconciled.append(pending)


def patch(number):
    return {"action": "patch", "changes": [{"path": "calc.py", "content": f"answer = {number}\n"}]}


def run(coding, actions, **options):
    settings, _, state, *_ = coding
    model, runner = ScriptModel(actions), FixtureRunner()
    agent = CodingAgent(settings, model=model, runner=runner)
    result = agent.run("owner", state["id"], **options)
    return result, model, runner


def test_real_git_iterative_fix_test_repair_checkpoint_and_binary_preservation(coding):
    settings, store, initial, repo, git = coding
    (repo / "calc.py").write_text("owner uncommitted edit\n")
    (repo / "owner.txt").write_text("untouched")
    dirty = git("status", "--porcelain")
    actions = [{"action": "list"}, {"action": "read", "path": "calc.py"},
               {"action": "search", "query": "answer"}, {"action": "plan", "plan": ["Inspect, patch, test and fix"]},
               patch(41), {"action": "test"}, patch(42), {"action": "test"},
               {"action": "finish", "summary": "Fixed with passing evidence"}]
    result, model, _ = run(coding, actions)
    assert result["status"] == "completed", result.get("error")
    assert result["retries"] == 1 and result["calls"] == 9
    assert decode_files(result["files"])["calc.py"] == b"answer = 42\n"
    assert decode_files(result["files"])["qa.xlsx"] == b"PK\x00\xfffixture"
    assert result["report"]["files"] == ["calc.py"]
    assert result["report"]["scope"] == "candidate_only"
    assert "FAIL: test_answer" in model.prompts[6][1]["content"]
    assert git("status", "--porcelain") == dirty
    assert git("rev-parse", "HEAD") == initial["repo_revision"]
    directory, loaded = store.load("owner", initial["id"])
    assert loaded["report"] == result["report"]
    metadata = (directory / "strategy.jsonl").read_text()
    assert "answer =" not in metadata and "FAIL:" not in metadata
    assert "observed" in metadata and '"test_result":"passed"' in metadata
    assert list(directory.glob("work-*")) == []


def test_pause_restart_resume_keeps_code_counters_and_budget(coding):
    settings, store, initial, *_ = coding
    model = ScriptModel([{"action": "plan", "plan": ["Fix and test"]}, patch(42), {"action": "test"}, {"action": "finish"}])
    first = CodingAgent(settings, model=model, runner=FixtureRunner()).run("owner", initial["id"], max_actions=2)
    assert first["status"] == "paused" and first["calls"] == 2
    tokens = first["tokens"]
    store.resume("owner", initial["id"])
    second = CodingAgent(settings, model=model, runner=FixtureRunner()).run("owner", initial["id"])
    assert second["status"] == "completed" and second["calls"] == 4
    assert second["tokens"] > tokens and second["active_seconds"] >= first["active_seconds"]
    assert second["report"]["files"] == ["calc.py"]


def test_repeated_error_stops_even_when_model_offers_more_patches(coding):
    result, _, _ = run(coding, [{"action": "plan", "plan": ["Try"]}, patch(1), {"action": "test"},
                              patch(2), {"action": "test"}, patch(42)])
    assert result["status"] == "budget_exhausted" and result["calls"] == 5
    assert result["last_test"]["status"] == "failed"
    assert result["tested_revision"] is None


@pytest.mark.parametrize("case", ["calls", "tokens", "steps", "deadline"])
def test_budget_survives_resume_and_no_extra_call_occurs(coding, case):
    settings, store, state, *_ = coding
    directory, state = store.load("owner", state["id"])
    state["status"] = "paused"
    if case == "deadline":
        state["created_at"] = 0
    elif case == "steps":
        state["steps"] = state["spec"]["budget"]["max_steps"]
    elif case == "calls":
        state["calls"] = state["spec"]["budget"]["model_calls"]
    else:
        state["tokens"] = state["spec"]["budget"]["token_budget"]
    store.save(directory, state)
    store.resume("owner", state["id"])
    result, model, _ = run(coding, [])
    assert result["status"] == "budget_exhausted" and not model.prompts


def test_crash_unknown_step_requires_ack_and_reserves_resources(coding):
    settings, store, initial, *_ = coding
    class Interrupted:
        def complete(self, state, messages):
            raise httpx.ReadTimeout("PRIVATE")
    result = CodingAgent(settings, model=Interrupted(), runner=FixtureRunner()).run("owner", initial["id"])
    assert result["status"] == "interrupted" and result["pending"]["kind"] == "model"
    assert result["calls"] == 1 and result["tokens"] > 0 and result["active_seconds"] > 0
    with pytest.raises(CodingJobError):
        store.resume("owner", initial["id"])
    old_tokens = result["tokens"]
    store.resume("owner", initial["id"], acknowledge_unknown=True)
    runner = FixtureRunner()
    resumed = CodingAgent(settings, model=ScriptModel([{"action": "plan", "plan": ["Continue"]}]), runner=runner).run("owner", initial["id"], max_actions=1)
    assert resumed["tokens"] > old_tokens and runner.reconciled == [{"kind": "model"}]


def test_pause_cancel_controls_and_cross_worker_lock(coding):
    settings, store, initial, *_ = coding
    directory, _ = store.load("owner", initial["id"])
    with store.lock(directory):
        with pytest.raises(JobBusy):
            store.resume("owner", initial["id"])
        store.request_control("owner", initial["id"], "pause")
    paused, model, _ = run(coding, [])
    assert paused["status"] == "paused" and not model.prompts
    store.request_control("owner", initial["id"], "cancel")
    with pytest.raises(CodingJobError):
        store.resume("owner", initial["id"])
    # A queued cancelled task cannot execute even if a worker is started later.
    directory, state = store.load("owner", initial["id"])
    state["status"] = "queued"
    store.save(directory, state)
    cancelled, model, _ = run(coding, [])
    assert cancelled["status"] == "cancelled" and not model.prompts


def test_corrupt_checkpoint_owner_and_symlink_are_rejected(coding, tmp_path):
    _, store, initial, *_ = coding
    with pytest.raises(CodingJobError):
        store.load("other-owner", initial["id"])
    directory, state = store.load("owner", initial["id"])
    path = directory / "checkpoint.json"
    valid = path.read_bytes()
    path.write_text('{"format":1,"payload":{},"checksum":"bad"}')
    with pytest.raises(CodingJobError):
        store.load("owner", initial["id"])
    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(valid)
    path.symlink_to(outside)
    with pytest.raises(CodingJobError):
        store.load("owner", initial["id"])


def test_patch_scope_tests_and_runtime_configuration_cannot_be_bypassed(coding):
    _, store, state, *_ = coding
    for path in ("tests/test_secret.py", "test_calc.py", "test.py", "testcalc.py", "calc_test.py",
                 "pkg/tests/fixture.py", ".env", "../outside.py", "qa.xlsx", "role_ai/security.py"):
        with pytest.raises(ValueError):
            TaskCreate(repository_id="fixture", project_id="p", request="x", allowed_files=(path,))
    result, _, _ = run(coding, [{"action": "plan", "plan": ["Break scope"]},
                              {"action": "patch", "changes": [{"path": "test_calc.py", "content": "pass"}]}])
    assert result["status"] == "failed" and decode_files(result["files"])["calc.py"] == b"answer = 0\n"
    directory, changed = store.load("owner", state["id"])
    changed["status"] = "queued"
    changed["runtime_hash"] = "wrong"
    store.save(directory, changed)
    blocked, model, _ = run(coding, [])
    assert blocked["status"] == "failed" and not model.prompts


def test_no_false_finish_malformed_actions_and_stale_tests(coding):
    result, _, _ = run(coding, [{"action": "plan", "plan": ["Fix"]}, {"action": "finish"}])
    assert result["status"] == "failed" and "report" not in result
    # Model output cannot expand commands or approve its own scope.
    for raw in ({"action": "shell", "command": "rm -rf /"}, {"action": "test", "approved": True}):
        with pytest.raises(ValueError):
            CodingAction.model_validate_json(json.dumps(raw))


def test_busy_gpu_does_not_spend_call_reservation_and_cloud_endpoint_is_rejected(coding):
    settings, _, initial, *_ = coding
    class Busy:
        def complete(self, state, messages):
            raise ModelBusy("slot busy")
    result = CodingAgent(settings, model=Busy(), runner=FixtureRunner()).run("owner", initial["id"])
    assert result["status"] == "waiting" and result["calls"] == result["tokens"] == 0
    settings.ollama_url = "https://external.invalid"
    with pytest.raises(CodingJobError):
        LocalCodingModel(settings).complete(result, [])


def test_recorder_failure_does_not_change_coding_result(coding, monkeypatch):
    import role_ai.coding_agent as module
    def broken(*a, **kw):
        raise OSError("PRIVATE")
    monkeypatch.setattr(module, "ExecutionObservation", broken)
    result, _, _ = run(coding, [{"action": "plan", "plan": ["Fix"]}, patch(42), {"action": "test"}, {"action": "finish"}])
    assert result["status"] == "completed" and result["recorder_gap"]
    assert "PRIVATE" not in json.dumps(result.get("report"))
    assert cumulative_diff(result)["files"] == ["calc.py"]


def test_coding_api_uses_existing_auth_csrf_and_owner_boundaries(coding):
    from fastapi.testclient import TestClient
    from role_ai.main import create_app
    settings, _, _, *_ = coding
    settings.bootstrap_email = "owner@example.test"
    from pydantic import SecretStr
    settings.bootstrap_password = SecretStr("fixture-password")
    settings.registration_enabled = True
    settings.allowed_origins = ["http://testserver"]
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/coding/status").status_code == 401
        login = client.post("/api/auth/login", json={"email": "owner@example.test", "password": "fixture-password"}).json()
        payload = {"repository_id": "fixture", "project_id": "project", "request": "Fix", "allowed_files": ["calc.py"], "approved": True}
        assert client.post("/api/coding/tasks", json=payload).status_code == 403
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        assert client.get("/api/coding/status").json()["gpu_concurrency"] == 1
        assert client.post("/api/coding/tasks", json={**payload, "approved": False}).status_code == 400
        assert client.post("/api/coding/tasks", json={**payload, "repository_id": "unregistered"}).status_code == 409
        created = client.post("/api/coding/tasks", json=payload)
        assert created.status_code == 201, created.text
        jid = created.json()["id"]
        assert client.get("/api/coding/tasks/" + jid).status_code == 200
        client.post("/api/auth/register", json={"email": "other@example.test", "password": "fixture"})
        assert client.get("/api/coding/tasks/" + jid).status_code == 403


@pytest.mark.parametrize("case", ["passed", "zero", "timeout", "oom", "cleanup"])
def test_repository_runner_limits_evidence_and_cleanup_guard(coding, monkeypatch, case):
    from role_ai.code_execution import CodeExecutor, ExecutionUnavailable
    from role_ai.coding_runner import RepositoryTestRunner
    settings, _, state, *_ = coding
    from role_ai.coding_jobs import encode_files
    state["files"] = encode_files({"calc.py": b"answer = 42\n", "test_calc.py": b"pass\n"})
    state["revision"] = checksum(state["files"])
    calls = []
    monkeypatch.setattr("role_ai.coding_runner.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(CodeExecutor, "preflight", lambda *a: None)
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_NOT_FOR_DOCKER")
    def command(base, env, *args, **kwargs):
        assert "OPENAI_API_KEY" not in env
        calls.append(args)
        if args[0] == "create":
            for flag in ("--network=none", "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--pull=never",
                         "--label=role-ai.coding-runtime=true", "--memory=256m", "--cpus=0.5"):
                assert flag in args
            assert not any("--gpus" in arg or "docker.sock" in arg for arg in args)
            assert args[-1] == "unittest" and "/work/_roleai_runner.py" in args
        if args[0] == "inspect":
            return json.dumps({"Running": False, "Error": "", "ExitCode": 0, "OOMKilled": case == "oom"})
        if args[0] == "rm" and case == "cleanup":
            raise ExecutionUnavailable("PRIVATE")
        return ""
    monkeypatch.setattr(CodeExecutor, "command", staticmethod(command))
    monkeypatch.setattr(CodeExecutor, "capture", lambda *a: (
        "timeout" if case == "timeout" else None,
        '__ROLEAI_TEST_RESULT__=' + json.dumps({"count": 0 if case == "zero" else 1, "code": 0})))
    runner = RepositoryTestRunner(settings)
    if case == "cleanup":
        with pytest.raises(CodingJobError):
            runner.run(state, "role-ai-coding-" + "a" * 32, None)
        assert (runner.settings.coding_root and calls[-1][0] == "rm")
        with pytest.raises(CodingJobError, match="cleanup"):
            runner.run(state, "role-ai-coding-" + "b" * 32, None)
    else:
        result = runner.run(state, "role-ai-coding-" + "a" * 32, None)
        assert (result["status"] == "passed") == (case == "passed")
        assert calls[-1][0] == "rm"


def test_checkpoint_write_failure_preserves_previous_revision(coding, monkeypatch):
    _, store, initial, *_ = coding
    directory, state = store.load("owner", initial["id"])
    state["steps"] = 99
    def fail(*args):
        raise OSError("fixture disk failure")
    monkeypatch.setattr("role_ai.coding_jobs.os.replace", fail)
    with pytest.raises(OSError):
        store.save(directory, state)
    _, restored = store.load("owner", initial["id"])
    assert restored["steps"] == 0
    assert list(directory.glob(".checkpoint-*")) == []


def test_gpu_lock_serializes_calls_and_unknown_timeout_blocks_next_inference(coding, monkeypatch):
    import asyncio
    import threading
    from role_ai.gpu_resources import GpuBudgetError, preflight
    settings, _, state, *_ = coding
    first, release = threading.Event(), threading.Event()
    model = LocalCodingModel(settings)
    results = []
    async def call(*args):
        first.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return "{}"
    monkeypatch.setattr(model, "_call", call)
    thread = threading.Thread(target=lambda: results.append(model.complete(state, [])))
    thread.start()
    try:
        assert first.wait(5)
        with pytest.raises(ModelBusy):
            model.complete(state, [])
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and results == ["{}"]
    async def timeout(*args):
        raise httpx.ReadTimeout("private upstream timeout")
    monkeypatch.setattr(model, "_call", timeout)
    with pytest.raises(httpx.ReadTimeout):
        model.complete(state, [])
    with pytest.raises(GpuBudgetError):
        preflight(settings, "inference")


def test_exact_replacement_and_bounded_valid_model_context(coding):
    result, model, _ = run(coding, [{"action": "plan", "plan": ["Inspect and fix"]},
        {"action": "read", "path": "calc.py", "offset": 0},
        {"action": "patch", "changes": [{"path": "calc.py", "old_text": "answer = 0", "new_text": "answer = 42"}]},
        {"action": "test"}, {"action": "finish"}])
    assert result["status"] == "completed"
    assert decode_files(result["files"])["calc.py"] == b"answer = 42\n"
    for messages in model.prompts:
        json.loads(messages[1]["content"])
    for changes in ([{"path": "calc.py"}], [{"path": "calc.py", "old_text": "x", "new_text": "y", "content": "z"}]):
        with pytest.raises(ValueError):
            CodingAction.model_validate_json(json.dumps({"action": "patch", "changes": changes}))


def test_local_model_requests_action_schema_instead_of_unconstrained_json(coding, monkeypatch):
    import asyncio
    from role_ai.providers import OllamaProvider
    settings, _, state, *_ = coding
    calls = []
    def handle(request):
        data = json.loads(request.content)
        calls.append((request.url.path, data))
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        schema = data["format"]
        assert schema == CodingAction.model_json_schema()
        assert schema["additionalProperties"] is False
        assert "test" in schema["properties"]["action"]["enum"]
        assert data["options"]["temperature"] == 0
        assert "think" not in data  # model without the thinking capability
        return httpx.Response(200, json={"message": {"content": '{"action":"test"}'}})
    monkeypatch.setattr(OllamaProvider, "client", lambda self: httpx.AsyncClient(
        base_url="http://localhost/", transport=httpx.MockTransport(handle), trust_env=False))
    reply = asyncio.run(LocalCodingModel(settings)._call(state, []))
    assert parse_reply(reply)[0].action == "test"
    # Missing usage metadata is reported as unknown, never as zero.
    assert reply["prompt_tokens"] is None and reply["output_tokens"] is None
    assert [p for p, _ in calls] == ["/api/show", "/api/chat"]
