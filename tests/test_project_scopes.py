"""Synthetic SQLite only; no live migration, network, model, or legacy API rewiring."""

import hashlib

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from role_ai.database import build_database
from role_ai.migrations import initialize_database
from role_ai.models import (
    Document, Memory, MemoryProvenance, Project, ProjectReadGrant, ResourceVersion,
    ScopePolicy, User,
)
from role_ai.project_scopes import ScopeError, ScopeRepository
from role_ai.task_contracts import ResourceRef


@pytest.fixture
def scoped(tmp_path):
    engine, factory = build_database(f"sqlite:///{tmp_path / 'scope.db'}")
    initialize_database(engine)
    with factory() as db:
        db.add_all([User(id=u, email=u + "@test.invalid", password_hash="synthetic", is_owner=True)
                    for u in ("u1", "u2")])
        db.flush()
        db.add_all([
            Document(id="d1", user_id="u1", filename="one.txt", project="A", content="original",
                     digest=hashlib.sha256(b"original").hexdigest()),
            Document(id="private", user_id="u1", filename="private.txt", project="", content="private",
                     digest=hashlib.sha256(b"private").hexdigest()),
            Document(id="other", user_id="u2", filename="two.txt", project="A", content="other",
                     digest=hashlib.sha256(b"other").hexdigest()),
            Memory(id="m1", user_id="u1", kind="knowledge", project="B", content="derived",
                   state="confirmed", revision=1),
            Memory(id="m2", user_id="u1", kind="knowledge", project="B", content="descendant",
                   state="confirmed", revision=1),
        ])
        db.commit()
        repo = ScopeRepository(db)
        resources = {key: repo.register_source(owner, kind, key) for owner, kind, key in (
            ("u1", "document", "d1"), ("u1", "document", "private"),
            ("u2", "document", "other"), ("u1", "memory", "m1"), ("u1", "memory", "m2"))}
        versions = {key: repo.record_current_version(row.user_id, row.id)
                    for key, row in resources.items()}
        db.commit()
        yield db, repo, resources, versions
    engine.dispose()


def test_default_private_personal_scope_and_same_name_users(scoped):
    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    assert repo.can_read_version("u1", resources["d1"].project_id, versions["d1"].id)
    assert not repo.can_read_version("u1", b, versions["d1"].id)
    assert not repo.can_read_version("u2", resources["other"].project_id, versions["d1"].id)
    assert repo.can_read_version("u1", None, versions["private"].id)
    assert not repo.can_read_version("u1", b, versions["private"].id)
    assert not repo.can_read_version("u1", None, versions["d1"].id)
    assert not repo.can_read_version("u1", "missing-project", versions["private"].id)
    assert not repo.can_read_version("u1", b, "missing-version")
    assert db.scalar(select(func.count()).select_from(ProjectReadGrant)) == 0


def test_explicit_exact_read_grant_revoke_and_generation(scoped):
    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    grant = repo.grant_read("u1", versions["d1"].id, b)
    assert repo.grant_read("u1", versions["d1"].id, b).id == grant.id
    assert grant.permission == "read" and repo.can_read_version("u1", b, versions["d1"].id)
    generation = db.get(ScopePolicy, "u1", populate_existing=True).generation
    repo.revoke_read("u1", grant.id)
    assert db.get(ScopePolicy, "u1").generation == generation + 1
    assert not repo.can_read_version("u1", b, versions["d1"].id)
    repo.revoke_read("u1", grant.id)
    assert db.get(ScopePolicy, "u1").generation == generation + 1
    assert db.get(ProjectReadGrant, grant.id) is not None


def test_version_change_never_inherits_old_share(scoped):
    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    repo.grant_read("u1", versions["d1"].id, b)
    document = db.get(Document, "d1")
    document.content = "changed"
    document.digest = hashlib.sha256(b"changed").hexdigest()
    db.flush()
    changed = repo.record_current_version("u1", resources["d1"].id)
    assert changed.id != versions["d1"].id
    assert not repo.can_read_version("u1", b, changed.id)
    assert not repo.can_read_version("u1", b, versions["d1"].id)
    assert repo.can_read_version("u1", resources["d1"].project_id, changed.id)


def test_revoked_ancestor_blocks_derived_chain_without_deleting_records(scoped):
    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    grant = repo.grant_read("u1", versions["d1"].id, b)
    first = repo.record_provenance("u1", versions["m1"].id, versions["d1"].id, grant.id)
    second = repo.record_provenance("u1", versions["m2"].id, versions["m1"].id)
    assert repo.can_read_version("u1", b, versions["m2"].id)
    repo.revoke_read("u1", grant.id)
    assert not repo.can_read_version("u1", b, versions["m1"].id)
    assert not repo.can_read_version("u1", b, versions["m2"].id)
    assert db.get(MemoryProvenance, first.id) and db.get(MemoryProvenance, second.id)
    assert db.get(Memory, "m1").content == "derived"
    assert db.get(Memory, "m2").content == "descendant"
    # An explicit new grant restores the source, not old grant-dependent derived content.
    replacement = repo.grant_read("u1", versions["d1"].id, b)
    assert replacement.id != grant.id
    assert repo.can_read_version("u1", b, versions["d1"].id)
    assert not repo.can_read_version("u1", b, versions["m2"].id)


def test_cycles_and_cross_user_provenance_are_rejected_by_database(scoped):
    db, repo, _, versions = scoped
    repo.record_provenance("u1", versions["m2"].id, versions["m1"].id)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(MemoryProvenance(derived_version_id=versions["m1"].id,
                               source_version_id=versions["m2"].id))
        db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(MemoryProvenance(derived_version_id=versions["m1"].id,
                               source_version_id=versions["other"].id))
        db.flush()


@pytest.mark.parametrize("damage", ["scope_owner", "scope_project", "grant_owner", "write_grant", "unversioned"])
def test_raw_database_cannot_bypass_owner_or_read_version_boundaries(scoped, damage):
    db, _, resources, versions = scoped
    with pytest.raises(IntegrityError), db.begin_nested():
        if damage == "scope_owner":
            db.execute(text("UPDATE scoped_resources SET user_id='u2' WHERE document_id='d1'"))
        elif damage == "scope_project":
            resources["d1"].project_id = resources["other"].project_id
        elif damage == "unversioned":
            db.add(ResourceVersion(resource_id=resources["d1"].id, version="latest",
                                   content_digest="a" * 64))
        else:
            db.add(ProjectReadGrant(version_id=versions["d1"].id,
                project_id=resources["other"].project_id if damage == "grant_owner" else resources["m1"].project_id,
                granted_by="u1", permission="write" if damage == "write_grant" else "read"))
        db.flush()


@pytest.mark.parametrize("table", ["scope_resource_versions", "scope_memory_provenance", "scope_read_grants"])
def test_scope_history_cannot_be_erased_or_revoked_grants_revived(scoped, table):
    db, repo, resources, versions = scoped
    grant = repo.grant_read("u1", versions["d1"].id, resources["m1"].project_id)
    edge = repo.record_provenance("u1", versions["m1"].id, versions["d1"].id, grant.id)
    repo.revoke_read("u1", grant.id)
    ids = {"scope_resource_versions": versions["d1"].id,
           "scope_memory_provenance": edge.id, "scope_read_grants": grant.id}
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": ids[table]})
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("UPDATE scope_read_grants SET revoked_at=NULL WHERE id=:id"), {"id": grant.id})


def test_changed_memory_same_revision_and_inactive_sources_fail_closed(scoped):
    db, repo, resources, versions = scoped
    memory = db.get(Memory, "m1")
    memory.content = "changed without a revision"
    db.flush()
    with pytest.raises(ScopeError):
        repo.record_current_version("u1", resources["m1"].id)
    assert not repo.can_read_version("u1", resources["m1"].project_id, versions["m1"].id)
    memory.revision += 1
    db.flush()
    changed = repo.record_current_version("u1", resources["m1"].id)
    assert repo.can_read_version("u1", resources["m1"].project_id, changed.id)
    memory.state = "candidate"
    db.flush()
    assert not repo.can_read_version("u1", resources["m1"].project_id, changed.id)
    memory.state, memory.expires_at = "confirmed", 1
    db.flush()
    assert not repo.can_read_version("u1", resources["m1"].project_id, changed.id, now=2)


def test_explicit_alias_mapping_and_p101_resource_reference_compatibility(scoped):
    db, repo, resources, versions = scoped
    names = ["프로젝트" * 15, "A", "a", " A "]
    rows = [repo.create_project("u1", name) for name in names]
    assert len({r.id for r in rows}) == 4
    assert all(repo.create_project("u1", name).id == r.id for name, r in zip(names, rows))
    assert all(len(r.id) <= 64 for r in rows)
    ref = ResourceRef(kind="document", resource_id="d1", version=versions["d1"].version)
    assert ref.version == db.get(Document, "d1").digest
    with pytest.raises(ScopeError):
        repo.register_source("u2", "document", "d1")
    with pytest.raises(ScopeError):
        repo.grant_read("u2", versions["d1"].id, resources["other"].project_id)


def test_scope_moves_and_archived_target_invalidate_eligibility(scoped):
    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    repo.grant_read("u1", versions["d1"].id, b)
    db.get(Project, b).state = "archived"
    db.flush()
    assert not repo.can_read_version("u1", b, versions["d1"].id)
    db.get(Document, "d1").project = "other-label"
    db.flush()
    assert not repo.can_read_version("u1", resources["d1"].project_id, versions["d1"].id)


def test_memory_revision_carries_revoked_provenance_without_reenabling_it(scoped):
    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    grant = repo.grant_read("u1", versions["d1"].id, b)
    repo.record_provenance("u1", versions["m1"].id, versions["d1"].id, grant.id)
    repo.revoke_read("u1", grant.id)
    memory = db.get(Memory, "m1")
    memory.revision, memory.content = 2, "edited derived memory"
    db.flush()
    current = repo.record_current_version("u1", resources["m1"].id)
    inherited = db.scalar(select(MemoryProvenance).where(
        MemoryProvenance.derived_version_id == current.id))
    assert inherited.grant_id == grant.id
    assert not repo.can_read_version("u1", b, current.id)
    assert db.get(Memory, "m1").content == "edited derived memory"


def test_missing_provenance_on_raw_new_version_never_restores_eligibility(scoped):
    from role_ai.project_scopes import source_fingerprint

    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    grant = repo.grant_read("u1", versions["d1"].id, b)
    repo.record_provenance("u1", versions["m1"].id, versions["d1"].id, grant.id)
    memory = db.get(Memory, "m1")
    memory.revision = 2
    db.flush()
    version, digest = source_fingerprint(memory)
    raw = ResourceVersion(resource_id=resources["m1"].id, version=version, content_digest=digest)
    db.add(raw)
    db.flush()
    assert not repo.can_read_version("u1", b, raw.id)


def test_archived_origin_blocks_its_shared_versions(scoped):
    db, repo, resources, versions = scoped
    b = resources["m1"].project_id
    repo.grant_read("u1", versions["d1"].id, b)
    db.get(Project, resources["d1"].project_id).state = "archived"
    db.flush()
    assert not repo.can_read_version("u1", b, versions["d1"].id)


def test_deep_provenance_is_bounded_and_denied_without_recursion_failure(scoped):
    db, repo, resources, _ = scoped
    for i in range(67):
        db.add(Memory(id=f"deep-{i}", user_id="u1", project="B", kind="knowledge",
                      content=f"synthetic-{i}", state="confirmed", revision=1))
    db.flush()
    chain = [repo.record_current_version("u1", repo.register_source(
        "u1", "memory", f"deep-{i}").id) for i in range(67)]
    for i in range(1, len(chain)):
        db.add(MemoryProvenance(derived_version_id=chain[i].id, source_version_id=chain[i-1].id))
    db.flush()
    assert repo.can_read_version("u1", resources["m1"].project_id, chain[0].id)
    assert not repo.can_read_version("u1", resources["m1"].project_id, chain[-1].id)


def test_revocation_from_another_session_invalidates_cached_grant(scoped):
    from sqlalchemy.orm import Session

    db, repo, resources, versions = scoped
    target = resources['m1'].project_id
    grant = repo.grant_read('u1', versions['d1'].id, target)
    grant_id, version_id = grant.id, versions['d1'].id
    db.commit()
    assert repo.can_read_version('u1', target, version_id)
    assert db.get(ProjectReadGrant, grant_id).revoked_at is None
    with Session(db.get_bind()) as other:
        ScopeRepository(other).revoke_read('u1', grant_id)
        other.commit()
    assert not repo.can_read_version('u1', target, version_id)
    assert db.get(ProjectReadGrant, grant_id, populate_existing=True).revoked_at is not None


def test_actual_source_owner_change_denies_stale_scope_metadata(scoped):
    db, repo, resources, versions = scoped
    origin = resources['d1'].project_id
    assert repo.can_read_version('u1', origin, versions['d1'].id)
    db.execute(text("UPDATE documents SET user_id='u2' WHERE id='d1'"))
    assert not repo.can_read_version('u1', origin, versions['d1'].id)
    assert not repo.can_read_version('u2', origin, versions['d1'].id)
    assert db.get(Document, 'd1', populate_existing=True).content == 'original'
