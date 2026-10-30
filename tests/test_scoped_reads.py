"""Read bridge proofs; route/delete semantics remain pending user decision."""

import pytest

from role_ai.scoped_reads import ScopedReadDenied, ScopedReads

pytest_plugins = ("test_scope_permissions",)


def test_bridge_defaults_to_private_inbox_and_never_all_projects(access):
    db, _, rows, _ = access
    assert ScopedReads(db, "a" * 64).visible("document") == []
    with pytest.raises(ScopedReadDenied):
        ScopedReads(db, "a" * 64).require("document", "doc")
    assert ScopedReads(db, "a" * 64, rows["doc"].project_id).require("document", "doc").id == "doc"


def test_bridge_uses_current_grant_without_copying_source(access):
    db, repo, rows, versions = access
    target = rows["derived"].project_id
    reads = ScopedReads(db, "a" * 64, target)
    with pytest.raises(ScopedReadDenied):
        reads.require("document", "doc")
    grant = repo.grant_read("u1", versions["doc"].id, target)
    db.commit()
    assert [x.id for x in reads.visible("document")] == ["doc"]
    repo.revoke_read("u1", grant.id)
    db.commit()
    assert reads.visible("document") == []


def test_bridge_refuses_other_user_and_unknown_source_kind(access):
    db, _, rows, _ = access
    with pytest.raises(ScopedReadDenied):
        ScopedReads(db, "b" * 64, rows["doc"].project_id).require("document", "doc")
    with pytest.raises(ScopedReadDenied):
        ScopedReads(db, "a" * 64).require("repository", "repo")


def test_bridge_listing_never_autoflushes_uncommitted_changes(access):
    from role_ai.models import User

    db, _, _, _ = access
    pending = User(id="pending", email="pending@test.invalid", password_hash="synthetic")
    db.add(pending)
    with pytest.raises(ScopedReadDenied):
        ScopedReads(db, "a" * 64).visible("document")
    assert pending in db.new
