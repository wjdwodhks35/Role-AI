import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from role_ai.config import Settings
from role_ai.main import create_app
from role_ai.models import ApiUsage
from role_ai.providers import ProviderUnavailable


@pytest.fixture
def paid_app(tmp_path):
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'paid.db'}",
        bootstrap_email="owner@example.test", bootstrap_password="safe-owner-password-123",
        registration_enabled=True, demo_enabled=True, default_provider="mock",
        openai_enabled=True, openai_api_key="fake-openai-test-secret", openai_model="gpt-test",
        openai_input_usd_per_million=Decimal("1"),
        openai_output_usd_per_million=Decimal("2"),
        openai_daily_budget_usd=Decimal("1"), openai_request_budget_usd=Decimal("0.25"),
    )
    calls, state = [], {"case": "success"}
    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "gpt-test"})
        if state["case"] == "timeout":
            raise httpx.ReadTimeout("fake-openai-test-secret", request=request)
        if state["case"] == "cancel":
            raise asyncio.CancelledError()
        if state["case"] == "unauthorized":
            return httpx.Response(401, text="fake-openai-test-secret")
        payload = {
            "model": "gpt-test", "status": "completed",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": "안녕하세요"}]}],
        }
        if state["case"] == "missing_usage":
            del payload["usage"]
        if state["case"] == "incomplete":
            payload["status"] = "incomplete"
        if state["case"] == "snapshot":
            payload["model"] = "gpt-test-2026-01-01"
        if state["case"] == "tool":
            payload["output"] = [{"type": "function_call", "name": "shell"}]
        return httpx.Response(200, json=payload)
    app = create_app(settings, openai_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        data = client.post("/api/auth/login", json={
            "email": "owner@example.test", "password": "safe-owner-password-123"}).json()
        client.headers["X-CSRF-Token"] = data["csrf_token"]
        cid = client.post("/api/conversations", json={}).json()["id"]
        yield client, settings, calls, state, cid


def post_chat(client, cid, **kwargs):
    return client.post(f"/api/conversations/{cid}/chat", json={
        "content": "hello", "provider": "openai", "allow_paid": True, **kwargs
    })


def test_quote_never_calls_provider(paid_app):
    client, settings, calls, _, cid = paid_app
    quote = client.post(f"/api/conversations/{cid}/quote", json={
        "provider": "openai", "content": "hello"}).json()
    assert quote["within_budget"] and quote["requires_consent"]
    assert Decimal(quote["estimated_upper_usd"]) > 0
    assert quote["period"] == "UTC day" and calls == []
    assert client.get("/api/usage").json()["records"] == []
    settings.openai_request_budget_usd = Decimal("0.000001")
    assert not client.post(f"/api/conversations/{cid}/quote", json={
        "provider": "openai", "content": "hello"}).json()["within_budget"]


def test_consent_required_before_reservation(paid_app):
    client, _, calls, _, cid = paid_app
    assert post_chat(client, cid, allow_paid=False).status_code == 403
    assert calls == [] and client.get("/api/usage").json()["records"] == []


def test_responses_contract_and_settlement(paid_app):
    client, _, calls, _, cid = paid_app
    response = post_chat(client, cid)
    assert response.status_code == 200
    reply = response.json()
    assert reply["usage"] == {"input_tokens": 20, "output_tokens": 10}
    assert reply["billing"] == {"calculated_usd": "0.00004", "state": "settled"}
    request = calls[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer fake-openai-test-secret"
    body = json.loads(request.content)
    assert body["store"] is False and body["max_output_tokens"] == 512
    assert body["tool_choice"] == "none" and body["tools"] == []
    assert body["input"][-1] == {"role": "user", "content": "hello"}
    record = client.get("/api/usage").json()["records"][0]
    assert record["input_usd_per_million"] == "1"
    assert record["state"] == "settled"
    assert "fake-openai-test-secret" not in client.get("/api/status").text


@pytest.mark.parametrize("field,value", [
    ("openai_enabled", False), ("openai_api_key", ""),
    ("openai_model", ""), ("openai_input_usd_per_million", Decimal("0")),
    ("openai_output_usd_per_million", Decimal("0")),
    ("openai_daily_budget_usd", Decimal("0")),
    ("openai_request_budget_usd", Decimal("0")),
])
def test_missing_config_blocks_before_network(paid_app, field, value):
    client, settings, calls, _, cid = paid_app
    if field == "openai_api_key":
        from pydantic import SecretStr
        value = SecretStr(value)
    setattr(settings, field, value)
    assert post_chat(client, cid).status_code == 503
    assert calls == [] and client.get("/api/usage").json()["records"] == []


def test_model_probe_does_not_infer_or_reserve(paid_app):
    client, _, calls, _, _ = paid_app
    response = client.post("/api/providers/openai/test")
    assert response.status_code == 200
    assert response.json()["inference_verified"] is False
    assert calls[0].method == "GET"
    assert client.get("/api/usage").json()["records"] == []


def test_nonowner_cannot_use_or_inspect_owner_key(paid_app):
    client, _, calls, _, _ = paid_app
    data = client.post("/api/auth/register", json={
        "email": "guest@example.test", "password": "safe-guest-password-123"}).json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    cid = client.post("/api/conversations", json={}).json()["id"]
    assert post_chat(client, cid).status_code == 403
    assert client.post(f"/api/conversations/{cid}/quote",
                       json={"provider": "openai", "content": "hi"}).status_code == 403
    assert client.post("/api/providers/openai/test").status_code == 403
    assert client.get("/api/usage").status_code == 403
    status = client.get("/api/status").json()
    assert next(p for p in status["providers"] if p["id"] == "openai")["usable"] is False
    assert calls == []


def test_request_limit_blocks_network(paid_app):
    client, settings, calls, _, cid = paid_app
    settings.openai_request_budget_usd = Decimal("0.000001")
    assert post_chat(client, cid).status_code == 503
    assert calls == []


def test_timeout_retains_budget_across_retries_and_days(paid_app):
    client, settings, calls, state, cid = paid_app
    quote = client.post(f"/api/conversations/{cid}/quote", json={
        "provider": "openai", "content": "hello"}).json()
    settings.openai_daily_budget_usd = Decimal(quote["estimated_upper_usd"])
    state["case"] = "timeout"
    failure = post_chat(client, cid)
    assert failure.status_code == 503
    assert "fake-openai-test-secret" not in failure.text
    record = client.get("/api/usage").json()["records"][0]
    assert record["state"] == "uncertain"
    with client.app.state.database_factory() as db:
        stored = db.get(ApiUsage, record["id"])
        stored.created_at = time.time() - 172800
        db.commit()
    assert post_chat(client, cid).status_code == 503
    assert len(calls) == 1
    assert client.get(f"/api/conversations/{cid}/messages").json() == []


@pytest.mark.parametrize("case,expected", [
    ("missing_usage", "uncertain"), ("unauthorized", "uncertain"),
    ("incomplete", "settled"), ("tool", "settled"),
])
def test_uncertain_or_rejected_output_is_accounted(paid_app, case, expected):
    client, _, calls, state, cid = paid_app
    state["case"] = case
    response = post_chat(client, cid)
    assert response.status_code == (200 if case == "missing_usage" else 503)
    assert client.get("/api/usage").json()["records"][0]["state"] == expected
    assert len(calls) == 1


def test_snapshot_model_is_reported_honestly(paid_app):
    client, _, _, state, cid = paid_app
    state["case"] = "snapshot"
    assert post_chat(client, cid).json()["model"] == "gpt-test-2026-01-01"


def test_budget_reservation_is_atomic(paid_app):
    client, settings, _, _, _ = paid_app
    ledger = client.app.state.billing
    user_id = client.get("/api/auth/me").json()["user"]["id"]
    prompt = [{"role": "user", "content": "hello"}]
    settings.openai_daily_budget_usd = Decimal(ledger.quote(prompt)["estimated_upper_usd"])
    def reserve():
        try:
            return ledger.reserve(user_id, prompt)
        except ProviderUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(r is not None for r in results) == 1


def test_settlement_uses_reserved_price_snapshot(paid_app):
    client, settings, _, _, _ = paid_app
    ledger = client.app.state.billing
    uid = client.get("/api/auth/me").json()["user"]["id"]
    rid = ledger.reserve(uid, [{"role": "user", "content": "hi"}])
    settings.openai_input_usd_per_million = Decimal("999")
    assert ledger.finish(rid, (20, 10)) == ("0.00004", "settled")


def test_cancellation_retains_reservation(paid_app):
    client, _, calls, state, _ = paid_app
    state["case"] = "cancel"
    uid = client.get("/api/auth/me").json()["user"]["id"]
    provider = client.app.state.providers.get("openai")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.complete_for_user(
            [{"role": "user", "content": "hi"}], uid, True, True))
    assert len(calls) == 1
    assert client.get("/api/usage").json()["records"][0]["state"] == "uncertain"
