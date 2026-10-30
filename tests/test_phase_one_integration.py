"""Phase 1 acceptance on synthetic rows/files, independent assertions, no real models."""

import asyncio
import hashlib
import pytest
from role_ai.config import Settings
from role_ai.models import ProjectReadGrant, Document, TaskApproval
from role_ai.task_approvals import ApprovalService, ApprovalDenied
from role_ai.task_checkpoints import TaskLifecycle, TaskCheckpointStore
from role_ai.task_resources import TaskRuntime, AttemptResult, RuntimeFailure
from role_ai.contract_execution import TaskToolDispatcher
from role_ai.task_backup import create_task_backup, restore_task_backup, verify_task_backup
from role_ai.backup import BackupError, verify_backup
from role_ai.database import build_database
from test_scope_permissions import contract, reference

pytest_plugins = ("test_scope_permissions",)


@pytest.fixture
def flow(access, tmp_path):
    db, scope, rows, versions = access
    settings = Settings(
        _env_file=None,
        database_url=str(db.bind.url),
        coding_root=str(tmp_path / "jobs"),
        storage_dir=str(tmp_path / "artifacts"),
    )
    ref = reference(versions)
    body = contract(
        db,
        rows["doc"].project_id,
        ref,
        allowed_tools=("documents.read",),
        approval_reference="phase-one-approval",
    )
    life = TaskLifecycle(db, settings)
    life.create("a" * 64, body)
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    queued = life.queue("a" * 64, "task", 0)
    return db, scope, rows, versions, settings, body, ref, life, queued


def test_full_phase_one_contract_gate_tool_runtime_independent_assertion_and_completion(flow):
    db, _, _, _, settings, body, ref, life, queued = flow
    digest = life.owned("a" * 64, "task")["state"].contract_digest
    manager = TaskRuntime(db, settings)
    dispatcher = TaskToolDispatcher(db, settings)

    async def operation():
        return AttemptResult(dispatcher.dispatch("a" * 64, body, "documents.read", ref))

    with life.claim("a" * 64, "task", queued["state"].revision) as running:
        result = asyncio.run(
            manager.attempt("a" * 64, "task", "documents.read", ref, "read", operation)
        )
        # Trusted deterministic verifier, independently checks actual bytes against approved version.
        observed = hashlib.sha256(result["content"].encode()).hexdigest()
        assert observed == ref.version and result["resource_id"] == "doc"
        verifying = life.store.transition("u1", "task", running["state"].revision, "verifying")
        evidence = {
            "contract_digest": digest,
            "candidate_digest": observed,
            "exit_code": 0,
            "tests": 1,
            "runner": "independent",
        }
        assert (
            life.complete("a" * 64, "task", verifying["state"].revision, evidence)["state"].status
            == "succeeded"
        )
    assert body == life.owned("a" * 64, "task")["contract"]
    assert db.get(Document, "doc").content == "synthetic"


def test_prompt_text_and_approval_id_cannot_grant_another_tool(flow):
    db, _, _, _, settings, body, ref, life, queued = flow
    calls = []
    dispatcher = TaskToolDispatcher(db, settings, sandbox_handler=lambda *args: calls.append(args))
    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(ApprovalDenied):
            dispatcher.dispatch("a" * 64, body, "coding.sandbox_run", ref, {"command": "pytest"})
        forged = body.model_copy(
            update={
                "permissions": ("read", "sandbox_run"),
                "allowed_tools": ("documents.read", "coding.sandbox_run"),
            }
        )
        with pytest.raises(ApprovalDenied):
            dispatcher.dispatch("a" * 64, forged, "documents.read", ref)
    assert not calls
    assert db.get(TaskApproval, "phase-one-approval").revoked_at is None


def test_same_user_cross_project_read_requires_exact_current_grant_and_revoke_blocks_runtime(flow):
    db, scope, rows, versions, settings, _, ref, _, _ = flow
    target = rows["derived"].project_id
    grant = scope.grant_read("u1", versions["doc"].id, target)
    db.commit()
    body = contract(
        db,
        target,
        ref,
        task_id="shared-task",
        approval_reference="shared-approval",
        allowed_tools=("documents.read",),
    )
    life = TaskLifecycle(db, settings)
    life.create("a" * 64, body)
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    queued = life.queue("a" * 64, "shared-task", 0)
    manager = TaskRuntime(db, settings)

    async def revoked():
        scope.revoke_read("u1", grant.id)
        db.commit()
        return AttemptResult("discard revoked content")

    with life.claim("a" * 64, "shared-task", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="permission_denied"):
            asyncio.run(
                manager.attempt("a" * 64, "shared-task", "documents.read", ref, "shared", revoked)
            )
    assert life.owned("a" * 64, "shared-task")["state"].status == "blocked"
    assert db.get(ProjectReadGrant, grant.id).revoked_at is not None
    assert db.get(Document, "doc").content == "synthetic"


def test_other_user_cannot_create_a_checkpoint_for_existing_contract(flow):
    _, _, _, _, _, body, _, life, _ = flow
    before = list(life.store.files.root.rglob("checkpoint.json"))
    with pytest.raises(ApprovalDenied):
        life.create("b" * 64, body)
    assert list(life.store.files.root.rglob("checkpoint.json")) == before


def prepare_pending(flow):
    db, _, _, _, settings, body, ref, life, queued = flow
    manager = TaskRuntime(db, settings)
    with life.claim("a" * 64, "task", queued["state"].revision):
        value = life.owned("a" * 64, "task")
        ledger = manager.load(value)
        ledger["steps"] = 1
        ledger["charged_tokens"] = 5120
        ledger["pending"] = {"id": "crashed", "model": True, "reserved_tokens": 5120}
        manager.save(body, ledger)
    return manager


def test_offline_companion_restores_rows_and_pending_checkpoints_inactive_without_replay(
    flow, tmp_path
):
    db, _, _, _, settings, body, _, life, _ = flow
    manager = prepare_pending(flow)
    original_doc = db.get(Document, "doc").content
    root = life.store.files.root
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.json")}
    target = tmp_path / "bundle"
    assert create_task_backup(settings, target, offline=True)["checkpoint_count"] == 1
    verify_task_backup(target)
    verify_backup(target / "application")
    restored = tmp_path / "new-parent" / "restored"
    result = restore_task_backup(target, restored)
    assert result["activation"] == "not_activated" and result["automatic_replay"] is False
    copy = TaskCheckpointStore(restored / "task-checkpoints")
    state = copy.load("u1", body.task_id)
    assert state["state"].status == "interrupted"
    ledger = copy.files.read(copy.directory("u1", "task") / "runtime.json")
    assert ledger["pending"]["id"] == "crashed" and ledger["charged_tokens"] == 5120
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.json")} == before
    restored_engine, factory = build_database(
        f"sqlite:///{restored / 'application/database.sqlite'}"
    )
    with factory() as other:
        assert other.get(Document, "doc").content == original_doc
        assert (
            other.get(TaskApproval, "phase-one-approval").contract_digest
            == state["state"].contract_digest
        )
    restored_engine.dispose()
    assert manager.status("a" * 64, "task")["pending"]["id"] == "crashed"


def test_active_execution_lease_prevents_companion_backup_and_preserves_source(flow, tmp_path):
    _, _, _, _, settings, _, _, life, queued = flow
    target = tmp_path / "busy-backup"
    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(BackupError):
            create_task_backup(settings, target, offline=True)
    assert not target.exists()
    assert not list(tmp_path.glob(".task-backup-*"))


@pytest.mark.parametrize("damage", ["common", "application", "extra"])
def test_companion_corruption_is_rejected_without_overwriting_restore_target(
    flow, tmp_path, damage
):
    _, _, _, _, settings, _, _, life, _ = flow
    prepare_pending(flow)
    bundle = tmp_path / "bundle"
    create_task_backup(settings, bundle, offline=True)
    if damage == "common":
        path = next((bundle / "task-checkpoints").rglob("runtime.json"))
        path.write_text("corrupt")
    elif damage == "application":
        (bundle / "application/database.sqlite").write_bytes(b"invalid")
    else:
        (bundle / "unexpected.env").write_text("must-not-copy")
    with pytest.raises(BackupError):
        verify_task_backup(bundle)
    target = tmp_path / "restore"
    with pytest.raises(BackupError):
        restore_task_backup(bundle, target)
    assert not target.exists()


def test_companion_requires_offline_and_new_nonoverlapping_destinations(flow, tmp_path):
    _, _, _, _, settings, _, _, life, _ = flow
    target = tmp_path / "backup"
    with pytest.raises(BackupError):
        create_task_backup(settings, target)
    target.mkdir()
    (target / "keep").write_text("preserve")
    with pytest.raises(BackupError):
        create_task_backup(settings, target, offline=True)
    assert (target / "keep").read_text() == "preserve"
    with pytest.raises(BackupError):
        create_task_backup(settings, life.store.files.root / "inside", offline=True)


@pytest.mark.parametrize(
    "path", ["role_ai/task_backup.py", "role_ai/backup.py", "role_ai/gpu_resources.py"]
)
def test_backup_and_gpu_policy_code_are_model_read_only(path):
    from role_ai.coding_jobs import TaskCreate

    with pytest.raises(ValueError):
        TaskCreate(repository_id="repo", project_id="p", request="change", allowed_files=(path,))


def test_companion_preserves_revoked_grants_and_approval_tombstones(flow, tmp_path):
    db, scope, rows, versions, settings, body, ref, life, _ = flow
    prepare_pending(flow)
    grant = scope.grant_read("u1", versions["doc"].id, rows["derived"].project_id)
    db.commit()
    scope.revoke_read("u1", grant.id)
    db.commit()
    ApprovalService(db, settings).revoke("a" * 64, "phase-one-approval")
    db.commit()
    backup = tmp_path / "revoked-backup"
    create_task_backup(settings, backup, offline=True)
    restored = tmp_path / "revoked-restored"
    restore_task_backup(backup, restored)
    engine, factory = build_database(f"sqlite:///{restored / 'application/database.sqlite'}")
    with factory() as copy:
        assert copy.get(ProjectReadGrant, grant.id).revoked_at is not None
        assert copy.get(TaskApproval, "phase-one-approval").revoked_at is not None
        from role_ai.task_approvals import TaskApprovalGate

        assert (
            TaskApprovalGate(copy, settings).evaluate("a" * 64, body, "documents.read", ref).state
            == "denied"
        )
    engine.dispose()


def test_state_root_change_invalidates_old_approval_instead_of_resetting_budget(flow, tmp_path):
    db, _, _, _, settings, body, ref, _, _ = flow
    from role_ai.task_approvals import TaskApprovalGate

    assert (
        TaskApprovalGate(db, settings).evaluate("a" * 64, body, "documents.read", ref).state
        == "allowed"
    )
    settings.coding_root = str(tmp_path / "another-root")
    assert (
        TaskApprovalGate(db, settings).evaluate("a" * 64, body, "documents.read", ref).state
        == "denied"
    )


def test_companion_empty_task_root_does_not_create_original_job_directory(flow, tmp_path):
    _, _, _, _, settings, _, _, _, _ = flow
    settings.coding_root = str(tmp_path / "never-used")
    target = tmp_path / "empty-backup"
    assert create_task_backup(settings, target, offline=True)["checkpoint_count"] == 0
    assert not (tmp_path / "never-used").exists()
    verify_task_backup(target)


def test_companion_detects_a_writer_ignoring_offline_locks_without_repairing_source(
    flow, tmp_path, monkeypatch
):
    _, _, _, _, settings, _, _, life, _ = flow
    manager = prepare_pending(flow)
    source = manager.path("u1", "task")
    import shutil

    original = shutil.copyfile
    changed = []

    def copy(src, dst, *args, **kwargs):
        result = original(src, dst, *args, **kwargs)
        if str(src) == str(source) and not changed:
            data = life.store.files.read(source)
            data["charged_tokens"] += 1
            life.store.files.atomic(source, data)
            changed.append(True)
        return result

    monkeypatch.setattr("role_ai.task_backup.shutil.copyfile", copy)
    target = tmp_path / "racing-backup"
    with pytest.raises(BackupError):
        create_task_backup(settings, target, offline=True)
    assert not target.exists() and changed == [True]
    assert life.store.files.read(source)["charged_tokens"] == 5121
    assert not list(tmp_path.glob(".task-backup-*"))
