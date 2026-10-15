import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from role_ai.code_execution import CodeExecutor, ExecutionRequest, ExecutionUnavailable, OUTPUT_LIMIT
from role_ai.config import Settings
from role_ai.main import create_app

IMAGE = "python:3.12-slim@sha256:" + "a" * 64


def settings(**kw):
    return Settings(_env_file=None, code_execution_enabled=True, code_execution_image=IMAGE, **kw)


def test_execution_policy_and_no_host_fallback(monkeypatch):
    request = ExecutionRequest(code="pass", tests="pass", approved=True)
    for config in (Settings(_env_file=None), settings(code_execution_socket="tcp://remote"),
                   Settings(_env_file=None, code_execution_enabled=True,
                            code_execution_image="python:latest"),
                   settings(mode="public", cookie_secure=True)):
        executor = CodeExecutor(config)
        monkeypatch.setattr("role_ai.code_execution.shutil.which", lambda name: pytest.fail("must not run"))
        with pytest.raises(ExecutionUnavailable):
            executor.execute(request)
    monkeypatch.setattr("role_ai.code_execution.shutil.which", lambda name: None)
    with pytest.raises(ExecutionUnavailable, match="Docker"):
        CodeExecutor(settings()).execute(request)


@pytest.mark.parametrize("missing", ["MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit", "SecurityOptions"])
def test_execution_preflight_requires_limits(monkeypatch, missing):
    info = dict(OSType="linux", MemoryLimit=True, SwapLimit=True, CpuCfsQuota=True,
                PidsLimit=True, SecurityOptions=["name=seccomp,profile=builtin"])
    info[missing] = False if missing != "SecurityOptions" else ["name=seccomp,profile=unconfined"]
    executor = CodeExecutor(settings())
    monkeypatch.setattr(executor, "command", lambda *a, **k: json.dumps(info))
    with pytest.raises(ExecutionUnavailable):
        executor.preflight([], {})


@pytest.mark.parametrize("reason", [None, "timeout", "output_limit", "memory_limit", "capture_failure", "cleanup_failure"])
def test_execution_cleanup_and_boundaries(tmp_path, monkeypatch, reason):
    from pathlib import Path
    executor = CodeExecutor(settings())
    request = ExecutionRequest(code="SENSITIVE_CODE", tests="SENSITIVE_TESTS", approved=True)
    calls, workdirs = [], []
    monkeypatch.setattr("role_ai.code_execution.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(executor, "preflight", lambda *a: None)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")

    def command(base, env, *args, **kwargs):
        assert base[:2] == ["/usr/bin/docker", "--host"]
        assert "OPENAI_API_KEY" not in env
        calls.append(args)
        if args[0] == "create":
            for flag in ("--network=none", "--read-only", "--user=65534:65534", "--cap-drop=ALL",
                         "--security-opt=no-new-privileges:true", "--memory=128m", "--memory-swap=128m",
                         "--cpus=0.5", "--pids-limit=32", "--pull=never", "--log-driver=none"):
                assert flag in args
            mount = args[args.index("--mount") + 1]
            assert mount.endswith(",dst=/work,readonly")
            work = Path(mount.split("src=")[1].split(",")[0])
            workdirs.append(work)
            assert (work / "submission.py").read_text() == request.code
            assert {p.name for p in work.iterdir()} == {"submission.py", "test_submission.py", "runner.py"}
        if args[0] == "inspect":
            return json.dumps({"ExitCode": 137 if reason == "memory_limit" else 0,
                               "OOMKilled": reason == "memory_limit", "Running": False, "Error": ""})
        if args[0] == "rm" and reason == "cleanup_failure":
            raise ExecutionUnavailable("cleanup")
        return ""

    def capture(*args):
        if reason == "capture_failure":
            raise ExecutionUnavailable("capture")
        return reason if reason in {"timeout", "output_limit"} else None, "test output"

    monkeypatch.setattr(executor, "command", command)
    monkeypatch.setattr(executor, "capture", capture)
    if reason in {"capture_failure", "cleanup_failure"}:
        with pytest.raises(ExecutionUnavailable):
            executor.execute(request)
    else:
        result = executor.execute(request)
        assert result["executed"] is True
        assert result["status"] == (reason or "completed")
        assert result["output_truncated"] == (reason == "output_limit")
    assert calls[-1][0] == "rm"
    assert all(not p.exists() for p in workdirs)
    assert executor.blocked == (reason == "cleanup_failure")


@pytest.mark.parametrize("case", ["success", "timeout", "output"])
def test_execution_output_and_deadline(monkeypatch, case):
    # Trusted fixture process tests only the bounded pipe reader, not submitted code on the host.
    executor = CodeExecutor(settings(code_execution_timeout_seconds=1))
    real_popen = subprocess.Popen
    script = {"success": "print('fixture')", "timeout": "import time; time.sleep(5)",
              "output": "import sys; sys.stdout.write('x'*100000)"}[case]

    def fixture_process(*args, **kwargs):
        return real_popen([__import__('sys').executable, "-c", script],
                          stdin=kwargs["stdin"], stdout=kwargs["stdout"], stderr=kwargs["stderr"])
    monkeypatch.setattr("role_ai.code_execution.subprocess.Popen", fixture_process)
    reason, output = executor.capture(["unused"], {}, "fixture")
    assert reason == {"success": None, "timeout": "timeout", "output": "output_limit"}[case]
    assert len(output.encode()) <= OUTPUT_LIMIT
    if case == "success":
        assert output.strip() == "fixture"


def test_execution_api_approval_auth_and_busy(tmp_path, monkeypatch):
    config = settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", registration_enabled=True,
                      bootstrap_email="owner@example.test", bootstrap_password="safe-owner-password-123")
    app = create_app(config)
    request = {"code": "pass", "tests": "pass", "approved": True}
    executions = []
    def execute(self, body):
        executions.append(body)
        return {"status": "completed", "executed": True, "exit_code": 0, "output": "ok"}
    monkeypatch.setattr(CodeExecutor, "execute", execute)
    with TestClient(app) as client:
        assert client.post("/api/code-execution/run", json=request).status_code == 401
        auth = client.post("/api/auth/login", json={"email": "owner@example.test", "password": "safe-owner-password-123"}).json()
        assert client.post("/api/code-execution/run", json=request).status_code == 403
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert client.get("/api/status").json()["code_execution_available"] is True
        assert client.post("/api/code-execution/run", json={**request, "approved": False}).status_code == 400
        assert client.post("/api/code-execution/run", json={**request, "code": "x" * 16001}).status_code == 422
        assert client.post("/api/code-execution/run", json=request).json()["executed"] is True
        assert len(executions) == 1
        client.post("/api/auth/logout")
        auth = client.post("/api/auth/register", json={"email": "other@example.test", "password": "safe-other-password-123"}).json()
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert client.post("/api/code-execution/run", json=request).status_code == 403
        assert client.get("/api/status").json()["code_execution_available"] is False
    router = __import__('role_ai.code_execution', fromlist=['execution_router']).execution_router(lambda: None, config)
    endpoint = router.routes[0].endpoint
    # The endpoint closes over one executor: held lock rejects concurrent execution.
    executor = next(c.cell_contents for c in endpoint.__closure__ if isinstance(c.cell_contents, CodeExecutor))
    executor.lock.acquire()
    try:
        with pytest.raises(__import__('fastapi').HTTPException) as err:
            endpoint(ExecutionRequest(**request), (SimpleNamespace(is_owner=True), None))
        assert err.value.status_code == 409
    finally:
        executor.lock.release()
