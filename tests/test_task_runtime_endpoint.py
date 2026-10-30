"""Private socket selection and live invalidation; no Docker/model/network calls."""

import json
import os
from pathlib import Path
import socket
import tempfile

import pytest

from role_ai.config import Settings
from role_ai.code_execution import CodeExecutor
from role_ai.task_runtime_endpoint import (
    socket_identity,
    bind_endpoint,
    endpoint_binding,
    verify_runtime,
)
from role_ai.task_approvals import ApprovalService, TaskApprovalGate, ApprovalDenied

pytest_plugins = ("test_task_approvals",)


@pytest.fixture
def endpoint():
    # Linux Unix socket names are bounded; keep synthetic fixture paths short.
    with tempfile.TemporaryDirectory(prefix="rai-endpoint-", dir="/tmp") as name:
        root = Path(name)
        path = root / "docker.sock"
        sockets = []

        def create():
            peer = socket.socket(socket.AF_UNIX)
            peer.bind(str(path))
            path.chmod(0o660)
            sockets.append(peer)

        create()
        yield root, path, create
        for peer in sockets:
            peer.close()


def test_private_socket_binding_is_explicit_and_never_changes_process_context(
    endpoint, monkeypatch
):
    root, path, _ = endpoint
    monkeypatch.setenv("DOCKER_HOST", "tcp://must-not-be-used.invalid:2375")
    settings = Settings(_env_file=None)
    assert endpoint_binding(settings) is None
    bind_endpoint(settings, str(path))
    assert settings.code_execution_socket == str(path)
    assert endpoint_binding(settings)["uid"] == os.getuid()
    assert os.environ["DOCKER_HOST"] == "tcp://must-not-be-used.invalid:2375"
    assert path.parent == root


@pytest.mark.parametrize(
    "value",
    [
        "relative.sock",
        "unix:///tmp/example.sock",
        "tcp://127.0.0.1:2375",
        "ssh://host/docker.sock",
        "/tmp/a/../docker.sock",
        "/tmp//docker.sock",
        "/tmp/a\n.sock",
        "/tmp/" + "x" * 110,
    ],
)
def test_noncanonical_remote_and_oversized_inputs_are_rejected(value):
    with pytest.raises(ValueError):
        socket_identity(value)


@pytest.mark.parametrize(
    "kind",
    ["missing", "regular", "socket_link", "parent_link", "public_parent", "world_socket", "owner"],
)
def test_bad_socket_type_links_permissions_and_ownership_fail_closed(endpoint, monkeypatch, kind):
    root, path, _ = endpoint
    if kind == "missing":
        path = root / "missing.sock"
    elif kind == "regular":
        path.unlink()
        path.write_text("not a socket")
    elif kind == "socket_link":
        link = root / "alias.sock"
        link.symlink_to(path)
        path = link
    elif kind == "parent_link":
        link = root / "alias"
        link.symlink_to(root, target_is_directory=True)
        path = link / "docker.sock"
    elif kind == "public_parent":
        root.chmod(0o755)
    elif kind == "world_socket":
        path.chmod(0o666)
    else:
        monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(ValueError):
        socket_identity(str(path))


def test_replaced_socket_invalidates_existing_task_approval(endpoint, coding_access):
    _, path, create = endpoint
    db, settings, body, ref, _ = coding_access
    bind_endpoint(settings, str(path))
    body = body.model_copy(update={"task_id": "new-endpoint", "approval_reference": "new-endpoint"})
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    TaskApprovalGate(db, settings).require("a" * 64, body, "coding.read", ref)
    path.unlink()
    create()
    with pytest.raises(ApprovalDenied, match="runtime_endpoint_changed"):
        TaskApprovalGate(db, settings).require("a" * 64, body, "coding.read", ref)


def runtime_settings(path, root):
    settings = Settings(
        _env_file=None,
        mode="local",
        code_execution_enabled=True,
        code_execution_image="python:3.12-slim@sha256:" + "1" * 64,
        storage_dir=str(root / "artifacts"),
    )
    bind_endpoint(settings, str(path))
    return settings


@pytest.mark.parametrize(
    "missing",
    [
        None,
        "rootless",
        "v2",
        "systemd",
        "memory",
        "swap",
        "cpu",
        "pids",
        "seccomp",
        "not_object",
        "invalid_json",
    ],
)
def test_explicit_endpoint_requires_existing_resource_preflight(endpoint, monkeypatch, missing):
    root, path, _ = endpoint
    settings = runtime_settings(path, root)
    info = {
        "OSType": "linux",
        "CgroupVersion": "2",
        "CgroupDriver": "systemd",
        "MemoryLimit": True,
        "SwapLimit": True,
        "CpuCfsQuota": True,
        "PidsLimit": True,
        "SecurityOptions": ["name=rootless", "name=seccomp,profile=builtin"],
    }
    fields = {
        "memory": "MemoryLimit",
        "swap": "SwapLimit",
        "cpu": "CpuCfsQuota",
        "pids": "PidsLimit",
    }
    if missing in fields:
        info[fields[missing]] = False
    elif missing == "rootless":
        info["SecurityOptions"].remove("name=rootless")
    elif missing == "seccomp":
        info["SecurityOptions"].remove("name=seccomp,profile=builtin")
    elif missing == "v2":
        info["CgroupVersion"] = "1"
    elif missing == "systemd":
        info["CgroupDriver"] = "none"
    calls = []

    def command(base, env, *args, **kwargs):
        calls.append((base, env, args))
        assert base[-2:] == ["--host", "unix://" + str(path)]
        assert "DOCKER_HOST" not in env
        if args[0] == "info":
            if missing == "not_object":
                return "[]"
            if missing == "invalid_json":
                return "{"
            return json.dumps(info)
        assert args[:2] == ("image", "inspect")
        return json.dumps({"Os": "linux", "Config": {}})

    monkeypatch.setattr("shutil.which", lambda name: "/trusted/docker")
    monkeypatch.setattr(CodeExecutor, "command", staticmethod(command))
    if missing:
        with pytest.raises(ValueError):
            verify_runtime(settings, root)
    else:
        verify_runtime(settings, root)
        assert calls and calls[-1][2][0] == "image"


def test_delegation_cli_forwards_socket_before_any_model_call(endpoint, monkeypatch, capsys):
    from role_ai import task_delegation_lab as lab

    root, path, _ = endpoint
    real_environment = lab.synthetic_environment
    called = []

    def environment(*args, **kwargs):
        assert kwargs["socket"] == str(path)
        result = real_environment(*args, **kwargs)
        called.append(result[2])
        return result

    def deny(settings, folder):
        assert endpoint_binding(settings) is not None
        raise ValueError("synthetic missing image")

    monkeypatch.setattr(lab, "synthetic_environment", environment)
    monkeypatch.setattr(lab, "verify_runtime", deny)
    monkeypatch.setattr(lab.tempfile, "mkdtemp", lambda **kwargs: str(root / "lab"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "lab",
            "--model",
            "synthetic",
            "--model-digest",
            "1" * 64,
            "--image",
            "python:3.12-slim@sha256:" + "1" * 64,
            "--task",
            "budget-check",
            "--socket",
            str(path),
        ],
    )
    assert lab.main() == 2
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    assert called[0].code_execution_socket == str(path)


def test_baseline_cli_forwards_same_explicit_socket(endpoint, monkeypatch, capsys):
    from role_ai import task_baseline as baseline

    root, path, _ = endpoint

    def grade(public, task_id, **kwargs):
        assert public == str(root / "public") and task_id == "budget-check"
        assert kwargs["socket"] == str(path)
        return {"metrics": {"success": False}}

    monkeypatch.setattr(baseline, "grade_baseline", grade)
    monkeypatch.setattr(
        "sys.argv",
        [
            "baseline",
            "--public-root",
            str(root / "public"),
            "--task",
            "budget-check",
            "--socket",
            str(path),
        ],
    )
    assert baseline.main() == 2
    assert json.loads(capsys.readouterr().out)["metrics"]["success"] is False
