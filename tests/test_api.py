import time

import pytest

from role_ai.main import COOKIE
from role_ai.repositories import Repository
from role_ai.security import token_hash


def create_conversation(client):
    response = client.post("/api/conversations", json={"title": "테스트"})
    assert response.status_code == 201
    return response.json()["id"]


def test_health_and_static(client):
    assert client.get("/api/health").json() == {"status": "ok", "phase": 1}
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="ko"' in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200


def test_requires_auth(client):
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/status").status_code == 401


def test_password_and_session_storage(signed_in):
    client = signed_in
    with client.app.state.database_factory() as db:
        repo = Repository(db)
        user = repo.user_by_email("alice@example.test")
        assert user.password_hash.startswith("pbkdf2_sha256$")
        assert "safe-test-password" not in user.password_hash
        raw = client.cookies.get(COOKIE)
        session = repo.auth_session(token_hash(raw))
        assert session.token_hash != raw
        assert session.expires_at > time.time()
    assert "csrf_token" in client.get("/api/auth/me").json()
    assert client.get("/api/auth/me").headers["Cache-Control"] == "no-store"


def test_logout_invalidates_server_session(signed_in):
    raw = signed_in.cookies.get(COOKIE)
    assert signed_in.post("/api/auth/logout").status_code == 204
    signed_in.cookies.set(COOKIE, raw)
    assert signed_in.get("/api/auth/me").status_code == 401


def test_login_and_bad_credentials(signed_in):
    assert signed_in.post("/api/auth/logout").status_code == 204
    assert signed_in.post("/api/auth/login", json={
        "email": "ALICE@example.test", "password": "safe-test-password-123"
    }).status_code == 200
    assert signed_in.post("/api/auth/login", json={
        "email": "alice@example.test", "password": "incorrect-password-123"
    }).status_code == 401


def test_duplicate_registration(signed_in):
    assert signed_in.post("/api/auth/register", json={
        "email": "alice@example.test", "password": "safe-test-password-123"
    }).status_code == 409


def test_csrf_and_origin(signed_in):
    client = signed_in
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/conversations", json={}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post("/api/conversations", json={},
                       headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/conversations", json={},
                       headers={"Origin": "http://testserver"}).status_code == 201


def test_expired_session(signed_in):
    with signed_in.app.state.database_factory() as db:
        repo = Repository(db)
        session = repo.auth_session(token_hash(signed_in.cookies.get(COOKIE)))
        session.expires_at = time.time() - 1
        db.commit()
    assert signed_in.get("/api/auth/me").status_code == 401


def test_user_isolation(signed_in):
    client = signed_in
    conversation_id = create_conversation(client)
    response = client.post("/api/auth/register", json={
        "email": "bob@example.test", "password": "safe-test-password-456"
    })
    assert response.status_code == 201
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert client.get("/api/conversations").json() == []
    assert client.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
    assert client.post(f"/api/conversations/{conversation_id}/chat",
                       json={"content": "steal"}).status_code == 404


@pytest.mark.parametrize("skill", ["developer", "code_tester"])
def test_chat_records_real_metadata_and_marks_mock(signed_in, skill):
    assert {s["id"]: s["label"] for s in signed_in.get("/api/status").json()["skills"]}["code_tester"] == "코드 테스트"
    conversation_id = create_conversation(signed_in)
    response = signed_in.post(f"/api/conversations/{conversation_id}/chat", json={
        "content": "<script>alert(1)</script>", "skill": skill
    })
    assert response.status_code == 200
    reply = response.json()
    assert reply["simulated"] is True
    assert reply["assistant_engine"] == "provider_fallback"
    assert reply["tools"] == []
    messages = signed_in.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "<script>alert(1)</script>"
    assert messages[1]["provider"] == "mock"
    assert messages[1]["skill"] == skill


def test_unimplemented_provider_never_fakes_success(signed_in):
    conversation_id = create_conversation(signed_in)
    response = signed_in.post(f"/api/conversations/{conversation_id}/chat",
                              json={"content": "hello", "provider": "openai"})
    assert response.status_code == 403
    assert signed_in.get(f"/api/conversations/{conversation_id}/messages").json() == []
    status = signed_in.get("/api/status").json()
    assert status["openclaw"] == "disabled"
    assert status["paid_api_calls"] == "disabled"


def test_invalid_requests(signed_in):
    conversation_id = create_conversation(signed_in)
    url = f"/api/conversations/{conversation_id}/chat"
    assert signed_in.post(url, json={"content": " "}).status_code == 422
    assert signed_in.post(url, json={"content": "x" * 4001}).status_code == 422
    assert signed_in.post(url, json={"content": "hi", "provider": "evil"}).status_code == 422
    assert signed_in.post(url, json={"content": "hi", "skill": "shell"}).status_code == 422


def test_body_limit(client):
    response = client.post("/api/auth/login", content=b"x" * 65537,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_auth_rate_limit(client):
    for _ in range(10):
        assert client.post("/api/auth/login", json={
            "email": "missing@example.test", "password": "incorrect-password-123"
        }).status_code == 401
    assert client.post("/api/auth/login", json={
        "email": "missing@example.test", "password": "incorrect-password-123"
    }).status_code == 429


def test_provider_probe(signed_in):
    response = signed_in.post("/api/providers/mock/test")
    assert response.json()["state"] == "simulated"
    assert signed_in.post("/api/providers/openai/test").status_code == 403
