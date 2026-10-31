import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from role_ai.assistant import AssistantContext, OpenClawGatewayAdapter
from role_ai.config import Settings
from role_ai.main import create_app
from role_ai.permissions import PermissionGate, PermissionRequest
from role_ai.providers import ModelReply, OllamaProvider, ProviderManager, ProviderUnavailable
from role_ai.repositories import ConcurrentTurn, Repository
from role_ai.security import RateLimiter, hash_password, verify_password


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_password_hashes_are_salted():
    one, two = hash_password("safe-test-password"), hash_password("safe-test-password")
    assert one != two
    assert verify_password("safe-test-password", one)
    assert not verify_password("wrong", one)
    assert not verify_password("wrong", "malformed")


def test_public_mode_cannot_enable_mock_or_insecure_cookies():
    with pytest.raises(ValidationError):
        settings(mode="public")
    with pytest.raises(ValidationError):
        settings(mode="public", cookie_secure=True, demo_enabled=True)
    with pytest.raises(ValidationError):
        settings(default_provider="mock")
    assert settings(mode="public", cookie_secure=True).mode == "public"


@pytest.mark.parametrize("password", ["x", "1234", "safe-owner-password-123", "p" * 128])
def test_registration_disabled_and_bootstrap_owner(tmp_path, password):
    for invalid in ("", "p" * 129):
        with pytest.raises(ValidationError):
            settings(bootstrap_email="owner@example.test", bootstrap_password=invalid)
    app = create_app(settings(
        database_url=f"sqlite:///{tmp_path / 'owner.db'}",
        bootstrap_email="owner@example.test", bootstrap_password=password,
    ))
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json={
            "email": "new@example.test", "password": "safe-test-password-123"
        }).status_code == 403
        response = client.post("/api/auth/login", json={
            "email": "owner@example.test", "password": password
        })
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        with app.state.database_factory() as db:
            assert Repository(db).user_by_email("owner@example.test").is_owner


def test_ollama_payload_and_metadata():
    def handler(request):
        assert request.url.path == "/api/chat"
        import json
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["think"] is False
        assert body["options"] == {"num_predict": 512, "num_ctx": 4096}
        return httpx.Response(200, json={
            "message": {"content": "안녕하세요", "thinking": "private reasoning"},
            "prompt_eval_count": 10, "eval_count": 5,
        })
    provider = OllamaProvider(settings(), httpx.MockTransport(handler))
    reply = asyncio.run(provider.complete([{"role": "user", "content": "hello"}]))
    assert reply.provider == "ollama" and reply.text == "안녕하세요"
    assert reply.input_tokens == 10 and reply.output_tokens == 5 and not reply.simulated


@pytest.mark.parametrize("content", ["", "   ", None, 123, "x" * 32001])
def test_ollama_rejects_invalid_content_even_with_thinking(content):
    provider = OllamaProvider(settings(), httpx.MockTransport(
        lambda _: httpx.Response(200, json={
            "message": {"content": content, "thinking": "private reasoning"},
            "done_reason": "length", "eval_count": 512,
        })
    ))
    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(provider.complete([{"role": "user", "content": "hello"}]))
    assert "private reasoning" not in str(exc.value)


def test_ollama_thinking_only_reply_becomes_chat_503(signed_in):
    provider = signed_in.app.state.providers.get("ollama")
    provider.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "message": {"content": "", "thinking": "private reasoning"},
        "done_reason": "length", "eval_count": 512,
    }))
    cid = signed_in.post("/api/conversations", json={}).json()["id"]
    response = signed_in.post(f"/api/conversations/{cid}/chat", json={
        "content": "hello", "provider": "ollama",
    })
    assert response.status_code == 503
    assert "private reasoning" not in response.text
    assert signed_in.get(f"/api/conversations/{cid}/messages").json() == []


@pytest.mark.parametrize("case", ["timeout", "server_error", "malformed"])
def test_ollama_errors_do_not_leak_upstream_data(case):
    def handler(request):
        if case == "timeout":
            raise httpx.ReadTimeout("secret-token", request=request)
        if case == "server_error":
            return httpx.Response(500, text="secret-token")
        return httpx.Response(200, json={"oops": "secret-token"})
    provider = OllamaProvider(settings(), httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(provider.complete([]))
    assert "secret-token" not in str(exc.value)


def test_ollama_probe_checks_model_installation():
    provider = OllamaProvider(settings(), httpx.MockTransport(
        lambda _: httpx.Response(200, json={"models": [{"name": "other:latest"}]})
    ))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.probe())


def test_paid_provider_and_mock_are_not_enabled_implicitly():
    manager = ProviderManager(settings(openai_enabled=True, openai_api_key="fake-test-only"))
    with pytest.raises(ProviderUnavailable):
        manager.get("mock")
    with pytest.raises(ProviderUnavailable):
        asyncio.run(manager.get("openai").complete([]))
    assert not any(item["paid_calls"] for item in manager.catalog())


def test_openclaw_and_permission_gate_fail_closed():
    with pytest.raises(ProviderUnavailable):
        asyncio.run(OpenClawGatewayAdapter().reply(
            AssistantContext("owner", "session", owner=True), [], "ollama", "assistant"
        ))
    decision = PermissionGate().evaluate(PermissionRequest(
        "public-user", "shell", "execute", "server"
    ))
    assert decision.state == "denied"


def test_concurrent_turn_cannot_overwrite_history(signed_in):
    with signed_in.app.state.database_factory() as db:
        repo = Repository(db)
        user = repo.user_by_email("alice@example.test")
        conversation = repo.create_conversation(user.id, "concurrency")
        uid, cid = user.id, conversation.id
        reply = ModelReply("reply", "mock", "mock-test", simulated=True)
        repo.append_turn(uid, cid, 0, "first", reply, "assistant")
        with pytest.raises(ConcurrentTurn):
            repo.append_turn(uid, cid, 0, "stale", reply, "assistant")
        assert len(repo.messages(uid, cid)) == 2


def test_limiter_capacity_fails_closed():
    limiter = RateLimiter(maximum=1, capacity=1)
    assert limiter.allow("one")
    assert not limiter.allow("one")
    assert not limiter.allow("two")
