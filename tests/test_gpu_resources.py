import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from role_ai.assistant import AssistantContext, OpenClawGatewayAdapter
from role_ai.config import Settings
from role_ai.gpu_resources import (
    GpuBudgetError, GpuPolicy, admission, configure_allocator, gpu_snapshot,
    policy_path, preflight, read_policy, write_policy,
)
from role_ai.main import create_app
from role_ai.providers import ProviderUnavailable
from role_ai.training import ResourcesBusy, run_one, run_sft


@pytest.fixture
def gpu_app(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'gpu.db'}",
        storage_dir=str(tmp_path / "artifacts"),
        bootstrap_email="owner@example.test", bootstrap_password="safe-owner-password-123",
        registration_enabled=True, demo_enabled=True, default_provider="mock",
        allowed_origins=["http://testserver"],
    )
    state = {"used": 1024, "unavailable": False}
    def snapshot():
        if state["unavailable"]:
            raise GpuBudgetError("fixture unavailable")
        return {"gpu_index": 0, "total_mib": 8192,
                "used_mib": state["used"], "free_mib": 8192 - state["used"]}
    monkeypatch.setattr("role_ai.gpu_resources.gpu_snapshot", snapshot)
    with TestClient(create_app(settings)) as client:
        data = client.post("/api/auth/login", json={
            "email": "owner@example.test", "password": "safe-owner-password-123"}).json()
        client.headers["X-CSRF-Token"] = data["csrf_token"]
        yield client, settings, state


def save(client, **extra):
    return client.put("/api/settings/gpu-memory", json={"enabled": True, **extra})


def test_gpu_settings_persistence_and_measurement(gpu_app):
    client, settings, _ = gpu_app
    initial = client.get("/api/settings/gpu-memory").json()
    assert not initial["policy"]["enabled"] and not initial["system_wide_hard_limit"]
    assert save(client, limit_mib=4096).status_code == 200
    persisted = read_policy(Settings(_env_file=None, storage_dir=settings.storage_dir))
    assert persisted.enabled and persisted.limit_mib == 4096
    status = client.get("/api/settings/gpu-memory").json()
    assert status["measurement"]["total_mib"] == 8192
    assert status["measurement"]["effective_limit_mib"] == 4096
    assert status["applies_to"] == "new_tasks_only"


@pytest.mark.parametrize("extra", [
    {"limit_mib": 511}, {"reserve_mib": -1}, {"training_mib": 0},
    {"limit_mib": True}, {"limit_mib": 4096.5},
])
def test_gpu_settings_validation(gpu_app, extra):
    client, settings, _ = gpu_app
    assert save(client, **extra).status_code == 422
    assert not policy_path(settings).exists()


def test_gpu_settings_owner_and_csrf(gpu_app):
    client, _, _ = gpu_app
    client.headers.pop("X-CSRF-Token")
    assert save(client).status_code == 403
    data = client.post("/api/auth/register", json={
        "email": "other@example.test", "password": "safe-test-password-123"}).json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    assert client.get("/api/settings/gpu-memory").status_code == 403
    assert save(client).status_code == 403


def test_gpu_admission_exact_boundary_and_physical_clamp():
    policy = GpuPolicy(enabled=True, limit_mib=6144, inference_mib=4096)
    snapshot = {"total_mib": 8192, "used_mib": 2048}
    assert admission(policy, snapshot, "inference")["allocator_limit_mib"] == 4096
    with pytest.raises(GpuBudgetError):
        admission(policy, {**snapshot, "used_mib": 2049}, "inference")
    wide = GpuPolicy(enabled=True, limit_mib=12288, reserve_mib=1024, inference_mib=5120)
    assert admission(wide, snapshot, "inference")["limit_mib"] == 7168
    with pytest.raises(GpuBudgetError):
        admission(GpuPolicy(enabled=True, reserve_mib=8192), snapshot, "inference")
    with pytest.raises(GpuBudgetError):
        admission(policy, snapshot, "unknown")


def test_gpu_unavailable_disabled_and_corrupt_repair(gpu_app, monkeypatch):
    client, settings, state = gpu_app
    state["unavailable"] = True
    assert preflight(settings, "inference") == {"enabled": False}
    assert save(client).status_code == 200
    assert client.get("/api/settings/gpu-memory").json()["measurement"]["state"] == "unavailable"
    with pytest.raises(GpuBudgetError):
        preflight(settings, "inference")
    policy_path(settings).write_text("broken", encoding="utf-8")
    assert client.get("/api/settings/gpu-memory").json()["policy_state"] == "invalid"
    with pytest.raises(GpuBudgetError):
        read_policy(settings)
    assert save(client, enabled=False).status_code == 200
    assert not read_policy(settings).enabled


def test_gpu_nvidia_smi_parsing_and_timeout(monkeypatch):
    monkeypatch.setattr("role_ai.gpu_resources.shutil.which", lambda _: "/fake/nvidia-smi")
    commands = []
    def success(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 3 and kwargs.get("shell") is None
        return SimpleNamespace(stdout="8192, 1024\n")
    monkeypatch.setattr("role_ai.gpu_resources.subprocess.run", success)
    assert gpu_snapshot()["free_mib"] == 7168
    assert commands[0][1:3] == ["-i", "0"]
    def unavailable(*args, **kwargs):
        return SimpleNamespace(stdout="8192, N/A\n")
    monkeypatch.setattr("role_ai.gpu_resources.subprocess.run", unavailable)
    with pytest.raises(GpuBudgetError):
        gpu_snapshot()
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("nvidia-smi", 3)
    monkeypatch.setattr("role_ai.gpu_resources.subprocess.run", timeout)
    with pytest.raises(GpuBudgetError):
        gpu_snapshot()


def test_gpu_ollama_block_and_release_after_allowed_request(gpu_app):
    client, _, state = gpu_app
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "local reply"}})
    client.app.state.providers.get("ollama").transport = httpx.MockTransport(handler)
    assert save(client).status_code == 200
    cid = client.post("/api/conversations", json={}).json()["id"]
    path = f"/api/conversations/{cid}/chat"
    state["used"] = 4000
    assert client.post(path, json={"content": "hello", "provider": "ollama"}).status_code == 503
    assert calls == [] and client.get(f"/api/conversations/{cid}/messages").json() == []
    state["used"] = 1024
    assert client.post(path, json={"content": "hello", "provider": "ollama"}).status_code == 200
    assert calls[0]["keep_alive"] == 0
    assert save(client, enabled=False).status_code == 200
    assert client.post(path, json={"content": "hello", "provider": "ollama"}).status_code == 200
    assert calls[-1]["keep_alive"] == "5m"


def test_gpu_semantic_and_gateway_preflight(gpu_app, monkeypatch):
    client, settings, state = gpu_app
    settings.embedding_model = "test-embed:latest"
    did = client.post("/api/documents", json={"filename": "gpu.txt", "content": "GPU reference"}).json()["id"]
    assert save(client).status_code == 200
    state["used"] = 7000
    def forbidden(*args, **kwargs):
        pytest.fail("No external model request is allowed")
    client.app.state.semantic.transport = httpx.MockTransport(forbidden)
    response = client.post("/api/documents/semantic", json={"query": "GPU", "document_ids": [did]})
    assert response.status_code == 503 and "GPU" in response.json()["detail"]
    gateway = OpenClawGatewayAdapter(settings)
    monkeypatch.setattr(gateway, "check_access", lambda _: None)
    monkeypatch.setattr(gateway, "_reply", forbidden)
    with pytest.raises(ProviderUnavailable, match="GPU"):
        asyncio.run(gateway.reply(AssistantContext("owner", "conversation", True), [], "ollama", "assistant"))


def test_gpu_remote_ollama_rejected_and_mock_exempt(gpu_app):
    client, settings, state = gpu_app
    assert save(client).status_code == 200
    settings.ollama_url = "http://remote.example"
    with pytest.raises(GpuBudgetError):
        preflight(settings, "inference")
    state["unavailable"] = True
    cid = client.post("/api/conversations", json={}).json()["id"]
    assert client.post(f"/api/conversations/{cid}/chat", json={
        "content": "hello", "provider": "mock"}).status_code == 200


def test_gpu_training_allocator_fraction():
    calls = []
    torch = SimpleNamespace(cuda=SimpleNamespace(
        set_per_process_memory_fraction=lambda value, device: calls.append((value, device))))
    limits = {"enabled": True, "limit_mib": 6144, "allocator_limit_mib": 5120}
    assert configure_allocator(torch, limits, 8192 * 1024 * 1024) == 0.625
    assert calls == [(0.625, 0)]
    assert configure_allocator(torch, {"enabled": False}, 0) is None
    with pytest.raises(GpuBudgetError):
        configure_allocator(torch, limits, 0)


def test_gpu_training_blocked_keeps_approval(learning_app, monkeypatch):
    from test_learning import queued
    client, settings, _ = learning_app
    record, _ = queued(client)
    write_policy(settings, GpuPolicy(enabled=True, limit_mib=4096, training_mib=5120))
    monkeypatch.setattr("role_ai.gpu_resources.gpu_snapshot",
                        lambda: {"total_mib": 8192, "used_mib": 1024})
    def forbidden(*args):
        pytest.fail("Trainer must not run above the memory limit")
    assert run_one(settings, trainer=forbidden)["state"] == "blocked"
    item = client.get("/api/learning/datasets").json()[0]
    assert item["id"] == record["id"] and item["approval_hash"]


def test_gpu_training_oom_becomes_resource_wait(monkeypatch):
    class OOM(Exception):
        pass
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=OOM)))
    def out_of_memory(*args):
        raise OOM("fixture OOM")
    monkeypatch.setattr("role_ai.training._run_sft", out_of_memory)
    with pytest.raises(ResourcesBusy):
        run_sft({}, [], [], None)


# Reuse the existing meaningful learning fixture, not actual GPU/model weights.
from test_learning import learning_app  # noqa: E402,F401
