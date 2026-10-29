"""P1-03: synthetic DB and authenticated-session proofs, no route/tool execution."""

import hashlib
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from role_ai.database import build_database
from role_ai.migrations import initialize_database
from role_ai.models import AuthSession, Document, Memory, Project, ScopePolicy, User
from role_ai.permissions import PermissionGate, PermissionRequest
from role_ai.project_scopes import ScopeRepository
from role_ai.scope_permissions import ScopeReadGate
from role_ai.task_contracts import ResourceRef, ReviewPolicy, TaskContract


@pytest.fixture
def access(tmp_path):
    engine, factory = build_database(f"sqlite:///{tmp_path / 'access.db'}")
    initialize_database(engine)
    with factory() as db:
        db.add_all(
            [User(id=u, email=u + "@test.invalid", password_hash="synthetic") for u in ["u1", "u2"]]
        )
        db.flush()
        db.add_all(
            [
                AuthSession(
                    token_hash=t * 64,
                    user_id=u,
                    csrf_token="synthetic",
                    expires_at=time.time() + 3600,
                )
                for t, u in [("a", "u1"), ("b", "u2")]
            ]
        )
        db.add(
            Document(
                id="doc",
                user_id="u1",
                project="A",
                filename="synthetic.txt",
                content="synthetic",
                digest=hashlib.sha256(b"synthetic").hexdigest(),
            )
        )
        db.add(
            Memory(
                id="derived",
                user_id="u1",
                project="B",
                kind="knowledge",
                content="synthetic derived",
                revision=1,
                state="confirmed",
            )
        )
        db.flush()
        repo = ScopeRepository(db)
        rows = {
            k: repo.register_source("u1", kind, k)
            for k, kind in [("doc", "document"), ("derived", "memory")]
        }
        versions = {k: repo.record_current_version("u1", v.id) for k, v in rows.items()}
        db.commit()
        yield db, repo, rows, versions
    engine.dispose()


def contract(db, project_id, resource, **updates):
    fields = dict(
        task_id="task",
        user_id="u1",
        scope="project" if project_id else "personal",
        project_id=project_id,
        agent_id="knowledge",
        goal="Read permitted evidence",
        acceptance_criteria=("No cross-project leak",),
        allowed_resources=(resource,),
        allowed_tools=("documents.read", "memory.read"),
        permissions=("read",),
        risk_level="low",
        review_policy=ReviewPolicy(policy_id="read-policy", version=1),
        policy_version=db.get(ScopePolicy, "u1", populate_existing=True).generation,
    )
    fields.update(updates)
    return TaskContract(**fields)


def reference(versions, key="doc"):
    return ResourceRef(
        kind="document" if key == "doc" else "memory",
        resource_id=key,
        version=versions[key].version,
    )


def test_owned_exact_read_and_permission_gate_adapter(access):
    db, _, rows, versions = access
    ref = reference(versions)
    body = contract(db, rows["doc"].project_id, ref)
    assert PermissionGate().evaluate_scoped_read(db, "a" * 64, body, ref).state == "allowed"
    assert (
        PermissionGate().evaluate(PermissionRequest("u1", "documents.read", "execute", "doc")).state
        == "denied"
    )


@pytest.mark.parametrize(
    "change",
    [
        "owner",
        "project",
        "personal",
        "version",
        "missing",
        "tools",
        "permissions",
        "policy",
        "unchecked",
        "resource",
    ],
)
def test_contract_cannot_enlarge_current_data_authority(access, change):
    db, _, rows, versions = access
    ref = reference(versions)
    body = contract(db, rows["doc"].project_id, ref)
    if change == "owner":
        body = body.model_copy(update={"user_id": "u2"})
    elif change == "project":
        body = body.model_copy(update={"project_id": rows["derived"].project_id})
    elif change == "personal":
        body = body.model_copy(update={"project_id": None, "scope": "personal"})
    elif change == "version":
        ref = ref.model_copy(update={"version": "0" * 64})
        body = body.model_copy(update={"allowed_resources": (ref,)})
    elif change == "missing":
        ref = ref.model_copy(update={"resource_id": "missing"})
        body = body.model_copy(update={"allowed_resources": (ref,)})
    elif change == "tools":
        body = body.model_copy(update={"allowed_tools": ("coding.run",)})
    elif change == "permissions":
        body = body.model_copy(
            update={"permissions": ("sandbox_run",), "approval_reference": "invented"}
        )
    elif change == "policy":
        body = body.model_copy(update={"policy_version": 999})
    elif change == "unchecked":
        body = body.model_copy(update={"scope": "project", "project_id": None})
    else:
        body = body.model_copy(update={"allowed_resources": ()})
    assert ScopeReadGate(db).evaluate("a" * 64, body, ref).state == "denied"


@pytest.mark.parametrize("session", ["b" * 64, "missing", "c" * 64, None])
def test_claimed_actor_does_not_replace_authenticated_session(access, session):
    db, _, rows, versions = access
    ref = reference(versions)
    assert (
        ScopeReadGate(db).evaluate(session, contract(db, rows["doc"].project_id, ref), ref).state
        == "denied"
    )


@pytest.mark.parametrize(
    "state", ["expired", "deleted", "archived", "uncommitted", "database_error"]
)
def test_session_project_and_db_changes_fail_closed(access, state, monkeypatch):
    db, _, rows, versions = access
    ref = reference(versions)
    body = contract(db, rows["doc"].project_id, ref)
    if state == "expired":
        db.get(AuthSession, "a" * 64).expires_at = 1
        db.commit()
    elif state == "deleted":
        db.delete(db.get(AuthSession, "a" * 64))
        db.commit()
    elif state == "archived":
        db.get(Project, rows["doc"].project_id).state = "archived"
        db.commit()
    elif state == "uncommitted":
        db.add(User(id="pending", email="pending@test.invalid", password_hash="synthetic"))
    else:
        from sqlalchemy.exc import OperationalError

        monkeypatch.setattr(
            db,
            "get",
            lambda *a, **k: (_ for _ in ()).throw(
                OperationalError("private", {}, Exception("private"))
            ),
        )
    decision = ScopeReadGate(db).evaluate("a" * 64, body, ref)
    assert decision.state == "denied" and "private" not in decision.reason


def test_read_grant_and_policy_generation_are_rechecked(access):
    db, repo, rows, versions = access
    ref = reference(versions)
    target = rows["derived"].project_id
    old = contract(db, target, ref)
    assert ScopeReadGate(db).evaluate("a" * 64, old, ref).state == "denied"
    grant = repo.grant_read("u1", versions["doc"].id, target)
    db.commit()
    assert ScopeReadGate(db).evaluate("a" * 64, old, ref).state == "denied"
    body = contract(db, target, ref)
    assert ScopeReadGate(db).evaluate("a" * 64, body, ref).state == "allowed"
    with Session(db.get_bind()) as other:
        ScopeRepository(other).revoke_read("u1", grant.id)
        other.commit()
    assert ScopeReadGate(db).evaluate("a" * 64, body, ref).state == "denied"
    assert ScopeReadGate(db).evaluate("a" * 64, contract(db, target, ref), ref).state == "denied"


def test_revoked_dependency_blocks_derived_memory_even_with_new_grant(access):
    db, repo, rows, versions = access
    target = rows["derived"].project_id
    grant = repo.grant_read("u1", versions["doc"].id, target)
    repo.record_provenance("u1", versions["derived"].id, versions["doc"].id, grant.id)
    db.commit()
    ref = reference(versions, "derived")
    assert ScopeReadGate(db).evaluate("a" * 64, contract(db, target, ref), ref).state == "allowed"
    repo.revoke_read("u1", grant.id)
    repo.grant_read("u1", versions["doc"].id, target)
    db.commit()
    assert ScopeReadGate(db).evaluate("a" * 64, contract(db, target, ref), ref).state == "denied"


def test_gate_does_not_reclassify_or_write_content(access):
    db, _, rows, versions = access
    before = list(db.execute(text("SELECT id,content,digest,project FROM documents")))
    ref = reference(versions)
    assert (
        ScopeReadGate(db).evaluate("a" * 64, contract(db, rows["doc"].project_id, ref), ref).state
        == "allowed"
    )
    assert list(db.execute(text("SELECT id,content,digest,project FROM documents"))) == before
    assert not db.new and not db.dirty and not db.deleted
    assert db.scalar(select(ScopePolicy.generation).where(ScopePolicy.user_id == "u1")) == 1


def test_personal_inbox_is_explicit_and_not_a_project_wildcard(access):
    db, repo, rows, _ = access
    digest = hashlib.sha256(b"personal").hexdigest()
    db.add(
        Document(
            id="personal",
            user_id="u1",
            project="",
            filename="private.txt",
            content="personal",
            digest=digest,
        )
    )
    db.commit()
    ref = ResourceRef(kind="document", resource_id="personal", version=digest)
    # A contract cannot register a legacy source by declaring it.
    assert ScopeReadGate(db).evaluate("a" * 64, contract(db, None, ref), ref).state == "denied"
    resource = repo.register_source("u1", "document", "personal")
    repo.record_current_version("u1", resource.id)
    db.commit()
    assert ScopeReadGate(db).evaluate("a" * 64, contract(db, None, ref), ref).state == "allowed"
    assert (
        ScopeReadGate(db).evaluate("a" * 64, contract(db, rows["doc"].project_id, ref), ref).state
        == "denied"
    )


def test_repository_reference_cannot_become_a_data_or_execution_capability(access):
    db, _, rows, _ = access
    ref = ResourceRef(
        kind="repository", resource_id="repo", version="a" * 40, paths=("src/main.py",)
    )
    body = contract(
        db,
        rows["doc"].project_id,
        ref,
        allowed_tools=("coding.read",),
        permissions=("read", "sandbox_run"),
        approval_reference="invented",
    )
    assert ScopeReadGate(db).evaluate("a" * 64, body, ref).state == "denied"
