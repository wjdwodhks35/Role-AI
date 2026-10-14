import json
import time
from concurrent.futures import ThreadPoolExecutor

from role_ai.providers import ModelReply


def test_edit_cannot_create_duplicate_active_memory(signed_in):
    original = signed_in.post("/api/memories", json={
        "content": "간결한 한국어 설명 선호", "kind": "feedback"}).json()
    other = signed_in.post("/api/memories", json={
        "content": "다른 선호 정보", "kind": "feedback"}).json()
    response = signed_in.patch("/api/memories/" + other["id"], json={
        "content": original["content"], "kind": "feedback", "revision": other["revision"]
    })
    assert response.status_code == 409
    records = signed_in.get("/api/memories").json()
    assert sum(r["content"] == original["content"] for r in records) == 1
    assert next(r for r in records if r["id"] == other["id"])["revision"] == 1
    # The rule must preserve separate project scopes and allow replacing expired memories.
    separate = signed_in.patch("/api/memories/" + other["id"], json={
        "content": original["content"], "kind": "feedback", "project": "separate",
        "revision": 1,
    })
    assert separate.status_code == 200
    expired = signed_in.patch("/api/memories/" + original["id"], json={
        "content": original["content"], "kind": "feedback", "revision": 1,
        "expires_at": time.time() - 1,
    })
    assert expired.status_code == 200
    replacement = signed_in.patch("/api/memories/" + other["id"], json={
        "content": original["content"], "kind": "feedback", "revision": 2,
    })
    assert replacement.status_code == 200


def test_concurrent_identical_memory_requests_are_deduplicated(signed_in):
    def create(_):
        response = signed_in.post("/api/memories", json={
            "content": "동시에 저장하는 동일한 사용자 선호", "kind": "feedback"})
        assert response.status_code == 201
        return response.json()["id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 1
    assert len(signed_in.get("/api/memories").json()) == 1


def test_malformed_extraction_does_not_save_memory(signed_in):
    cid = signed_in.post("/api/conversations", json={}).json()["id"]
    signed_in.post(f"/api/conversations/{cid}/chat", json={"content": "내 선호"})
    mid = signed_in.get(f"/api/conversations/{cid}/messages").json()[0]["id"]
    class Malformed:
        async def complete(self, messages):
            return ModelReply('{"memories":[{"content":"valid"},{"content":""}]}', "ollama", "test")
    signed_in.app.state.providers.providers["ollama"] = Malformed()
    response = signed_in.post("/api/memories/extract", json={"source_message_id": mid})
    assert response.status_code == 503
    assert signed_in.get("/api/memories").json() == []


def test_memory_mutation_requires_csrf(signed_in):
    signed_in.headers.pop("X-CSRF-Token")
    assert signed_in.post("/api/memories", json={"content": "변경 불가"}).status_code == 403
    assert signed_in.get("/api/memories").json() == []


def test_memory_context_budget_keeps_valid_json(signed_in):
    from role_ai.memory import MemoryRepository
    for i in range(8):
        signed_in.post("/api/memories", json={
            "content": f"사용자 선호 {i} " + "가" * 980, "kind": "feedback"})
    uid = signed_in.get("/api/auth/me").json()["user"]["id"]
    with signed_in.app.state.database_factory() as db:
        context = MemoryRepository(db).context(uid, "선호")
    data = json.loads(context)
    assert len(context) <= 3500 and data
    assert all("memory_id" in item and "source_message_id" in item for item in data)
