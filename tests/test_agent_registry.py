import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from role_ai.agent_registry import (
    AgentConfiguration,
    AgentConfigurationError,
    AgentRegistry,
    default_configuration,
    load_configuration,
)
from role_ai.config import Settings
from role_ai.main import create_app


def document():
    return default_configuration().model_dump(mode="json")


@pytest.fixture
def owner_client(tmp_path):
    from fastapi.testclient import TestClient

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'owner.db'}",
        storage_dir=str(tmp_path / "artifacts"),
        bootstrap_email="owner@example.test",
        bootstrap_password="fixture-password",
        registration_enabled=True,
        demo_enabled=True,
        default_provider="mock",
    )
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": "fixture-password"}
        )
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield client


def test_six_definitions_resolve_existing_models_without_execution():
    settings = Settings(_env_file=None, ollama_model="existing-model")
    one = AgentRegistry(settings).describe()
    assert len(one["agents"]) == 6
    assert len({a["log_namespace"] for a in one["agents"]}) == 6
    assert all(a["model"] == "existing-model" for a in one["agents"])
    assert not one["execution_enabled"] and not one["scheduler_enforced"]
    assert one["config_hash"] == AgentRegistry(settings).describe()["config_hash"]
    assert all(a["mode"] == "definition_only" for a in one["agents"])
    changed = Settings(_env_file=None, ollama_model="different-model")
    assert AgentRegistry(changed).config_hash != one["config_hash"]


@pytest.mark.parametrize(
    "damage",
    ["missing", "duplicate", "privilege", "token", "model", "parallel", "type", "dispatch"],
)
def test_schema_rejects_unsupported_or_unsafe_settings(damage):
    value = document()
    if damage == "missing":
        value["agents"].pop()
    elif damage == "duplicate":
        value["agents"][-1] = value["agents"][0]
    elif damage == "privilege":
        value["agents"][0]["capabilities"] = ["exec"]
    elif damage == "token":
        value["api_token"] = "do-not-echo-this-secret"
    elif damage == "model":
        value["agents"][0]["model_ref"] = "remote-model"
    elif damage == "parallel":
        value["max_local_active"] = 6
    elif damage == "type":
        value["agents"][0]["max_steps"] = "8"
    else:
        value["agents"][0]["mode"] = "execute"
    with pytest.raises(ValidationError):
        AgentConfiguration.model_validate_json(json.dumps(value))


def test_explicit_configuration_is_not_ignored_and_has_no_raw_error_leak(tmp_path):
    path = tmp_path / "roles.json"
    value = document()
    value["agents"][0]["timeout_seconds"] = 17
    path.write_text(json.dumps(value))
    assert load_configuration(tmp_path, path.name).agents[0].timeout_seconds == 17
    path.write_text('{"api_token":"do-not-echo-this-secret"}')
    with pytest.raises(AgentConfigurationError) as error:
        load_configuration(tmp_path, path.name)
    assert "do-not-echo" not in str(error.value)
    assert "no fallback" in str(error.value)
    with pytest.raises(AgentConfigurationError):
        load_configuration(tmp_path, "missing.json")


@pytest.mark.parametrize(
    "filename", ["../roles.json", "/tmp/roles.json", "x\\roles.json", "roles.yaml"]
)
def test_path_escape_rejected(tmp_path, filename):
    with pytest.raises(AgentConfigurationError):
        load_configuration(tmp_path, filename)


def test_symlink_large_file_and_duplicate_keys_rejected(tmp_path):
    actual = tmp_path / "actual.json"
    actual.write_text(json.dumps(document()))
    link = tmp_path / "link.json"
    link.symlink_to(actual)
    with pytest.raises(AgentConfigurationError):
        load_configuration(tmp_path, link.name)
    actual.write_text(" " * 65537)
    with pytest.raises(AgentConfigurationError):
        load_configuration(tmp_path, actual.name)
    actual.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(AgentConfigurationError):
        load_configuration(tmp_path, actual.name)


def test_invalid_config_rejected_before_database_creation(tmp_path):
    settings = Settings(
        _env_file=None,
        agent_config_dir=str(tmp_path),
        agent_config_file="missing.json",
        database_url=f"sqlite:///{tmp_path / 'absent.db'}",
    )
    with pytest.raises(AgentConfigurationError):
        create_app(settings)
    assert not (tmp_path / "absent.db").exists()


def test_api_auth_owner_no_store_and_no_mutation(owner_client, monkeypatch):
    from sqlalchemy import inspect

    signed_in = owner_client
    app = signed_in.app
    with app.state.database_factory() as db:
        before = set(inspect(db.bind).get_table_names())
    monkeypatch.setattr(
        app.state.providers, "get", lambda *_: pytest.fail("Model lookup during definition query")
    )
    response = signed_in.get("/api/agents")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert len(response.json()["agents"]) == 6
    assert not response.json()["execution_enabled"]
    assert signed_in.post("/api/agents", json={}).status_code == 405
    with app.state.database_factory() as db:
        assert set(inspect(db.bind).get_table_names()) == before
    # Registering the second actor preserves normal ownership boundaries.
    assert (
        signed_in.post(
            "/api/auth/register", json={"email": "other@example.test", "password": "test"}
        ).status_code
        == 201
    )
    assert signed_in.get("/api/agents").status_code == 403


def test_api_requires_authentication(client):
    assert client.get("/api/agents").status_code == 401


def test_chat_keeps_one_provider_call_without_registry_dispatch(signed_in, monkeypatch):
    from role_ai.providers import ModelReply

    calls = []
    providers = signed_in.app.state.providers
    original = providers.get

    class Counter:
        async def complete(self, messages):
            calls.append(messages)
            return ModelReply(text="합성 답변", provider="mock", model="fixture", simulated=True)

    monkeypatch.setattr(
        providers, "get", lambda name: Counter() if name == "mock" else original(name)
    )
    monkeypatch.setattr(
        signed_in.app.state.agent_registry,
        "describe",
        lambda: pytest.fail("Role dispatch added to the chat path"),
    )
    cid = signed_in.post("/api/conversations", json={}).json()["id"]
    response = signed_in.post(
        f"/api/conversations/{cid}/chat", json={"content": "안녕", "provider": "mock"}
    )
    assert response.status_code == 200
    assert len(calls) == 1


def test_public_mode_cannot_list_definitions():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from role_ai.agent_registry import agents_router

    settings = Settings(_env_file=None, mode="public", cookie_secure=True)
    app = FastAPI()
    app.include_router(
        agents_router(
            lambda: (SimpleNamespace(is_owner=True), None), settings, AgentRegistry(settings)
        )
    )
    assert TestClient(app).get("/api/agents").status_code == 403


@pytest.mark.parametrize("value", [True, 1.0])
def test_parallelism_limit_does_not_accept_boolean_or_float(value):
    raw = document()
    raw["max_local_active"] = value
    with pytest.raises(ValidationError):
        AgentConfiguration.model_validate_json(json.dumps(raw))


def test_nested_invalid_json_has_safe_error(tmp_path):
    (tmp_path / "roles.json").write_text("[" * 2000 + "]" * 2000)
    with pytest.raises(AgentConfigurationError, match="no fallback"):
        load_configuration(tmp_path, "roles.json")


def test_config_directory_symlink_rejected(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    (root / "roles.json").write_text(json.dumps(document()))
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(AgentConfigurationError):
        load_configuration(alias, "roles.json")


def test_configured_restrictions_and_existing_coding_model_are_reflected(tmp_path):
    raw = document()
    raw["agents"][1]["capabilities"] = ["code.propose"]
    (tmp_path / "roles.json").write_text(json.dumps(raw))
    settings = SimpleNamespace(
        agent_config_dir=tmp_path,
        agent_config_file="roles.json",
        ollama_model="existing-main",
        ollama_coding_model="existing-coder",
    )
    report = AgentRegistry(settings).describe()
    assert report["source"] == "configured"
    coding = next(a for a in report["agents"] if a["id"] == "coding")
    assert coding["model"] == "existing-coder"
    assert coding["capabilities"] == ["code.propose"]
    assert len(coding["prompt_sha256"]) == 64
    assert not report["execution_enabled"]
