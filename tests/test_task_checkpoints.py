"""Synthetic states/checkpoints; no model execution and no user source changes."""

import json
from pathlib import Path
import pytest
from role_ai.config import Settings
from role_ai.coding_jobs import JobBusy, CodingJobError
from role_ai.task_approvals import ApprovalService, ApprovalDenied
from role_ai.task_checkpoints import TaskCheckpointStore, TaskLifecycle, TaskStateError
from test_scope_permissions import contract, reference

pytest_plugins = ("test_scope_permissions",)


@pytest.fixture
def lifecycle(access, tmp_path):
    db, _, rows, versions = access
    settings = Settings(_env_file=None, coding_root=str(tmp_path / "jobs"))
    ref = reference(versions)
    body = contract(
        db,
        rows["doc"].project_id,
        ref,
        allowed_tools=("documents.read",),
        approval_reference="approval",
    )
    service = TaskLifecycle(db, settings)
    service.create("a" * 64, body)
    return db, settings, body, service


def approved_queue(fixture):
    db, settings, body, service = fixture
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    return service.queue("a" * 64, body.task_id, 0)


def test_create_is_idempotent_but_contract_identity_is_not_replaceable(lifecycle):
    _, _, body, service = lifecycle
    assert service.create("a" * 64, body)["state"].status == "proposed"
    with pytest.raises(TaskStateError):
        service.create("a" * 64, body.model_copy(update={"goal": "changed"}))
    assert service.store.load("u1", "task")["contract"] == body


def test_state_queue_requires_actual_approval_and_owner(lifecycle):
    _, _, body, service = lifecycle
    with pytest.raises(ApprovalDenied):
        service.queue("a" * 64, "task", 0)
    with pytest.raises(CodingJobError):
        service.owned("b" * 64, "task")
    queued = approved_queue(lifecycle)
    assert queued["state"].status == "queued" and queued["state"].revision == 2
    with pytest.raises(TaskStateError):
        service.queue("a" * 64, "task", 0)


@pytest.mark.parametrize("target", ["succeeded", "verifying", "running", "failed"])
def test_proposal_cannot_skip_state_machine_or_fake_success(lifecycle, target):
    _, _, _, service = lifecycle
    with pytest.raises(TaskStateError):
        service.store.transition("u1", "task", 0, target)
    assert service.store.load("u1", "task")["state"].revision == 0


def test_claim_lock_prevents_duplicate_worker_and_crash_requires_resume(lifecycle):
    _, _, _, service = lifecycle
    queued = approved_queue(lifecycle)
    with service.claim("a" * 64, "task", queued["state"].revision) as running:
        assert running["state"].status == "running"
        with pytest.raises(JobBusy):
            with service.claim("a" * 64, "task", running["state"].revision):
                pass
        with pytest.raises(JobBusy):
            service.queue("a" * 64, "task", running["state"].revision)
    interrupted = service.owned("a" * 64, "task")
    assert interrupted["state"].status == "interrupted"
    resumed = service.queue("a" * 64, "task", interrupted["state"].revision)
    assert resumed["state"].status == "queued"


def test_persisted_running_checkpoint_is_never_automatically_replayed(lifecycle):
    _, _, _, service = lifecycle
    queued = approved_queue(lifecycle)
    running = service.store.transition("u1", "task", queued["state"].revision, "running")
    entered = []
    with pytest.raises(TaskStateError, match="explicit resume"):
        with service.claim("a" * 64, "task", running["state"].revision):
            entered.append(True)
    assert not entered
    assert service.owned("a" * 64, "task")["state"].status == "interrupted"


def test_pause_resume_and_sticky_cancel(lifecycle):
    _, _, _, service = lifecycle
    queued = approved_queue(lifecycle)
    service.control("a" * 64, "task", "pause")
    paused = service.owned("a" * 64, "task")
    assert paused["state"].status == "paused"
    queued = service.queue("a" * 64, "task", paused["state"].revision)
    with service.claim("a" * 64, "task", queued["state"].revision):
        service.control("a" * 64, "task", "cancel")
        service.control("a" * 64, "task", "pause")
        assert service.observe_control("u1", "task") == "cancel"
    cancelled = service.owned("a" * 64, "task")
    assert cancelled["state"].status == "cancelled"
    with pytest.raises((TaskStateError, ApprovalDenied)):
        service.queue("a" * 64, "task", cancelled["state"].revision)


def test_only_current_approval_and_independent_verification_can_complete(lifecycle):
    db, settings, body, service = lifecycle
    queued = approved_queue(lifecycle)
    with service.claim("a" * 64, "task", queued["state"].revision) as running:
        verifying = service.store.transition("u1", "task", running["state"].revision, "verifying")
        rev = verifying["state"].revision
        with pytest.raises(TaskStateError):
            service.complete("a" * 64, "task", rev, {"passed": True})
        evidence = {
            "contract_digest": verifying["state"].contract_digest,
            "candidate_digest": "1" * 64,
            "exit_code": 0,
            "tests": 2,
            "runner": "independent",
        }
        done = service.complete("a" * 64, "task", rev, evidence)
        assert done["state"].status == "succeeded"
    assert service.owned("a" * 64, "task")["evidence"] == evidence
    assert body.model_dump_json() == service.store.load("u1", "task")["contract"].model_dump_json()


def test_revocation_blocks_resume_and_does_not_rewrite_checkpoint(lifecycle):
    db, settings, body, service = lifecycle
    approved_queue(lifecycle)
    service.control("a" * 64, "task", "pause")
    paused = service.owned("a" * 64, "task")
    path = service.store.directory("u1", "task") / "checkpoint.json"
    before = path.read_bytes()
    ApprovalService(db, settings).revoke("a" * 64, "approval")
    db.commit()
    with pytest.raises(ApprovalDenied):
        service.queue("a" * 64, "task", paused["state"].revision)
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["checksum", "contract", "owner", "history", "symlink"])
def test_corrupt_or_retargeted_checkpoint_fails_without_repair(lifecycle, tmp_path, damage):
    _, _, _, service = lifecycle
    path = service.store.directory("u1", "task") / "checkpoint.json"
    item = json.loads(path.read_text())
    if damage == "checksum":
        item["checksum"] = "0" * 64
        path.write_text(json.dumps(item))
    elif damage == "symlink":
        backup = tmp_path / "original.json"
        backup.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(backup)
    else:
        value = item["payload"]
        if damage == "contract":
            value["contract"]["goal"] = "modified"
        elif damage == "owner":
            value["contract"]["user_id"] = "u2"
        elif damage == "history":
            value["history"] = [{"revision": 1, "from": "proposed", "to": "succeeded", "at": 0}]
        service.store.files.atomic(path, value)
    before = path.read_bytes()
    with pytest.raises(CodingJobError):
        service.store.load("u1", "task")
    assert path.read_bytes() == before


def test_atomic_save_failure_preserves_previous_checkpoint(lifecycle, monkeypatch):
    _, _, _, service = lifecycle
    path = service.store.directory("u1", "task") / "checkpoint.json"
    before = path.read_bytes()

    def fail(*_):
        raise OSError("synthetic fsync/replace failure")

    monkeypatch.setattr("role_ai.coding_jobs.os.replace", fail)
    with pytest.raises(CodingJobError):
        service.store.transition("u1", "task", 0, "approved")
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".checkpoint-*"))


def test_restart_reads_same_contract_and_revision_without_executing(lifecycle):
    _, settings, body, service = lifecycle
    queued = approved_queue(lifecycle)
    restored = TaskCheckpointStore(Path(settings.coding_root) / "contracts").load(
        "u1", body.task_id
    )
    assert restored["state"] == queued["state"]
    assert restored["contract"] == body
    assert len(restored["history"]) == 2


def test_waiting_for_approval_and_early_pause_are_observations_only(lifecycle):
    db, settings, body, service = lifecycle
    with pytest.raises(TaskStateError):
        service.control("a" * 64, "task", "pause")
    assert service.observe_control("u1", "task") is None
    waiting = service.store.transition("u1", "task", 0, "awaiting_approval")
    with pytest.raises(ApprovalDenied):
        service.queue("a" * 64, "task", waiting["state"].revision)
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    assert service.queue("a" * 64, "task", waiting["state"].revision)["state"].status == "queued"


def test_cancel_during_verification_cannot_become_success(lifecycle):
    _, _, _, service = lifecycle
    queued = approved_queue(lifecycle)
    with service.claim("a" * 64, "task", queued["state"].revision) as running:
        verifying = service.store.transition("u1", "task", running["state"].revision, "verifying")
        service.control("a" * 64, "task", "cancel")
        evidence = {
            "contract_digest": verifying["state"].contract_digest,
            "candidate_digest": "1" * 64,
            "exit_code": 0,
            "tests": 2,
            "runner": "independent",
        }
        with pytest.raises(TaskStateError):
            service.complete("a" * 64, "task", verifying["state"].revision, evidence)
    assert service.owned("a" * 64, "task")["state"].status == "cancelled"


def test_checkpoint_checksums_and_status_never_replace_db_approval(lifecycle):
    db, settings, body, service = lifecycle
    approved_queue(lifecycle)
    path = service.store.directory("u1", "task") / "checkpoint.json"
    item = json.loads(path.read_text())["payload"]
    altered = body.model_copy(update={"goal": "forged goal"})
    from role_ai.task_contracts import contract_digest

    item["contract"] = altered.model_dump(mode="json")
    item["state"]["contract_digest"] = contract_digest(altered)
    service.store.files.atomic(path, item)
    # Even a coherent metadata file is not an approval credential.
    value = service.store.load("u1", "task")
    with pytest.raises(ApprovalDenied):
        with service.claim("a" * 64, "task", value["state"].revision):
            pass


def test_http_state_endpoints_cannot_supply_arbitrary_completion(signed_in, tmp_path):
    c = signed_in
    c.app.state.settings.coding_root = str(tmp_path / "http-tasks")
    assert (
        c.post(
            "/api/contract-tasks/task/queue", json={"revision": 0, "status": "succeeded"}
        ).status_code
        == 422
    )
    assert c.post("/api/contract-tasks/task/complete", json={"passed": True}).status_code == 404
    assert c.post("/api/contract-tasks/task/control", json={"action": "execute"}).status_code == 422
    csrf = c.headers.pop("X-CSRF-Token")
    assert c.post("/api/contract-tasks/task/control", json={"action": "cancel"}).status_code == 403
    c.headers["X-CSRF-Token"] = csrf


def test_api_queue_requires_second_explicit_resume_after_crash(lifecycle):
    _, _, _, service = lifecycle
    queued = approved_queue(lifecycle)
    running = service.store.transition("u1", "task", queued["state"].revision, "running")
    with pytest.raises(TaskStateError, match="inspect revision"):
        service.queue("a" * 64, "task", running["state"].revision)
    interrupted = service.owned("a" * 64, "task")
    assert interrupted["state"].status == "interrupted"
    assert (
        service.queue("a" * 64, "task", interrupted["state"].revision)["state"].status == "queued"
    )


def test_http_create_approve_queue_pause_resume_and_cancel(signed_in, tmp_path):
    import hashlib
    from role_ai.security import token_hash
    from role_ai.models import AuthSession
    from role_ai.task_contracts import TaskContract, ResourceRef, ReviewPolicy

    c = signed_in
    c.app.state.settings.coding_root = str(tmp_path / "http-tasks")
    doc = c.post(
        "/api/documents", json={"filename": "synthetic.txt", "content": "synthetic", "project": "P"}
    ).json()
    projects = c.get("/api/projects").json()
    with c.app.state.database_factory() as db:
        uid = db.get(AuthSession, token_hash(c.cookies.get("role_ai_session"))).user_id
    body = TaskContract(
        task_id="http-task",
        user_id=uid,
        scope="project",
        project_id=projects["projects"][0]["id"],
        agent_id="knowledge",
        goal="read",
        acceptance_criteria=("only approved source",),
        allowed_resources=(
            ResourceRef(
                kind="document",
                resource_id=doc["id"],
                version=hashlib.sha256(b"synthetic").hexdigest(),
            ),
        ),
        allowed_tools=("documents.read",),
        permissions=("read",),
        risk_level="low",
        review_policy=ReviewPolicy(policy_id="p", version=1),
        policy_version=projects["policy_generation"],
        approval_reference="http-task-approval",
    )
    value = {"contract": body.model_dump(mode="json")}
    created = c.post("/api/contract-tasks", json=value)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "proposed"
    assert "contract" not in created.json()
    assert c.post("/api/contract-tasks/http-task/queue", json={"revision": 0}).status_code == 409
    assert c.post("/api/task-approvals", json=value).status_code == 201
    queued = c.post("/api/contract-tasks/http-task/queue", json={"revision": 0})
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "queued"
    assert (
        c.post("/api/contract-tasks/http-task/control", json={"action": "pause"}).status_code == 200
    )
    paused = c.get("/api/contract-tasks/http-task").json()
    assert paused["status"] == "paused"
    resumed = c.post("/api/contract-tasks/http-task/queue", json={"revision": paused["revision"]})
    assert resumed.status_code == 200, resumed.text
    assert (
        c.post("/api/contract-tasks/http-task/control", json={"action": "cancel"}).status_code
        == 200
    )
    assert c.get("/api/contract-tasks/http-task").json()["status"] == "cancelled"
