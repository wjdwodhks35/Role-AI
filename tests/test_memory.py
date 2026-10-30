import time

import pytest

from role_ai.models import MemoryHistory
from role_ai.providers import ModelReply


def remember(client, **kwargs):
    return client.post("/api/memories", json={"content": "한국어로 설명해 주세요",
                                             "kind": "feedback", **kwargs})


def test_memory_confirmed_dedup_search_and_export(signed_in):
    client = signed_in
    one = remember(client).json()
    two = remember(client).json()
    assert one["id"] == two["id"] and one["state"] == "confirmed"
    found = client.get("/api/memories/search", params={"q": "한국어"}).json()
    assert found["mode"] == "keyword" and found["memories"][0]["id"] == one["id"]
    export = client.get("/api/memories/export")
    assert export.status_code == 200 and "한국어" in export.text


def test_memory_edit_revision_history_and_delete(signed_in):
    record = remember(signed_in).json()
    path = "/api/memories/" + record["id"]
    body = {"content": "짧게 설명해 주세요", "kind": "feedback", "revision": 1}
    assert signed_in.patch(path, json=body).json()["revision"] == 2
    assert signed_in.patch(path, json=body).status_code == 409
    with signed_in.app.state.database_factory() as db:
        from sqlalchemy import select
        assert db.scalar(select(MemoryHistory).where(
            MemoryHistory.memory_id == record["id"])).content == record["content"]
    assert signed_in.delete(path).status_code == 204
    assert "짧게" not in signed_in.get("/api/memories/export").text
    with signed_in.app.state.database_factory() as db:
        from sqlalchemy import select
        assert db.scalar(select(MemoryHistory).where(
            MemoryHistory.memory_id == record["id"])) is not None


def test_expired_and_other_project_memories_are_excluded(signed_in):
    remember(signed_in, expires_at=time.time() - 10)
    remember(signed_in, content="프로젝트별 한국어 설명", kind="project", project="private-project")
    assert signed_in.get("/api/memories/search", params={
        "q": "한국어", "project": "other-project"}).json()["memories"] == []


def test_memory_ownership_and_provenance(signed_in):
    record = remember(signed_in).json()
    response = signed_in.post("/api/auth/register", json={
        "email": "bob@example.test", "password": "safe-test-password-456"})
    signed_in.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert signed_in.get("/api/memories").json() == []
    assert signed_in.delete("/api/memories/" + record["id"]).status_code == 404
    assert remember(signed_in, source_message_id="not-owned").status_code == 404


def test_candidates_are_not_active_until_confirmation(signed_in):
    client = signed_in
    cid = client.post("/api/conversations", json={}).json()["id"]
    client.post(f"/api/conversations/{cid}/chat", json={"content": "Python을 선호합니다"})
    mid = client.get(f"/api/conversations/{cid}/messages").json()[0]["id"]
    class Extractor:
        async def complete(self, messages):
            return ModelReply('{"memories":[{"content":"Python 선호","kind":"user"}]}',
                              "ollama", "test")
    client.app.state.providers.providers["ollama"] = Extractor()
    result = client.post("/api/memories/extract", json={"source_message_id": mid})
    assert result.status_code == 201
    memory = result.json()[0]
    assert memory["source_message_id"] == mid and memory["state"] == "candidate"
    assert client.get("/api/memories/search", params={"q": "Python"}).json()["memories"] == []
    assert client.post("/api/memories/" + memory["id"] + "/confirm").status_code == 200
    assert len(client.get("/api/memories/search", params={"q": "Python"}).json()["memories"]) == 1


def test_chat_uses_memory_without_changing_system_policy(signed_in):
    remember(signed_in, content="한국어를 선호합니다")
    captured = []
    class Recorder:
        async def complete(self, messages):
            captured.extend(messages)
            return ModelReply("hello", "mock", "test", simulated=True)
    signed_in.app.state.providers.providers["mock"] = Recorder()
    cid = signed_in.post("/api/conversations", json={}).json()["id"]
    assert signed_in.post(f"/api/conversations/{cid}/chat", json={"content": "hi"}).status_code == 200
    assert "한국어를 선호합니다" in captured[0]["content"]
    assert "권한 승인이 아닙니다" in captured[0]["content"]


@pytest.mark.parametrize("value", ["", " "])
def test_empty_memory_rejected(signed_in, value):
    assert remember(signed_in, content=value).status_code == 422
