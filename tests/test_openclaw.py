import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from role_ai.assistant import AssistantContext, OpenClawGatewayAdapter
from role_ai.config import Settings
from role_ai.main import create_app
from role_ai.providers import ProviderUnavailable


@pytest.fixture
def gateway_settings(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({
        "tools": {"deny": ["*"]},
        "agents": {"defaults": {"model": {"primary": "ollama/qwen2.5:7b", "fallbacks": []}}},
        "gateway": {"auth": {"mode": "token"},
                    "http": {"endpoints": {"chatCompletions": {"enabled": True}}}},
    }))
    return Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'gateway.db'}",
        demo_enabled=True, default_provider="mock", registration_enabled=True,
        bootstrap_email="owner@example.test", bootstrap_password="safe-owner-password-123",
        openclaw_enabled=True, openclaw_config_path=str(path),
        openclaw_token="fake-gateway-test-token",
    )


def response(request):
    if request.method == "GET":
        return httpx.Response(200, json={"data": [{"id": "openclaw/default"}]})
    return httpx.Response(200, json={"choices": [{"message": {"content": "안녕하세요"}}]})


def test_gateway_contract_and_stable_session(gateway_settings):
    requests = []
    def handler(request):
        requests.append(request)
        return response(request)
    adapter = OpenClawGatewayAdapter(gateway_settings, httpx.MockTransport(handler))
    context = AssistantContext("owner", "conversation", True)
    assert asyncio.run(adapter.probe(context))["inference_verified"] is False
    for _ in range(2):
        reply = asyncio.run(adapter.reply(context, [{"role": "user", "content": "hi"}],
                                          "openai", "developer"))
        assert reply.provider == "openclaw" and reply.model == "openclaw/default"
    payloads = [json.loads(r.content) for r in requests if r.method == "POST"]
    assert payloads[0]["user"] == payloads[1]["user"]
    assert payloads[0]["model"] == "openclaw/default"
    assert payloads[0]["tool_choice"] == "none"
    assert payloads[0]["max_completion_tokens"] == 512
    assert "x-openclaw-model" not in requests[-1].headers
    assert requests[-1].headers["authorization"] == "Bearer fake-gateway-test-token"
    assert adapter.session_key(context, "doctor") != adapter.session_key(context, "developer")
    assert adapter.session_key(AssistantContext("owner", "other", True), "developer") != payloads[0]["user"]

    reply = asyncio.run(adapter.reply(context, [{"role": "user", "content": "테스트 작성"}],
                                      "ollama", "code_tester"))
    tester = json.loads(requests[-1].content)
    assert reply.provider == "openclaw"
    assert tester["tools"] == [] and tester["tool_choice"] == "none"
    assert tester["user"] != payloads[0]["user"]
    assert "미실행" in tester["messages"][0]["content"]
    assert "정상·경계·예외·회귀" in tester["messages"][0]["content"]


@pytest.mark.parametrize("unsafe", ["tools", "paid", "fallback", "remote", "disabled", "public", "nonowner", "missing"])
def test_gateway_guards_before_network(gateway_settings, unsafe):
    context = AssistantContext("owner", "conversation", unsafe != "nonowner")
    from pathlib import Path
    path = Path(gateway_settings.openclaw_config_path)
    policy = json.loads(path.read_text())
    if unsafe == "tools":
        policy["tools"]["deny"] = []
    elif unsafe == "paid":
        policy["agents"]["defaults"]["model"]["primary"] = "openai/gpt-test"
    elif unsafe == "fallback":
        policy["agents"]["defaults"]["model"]["fallbacks"] = ["openai/gpt-test"]
    elif unsafe == "remote":
        gateway_settings.openclaw_url = "https://example.com"
    elif unsafe == "disabled":
        gateway_settings.openclaw_enabled = False
    elif unsafe == "public":
        gateway_settings.mode = "public"
    elif unsafe == "missing":
        gateway_settings.openclaw_config_path = ""
    path.write_text(json.dumps(policy))
    def forbidden(request):
        pytest.fail("Guard must block before network")
    adapter = OpenClawGatewayAdapter(gateway_settings, httpx.MockTransport(forbidden))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(adapter.reply(context, [{"role": "user", "content": "hi"}], "ollama", "assistant"))


@pytest.mark.parametrize("case", ["timeout", "unauthorized", "tool_response", "malformed"])
def test_gateway_error_redaction_without_retries(gateway_settings, case):
    calls = []
    def handler(request):
        calls.append(request)
        if case == "timeout":
            raise httpx.ReadTimeout("fake-gateway-test-token", request=request)
        if case == "unauthorized":
            return httpx.Response(401, text="fake-gateway-test-token")
        if case == "tool_response":
            return httpx.Response(200, json={"choices": [{"message": {
                "content": "hello", "tool_calls": [{"id": "do-not-run"}]}}]})
        return httpx.Response(200, json={})
    adapter = OpenClawGatewayAdapter(gateway_settings, httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailable) as error:
        asyncio.run(adapter.reply(AssistantContext("owner", "conversation", True),
                                  [{"role": "user", "content": "hi"}], "ollama", "assistant"))
    assert len(calls) == 1
    assert "fake-gateway-test-token" not in str(error.value)


def test_owner_chat_nonowner_rejection_and_manual_fallback(gateway_settings):
    requests = []
    def handler(request):
        requests.append(request)
        return response(request)
    app = create_app(gateway_settings, gateway_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={
            "email": "owner@example.test", "password": "safe-owner-password-123"}).json()
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        assert client.get("/api/status").json()["openclaw_available"] is True
        assert client.post("/api/assistant/openclaw/test").status_code == 200
        cid = client.post("/api/conversations", json={}).json()["id"]
        url = f"/api/conversations/{cid}/chat"
        reply = client.post(url, json={"engine": "openclaw", "content": "hello"}).json()
        assert reply["assistant_engine"] == "openclaw"
        fallback = client.post(url, json={"content": "next"}).json()
        assert fallback["assistant_engine"] == "provider_fallback"
        assert fallback["simulated"] is True
        assert len(client.get(f"/api/conversations/{cid}/messages").json()) == 4
        signed = client.post("/api/auth/register", json={
            "email": "guest@example.test", "password": "safe-guest-password-123"}).json()
        client.headers["X-CSRF-Token"] = signed["csrf_token"]
        guest_cid = client.post("/api/conversations", json={}).json()["id"]
        count = len(requests)
        assert client.post("/api/assistant/openclaw/test").status_code == 403
        assert client.post(f"/api/conversations/{guest_cid}/chat", json={
            "engine": "openclaw", "content": "hello"}).status_code == 403
        assert len(requests) == count
