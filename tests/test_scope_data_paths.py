"""Synthetic HTTP isolation, immutable conversation bounds and approved archival."""

import hashlib
import json
import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from role_ai.models import (
    Document,
    DocumentChunk,
    Memory,
    MemoryHistory,
    Conversation,
)
from role_ai.scope_models import SourceArchive, ConversationBinding, ConversationSource
from role_ai.providers import ModelReply
from role_ai.backup import create_backup, restore_backup
from role_ai.database import build_database
from role_ai.project_scopes import ScopeRepository
from role_ai.scope_data import resource_for


def doc(c, project="", content="car synthetic secret"):
    r = c.post(
        "/api/documents", json={"filename": "synthetic.txt", "content": content, "project": project}
    )
    assert r.status_code == 201, r.text
    return r.json()


def project(c, name):
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def share(c, d, pid):
    r = c.post(
        f"/api/projects/{pid}/read-grants",
        json={
            "kind": "document",
            "resource_id": d["id"],
            "version": hashlib.sha256(d.get("body", "car synthetic secret").encode()).hexdigest(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_data_routes_default_private_and_only_exact_grants_cross_projects(signed_in):
    c = signed_in
    personal = doc(c)
    a = doc(c, "A")
    b = project(c, "B")
    assert {d["id"] for d in c.get("/api/documents").json()} == {personal["id"]}
    for path in [f"/api/documents/{a['id']}", f"/api/documents/{a['id']}/export"]:
        assert c.get(path).status_code == 404
        assert c.get(path, params={"project": "B"}).status_code == 404
    assert (
        c.get("/api/documents/search", params={"q": "car", "project": "B"}).json()["sources"] == []
    )
    gid = share(c, a, b)
    assert [d["id"] for d in c.get("/api/documents", params={"project": "B"}).json()] == [a["id"]]
    assert (
        c.get(f"/api/documents/{a['id']}/export", params={"project": "B"}).text
        == "car synthetic secret"
    )
    assert c.delete(f"/api/documents/{a['id']}", params={"project": "B"}).status_code == 404
    assert c.delete(f"/api/projects/{b}/read-grants/{gid}").status_code == 204
    assert c.get(f"/api/documents/{a['id']}", params={"project": "B"}).status_code == 404
    assert (
        c.get("/api/documents/search", params={"q": "car", "project": "B"}).json()["sources"] == []
    )


def test_archival_preserves_original_chunks_versions_and_is_idempotent(signed_in):
    c = signed_in
    d = doc(c)
    path = f"/api/documents/{d['id']}"
    for _ in range(2):
        r = c.delete(path)
        assert r.status_code == 204 and r.headers["x-role-ai-removal"] == "archived"
    assert c.get(path).status_code == 404
    assert c.get(path + "/export").status_code == 404
    assert c.get("/api/documents").json() == []
    assert c.get("/api/documents/search?q=car").json()["sources"] == []
    with c.app.state.database_factory() as db:
        row = db.get(Document, d["id"])
        assert row.content == "car synthetic secret"
        assert (
            db.scalar(select(DocumentChunk).where(DocumentChunk.document_id == row.id)) is not None
        )
        resource = resource_for(db, "document", row.id)
        assert db.get(SourceArchive, resource.id) is not None
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(
                text("DELETE FROM scope_archives WHERE resource_id=:id"), {"id": resource.id}
            )
    replacement = doc(c)
    assert replacement["id"] != d["id"] and not replacement["duplicate"]
    assert doc(c)["id"] == replacement["id"]


def test_memory_archival_preserves_history_and_hides_model_profile(signed_in):
    c = signed_in
    r = c.post("/api/memories", json={"content": "original preference", "kind": "feedback"}).json()
    assert (
        c.patch(
            "/api/memories/" + r["id"],
            json={"content": "edited preference", "kind": "feedback", "revision": 1},
        ).status_code
        == 200
    )
    assert c.delete("/api/memories/" + r["id"]).headers["x-role-ai-removal"] == "archived"
    assert c.get("/api/memories").json() == []
    assert c.get("/api/memories/search?q=preference").json()["memories"] == []
    assert "preference" not in c.get("/api/memories/export").text
    with c.app.state.database_factory() as db:
        assert db.get(Memory, r["id"]).content == "edited preference"
        assert (
            db.scalar(select(MemoryHistory).where(MemoryHistory.memory_id == r["id"])).content
            == "original preference"
        )


def test_new_conversation_fixed_scope_legacy_conversation_never_auto_moved(signed_in):
    c = signed_in
    uid = c.get("/api/auth/me").json()["user"]["id"]
    project(c, "A")
    cid = c.post("/api/conversations", json={"project": "A"}).json()["id"]
    assert (
        c.post(
            f"/api/conversations/{cid}/chat", json={"content": "synthetic", "project": "A"}
        ).status_code
        == 200
    )
    assert (
        c.post(f"/api/conversations/{cid}/chat", json={"content": "synthetic"}).status_code == 409
    )
    with c.app.state.database_factory() as db:
        old = Conversation(user_id=uid, title="A only in title")
        db.add(old)
        db.commit()
        oldid = old.id
    assert c.get(f"/api/conversations/{oldid}/messages").status_code == 200
    assert (
        c.post(
            f"/api/conversations/{oldid}/chat", json={"content": "synthetic", "project": "A"}
        ).status_code
        == 409
    )
    with c.app.state.database_factory() as db:
        assert db.get(Conversation, oldid).title == "A only in title"
        assert db.get(ConversationBinding, oldid) is None


def test_grant_revoke_blocks_old_history_and_model_calls(signed_in):
    c = signed_in
    a = doc(c, "A")
    b = project(c, "B")
    gid = share(c, a, b)
    cid = c.post("/api/conversations", json={"project": "B"}).json()["id"]
    assert (
        c.post(
            f"/api/conversations/{cid}/chat",
            json={"content": "car", "project": "B", "document_ids": [a["id"]]},
        ).status_code
        == 200
    )
    calls = []

    class Capture:
        async def complete(self, messages):
            calls.append(messages)
            return ModelReply("synthetic", "mock", "mock", simulated=True)

    c.app.state.providers.providers["mock"] = Capture()
    c.delete(f"/api/projects/{b}/read-grants/{gid}")
    assert (
        c.post(
            f"/api/conversations/{cid}/chat", json={"content": "continue", "project": "B"}
        ).status_code
        == 409
    )
    assert calls == []
    with c.app.state.database_factory() as db:
        assert list(
            db.scalars(select(ConversationSource).where(ConversationSource.conversation_id == cid))
        )
    # Owner archive remains readable; it is not reused as a new Agent context.
    assert len(c.get(f"/api/conversations/{cid}/messages").json()) == 2


def test_semantic_filters_before_embedding_and_rechecks_after_inference(signed_in):
    from test_pdf_semantic import configure

    c = signed_in
    a = doc(c, "A")
    b = project(c, "B")
    calls = configure(c)
    body = {"query": "car", "document_ids": [a["id"]], "project": "B"}
    assert c.post("/api/documents/semantic", json=body).status_code == 404
    assert calls == []
    gid = share(c, a, b)
    assert c.post("/api/documents/semantic", json=body).status_code == 200
    calls.clear()

    uid = c.get("/api/auth/me").json()["user"]["id"]

    def handler(request):
        data = json.loads(request.content)
        calls.append(request.url.path)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["embedding"]})
        with c.app.state.database_factory() as db:
            ScopeRepository(db).revoke_read(uid, gid)
            db.commit()
        return httpx.Response(
            200, json={"model": "test-embed:latest", "embeddings": [[1, 0] for _ in data["input"]]}
        )

    c.app.state.semantic.transport = httpx.MockTransport(handler)
    assert c.post("/api/documents/semantic", json=body).status_code == 409


def test_extraction_ignores_model_project_and_rejects_legacy_before_model(signed_in):
    c = signed_in
    cid = c.post("/api/conversations", json={}).json()["id"]
    c.post(f"/api/conversations/{cid}/chat", json={"content": "synthetic preference"})
    mid = c.get(f"/api/conversations/{cid}/messages").json()[0]["id"]

    class Extractor:
        async def complete(self, messages):
            return ModelReply(
                '{"memories":[{"content":"synthetic candidate","project":"other","kind":"user"}]}',
                "ollama",
                "mock",
            )

    c.app.state.providers.providers["ollama"] = Extractor()
    r = c.post("/api/memories/extract", json={"source_message_id": mid})
    assert r.status_code == 201 and r.json()[0]["project"] == ""
    assert c.post("/api/memories/" + r.json()[0]["id"] + "/confirm").status_code == 200
    with c.app.state.database_factory() as db:
        row = db.scalar(select(Memory).where(Memory.id == r.json()[0]["id"]))
        assert resource_for(db, "memory", row.id).reason == "verified_bound_message"


def test_archive_backup_restore_keeps_tombstone_and_original(signed_in, tmp_path):
    c = signed_in
    d = doc(c)
    c.delete("/api/documents/" + d["id"])
    settings = c.app.state.settings.model_copy(update={"storage_dir": str(tmp_path / "artifacts")})
    create_backup(settings, tmp_path / "backup", offline=True)
    restore_backup(tmp_path / "backup", tmp_path / "restored")
    engine, factory = build_database(f"sqlite:///{tmp_path / 'restored/database.sqlite'}")
    try:
        with factory() as db:
            assert db.get(Document, d["id"]).content == "car synthetic secret"
            assert db.get(SourceArchive, resource_for(db, "document", d["id"]).id) is not None
    finally:
        engine.dispose()


def test_archive_in_native_project_cannot_be_used_as_cross_project_delete_oracle(signed_in):
    c = signed_in
    d = doc(c, "A")
    project(c, "B")
    path = "/api/documents/" + d["id"]
    assert c.delete(path, params={"project": "A"}).status_code == 204
    assert c.delete(path, params={"project": "B"}).status_code == 404
    assert c.delete(path, params={"project": "A"}).status_code == 204


def test_chat_drops_result_if_grant_revoked_while_model_is_running(signed_in):
    c = signed_in
    d = doc(c, "A")
    b = project(c, "B")
    gid = share(c, d, b)
    uid = c.get("/api/auth/me").json()["user"]["id"]
    cid = c.post("/api/conversations", json={"project": "B"}).json()["id"]

    class Revoker:
        async def complete(self, messages):
            with c.app.state.database_factory() as db:
                ScopeRepository(db).revoke_read(uid, gid)
                db.commit()
            return ModelReply("private response withheld", "mock", "mock", simulated=True)

    c.app.state.providers.providers["mock"] = Revoker()
    response = c.post(
        f"/api/conversations/{cid}/chat",
        json={"content": "car", "project": "B", "document_ids": [d["id"]]},
    )
    assert response.status_code == 409 and "private response" not in response.text
    assert c.get(f"/api/conversations/{cid}/messages").json() == []


def test_other_user_and_missing_csrf_cannot_grant_or_archive(signed_in):
    c = signed_in
    d = doc(c, "A")
    b = project(c, "B")
    c.headers.pop("X-CSRF-Token")
    assert (
        c.post(
            f"/api/projects/{b}/read-grants",
            json={"kind": "document", "resource_id": d["id"], "version": "a" * 64},
        ).status_code
        == 403
    )
    assert c.delete("/api/documents/" + d["id"], params={"project": "A"}).status_code == 403
    r = c.post(
        "/api/auth/register",
        json={"email": "second@test.invalid", "password": "synthetic-password-123"},
    )
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.get("/api/projects").json()["projects"] == []
    assert c.delete("/api/documents/" + d["id"], params={"project": "A"}).status_code == 404
    assert (
        c.post(
            f"/api/projects/{b}/read-grants",
            json={"kind": "document", "resource_id": d["id"], "version": "a" * 64},
        ).status_code
        == 404
    )


def test_conversation_metadata_roundtrips_exact_project_and_stale_state(signed_in):
    c = signed_in
    item = doc(c)
    created = c.post("/api/conversations", json={"project": " A "})
    assert created.status_code == 201
    record = created.json()
    assert record["project"] == " A " and record["scope_state"] == "bound"
    listing = {r["id"]: r for r in c.get("/api/conversations").json()}
    assert listing[record["id"]]["project"] == " A "
    assert listing[record["id"]]["scope_state"] == "bound"
    c.delete("/api/documents/" + item["id"])
    listing = {r["id"]: r for r in c.get("/api/conversations").json()}
    assert listing[record["id"]]["project"] == " A "
    assert listing[record["id"]]["scope_state"] == "archive"
