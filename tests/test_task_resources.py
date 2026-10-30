"""Durable reservations, safe retries and synthetic GPU admission; no model/API calls."""

import asyncio
import pytest
from role_ai.config import Settings
from role_ai.task_resources import TaskRuntime, RuntimeLimits, AttemptResult, RuntimeFailure
from role_ai.task_approvals import ApprovalService
from role_ai.coding_agent import ModelBusy
from role_ai.coding_tasks import CodingBoundaryError
from role_ai.task_checkpoints import TaskLifecycle
from test_scope_permissions import contract, reference

pytest_plugins = ("test_scope_permissions",)


@pytest.fixture
def runtime(access, tmp_path):
    db, _, rows, versions = access
    settings = Settings(
        _env_file=None, coding_root=str(tmp_path / "jobs"), storage_dir=str(tmp_path / "artifacts")
    )
    ref = reference(versions)
    body = contract(
        db,
        rows["doc"].project_id,
        ref,
        allowed_tools=("documents.read",),
        approval_reference="approval",
    )
    life = TaskLifecycle(db, settings)
    life.create("a" * 64, body)
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    queued = life.queue("a" * 64, "task", 0)
    manager = TaskRuntime(db, settings)
    return db, settings, body, ref, life, manager, queued


def call(manager, ref, operation_id, operation, **kwargs):
    return asyncio.run(
        manager.attempt("a" * 64, "task", "documents.read", ref, operation_id, operation, **kwargs)
    )


def test_measured_usage_refunds_reservation_and_duplicate_operation_never_replays(runtime):
    _, _, _, ref, life, manager, queued = runtime
    calls = []

    async def operation():
        calls.append(True)
        return AttemptResult("result", 10, 3)

    with life.claim("a" * 64, "task", queued["state"].revision):
        assert call(manager, ref, "call1", operation, model=True) == "result"
        report = manager.status("a" * 64, "task")
        assert report["charged_tokens"] == 13 and report["model_tokens"] == 13
        assert report["measured_calls"] == 1
        with pytest.raises(RuntimeFailure, match="operation_already_recorded"):
            call(manager, ref, "call1", operation, model=True)
    assert calls == [True]


def test_unmeasured_usage_preserves_reservation_and_is_not_reported_as_zero(runtime):
    _, _, _, ref, life, manager, queued = runtime

    async def operation():
        return AttemptResult("unmeasured")

    with life.claim("a" * 64, "task", queued["state"].revision):
        call(manager, ref, "unknown", operation, model=True)
        report = manager.status("a" * 64, "task")
        assert report["charged_tokens"] == 5120
        assert report["model_tokens"] is None
        assert report["unmeasured_calls"] == 1
        assert report["charged_tokens_are_measurement"] is False


def test_known_busy_retries_are_bounded_and_charge_every_attempt(runtime):
    _, _, body, ref, life, manager, queued = runtime
    calls = []

    async def busy():
        calls.append(True)
        raise ModelBusy("must not be journaled secret")

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="resource_busy"):
            call(manager, ref, "busy", busy, model=True)
    report = manager.status("a" * 64, "task")
    assert len(calls) == body.budget.max_retries + 1
    assert report["steps"] == len(calls) and report["retries"] == body.budget.max_retries
    assert report["charged_tokens"] == 5120 * len(calls)
    assert "must not be journaled" not in manager.path("u1", "task").read_text()


@pytest.mark.parametrize("error", [CodingBoundaryError("private"), ValueError("private")])
def test_scope_or_unknown_failures_never_automatically_retry(runtime, error):
    _, _, _, ref, life, manager, queued = runtime
    calls = []

    async def operation():
        calls.append(True)
        raise error

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(RuntimeFailure):
            call(manager, ref, "failure", operation)
    assert len(calls) == 1
    assert life.owned("a" * 64, "task")["state"].status in {"failed", "blocked"}


def test_token_budget_is_checked_before_callback(runtime):
    db, settings, _, ref, life, _, queued = runtime
    manager = TaskRuntime(db, settings, limits=RuntimeLimits(token_budget=1))
    calls = []

    async def operation():
        calls.append(True)
        return AttemptResult("forbidden")

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="budget_exhausted"):
            call(manager, ref, "too-much", operation, model=True)
    assert not calls
    assert life.owned("a" * 64, "task")["state"].status == "budget_exhausted"


@pytest.mark.parametrize(
    "snapshot",
    [None, {}, {"total_mib": 8192, "used_mib": None}, {"total_mib": 8192, "used_mib": 4000}],
)
def test_missing_or_overcommitted_vram_never_starts_callback(runtime, snapshot):
    db, settings, _, ref, life, _, queued = runtime
    manager = TaskRuntime(db, settings, probe=lambda: snapshot)
    calls = []

    async def operation():
        calls.append(True)
        return AttemptResult("forbidden")

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="gpu_admission_denied"):
            call(manager, ref, "gpu", operation, model=True, gpu_required=True)
    assert not calls
    assert life.owned("a" * 64, "task")["state"].status == "waiting"
    assert not (manager.path("u1", "task")).exists()


def test_gpu_admission_reuses_conservative_existing_math_without_policy_write(runtime):
    db, settings, _, ref, life, _, queued = runtime
    manager = TaskRuntime(db, settings, probe=lambda: {"total_mib": 8192, "used_mib": 0})

    async def operation():
        return AttemptResult("allowed", 5, 2)

    with life.claim("a" * 64, "task", queued["state"].revision):
        assert call(manager, ref, "gpu-ok", operation, model=True, gpu_required=True) == "allowed"
    from pathlib import Path

    assert not (Path(settings.storage_dir) / "gpu-memory-policy.json").exists()


def test_gpu_overrun_discards_result_instead_of_reporting_success(runtime):
    db, settings, _, ref, life, _, queued = runtime
    snapshots = iter([{"total_mib": 8192, "used_mib": 0}, {"total_mib": 8192, "used_mib": 7000}])
    manager = TaskRuntime(db, settings, probe=lambda: next(snapshots))

    async def operation():
        return AttemptResult("must-discard", 5, 2)

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="budget_exhausted"):
            call(manager, ref, "gpu-over", operation, model=True, gpu_required=True)
    assert life.owned("a" * 64, "task")["state"].status == "budget_exhausted"


def test_revocation_after_operation_discards_value_and_stops(runtime):
    db, settings, _, ref, life, manager, queued = runtime

    async def operation():
        ApprovalService(db, settings).revoke("a" * 64, "approval")
        db.commit()
        return AttemptResult("discard", 5, 2)

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="permission_denied"):
            call(manager, ref, "revoked", operation, model=True)
    assert life.owned("a" * 64, "task")["state"].status == "blocked"


def test_pending_intent_requires_explicit_recovery_and_never_refunds_unknown_call(runtime):
    _, _, _, ref, life, manager, queued = runtime
    with life.claim("a" * 64, "task", queued["state"].revision):
        value = life.owned("a" * 64, "task")
        data = manager.load(value)
        data["steps"] = 1
        data["charged_tokens"] = 5120
        data["pending"] = {"id": "crashed", "reserved_tokens": 5120, "model": True}
        manager.save(value["contract"], data)

        async def forbidden():
            raise AssertionError("must not replay")

        with pytest.raises(RuntimeFailure, match="recovery_required"):
            call(manager, ref, "crashed", forbidden, model=True)
    recovered = manager.recover("a" * 64, "task")
    assert recovered["pending"] is None and recovered["charged_tokens"] == 5120
    assert recovered["unmeasured_calls"] == 1
    assert recovered["events"][-1]["kind"] == "interrupted_not_replayed"
    assert manager.recover("a" * 64, "task") == recovered


def test_cancelled_await_is_preserved_and_no_automatic_retry(runtime):
    _, _, _, ref, life, manager, queued = runtime

    async def operation():
        raise asyncio.CancelledError()

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(asyncio.CancelledError):
            call(manager, ref, "cancel", operation, model=True)
    assert life.owned("a" * 64, "task")["state"].status == "cancelled"
    assert manager.status("a" * 64, "task")["charged_tokens"] == 5120


def test_step_budget_cannot_be_reset_by_new_operation_ids(runtime):
    _, _, body, ref, life, manager, queued = runtime
    calls = []

    async def operation():
        calls.append(True)
        return AttemptResult("ok")

    with life.claim("a" * 64, "task", queued["state"].revision):
        for index in range(body.budget.max_steps):
            call(manager, ref, "step" + str(index), operation)
        with pytest.raises(RuntimeFailure, match="budget_exhausted"):
            call(manager, ref, "extra", operation)
    assert len(calls) == body.budget.max_steps
    assert manager.status("a" * 64, "task")["steps"] == body.budget.max_steps


def test_expired_task_deadline_blocks_before_callback(runtime, monkeypatch):
    _, _, body, ref, life, manager, queued = runtime
    import time
    from types import SimpleNamespace

    monkeypatch.setattr(
        "role_ai.task_resources.time",
        SimpleNamespace(
            time=lambda: queued["state"].created_at + body.budget.timeout_seconds + 1,
            monotonic=time.monotonic,
        ),
    )
    calls = []

    async def operation():
        calls.append(True)
        return AttemptResult("late")

    with life.claim("a" * 64, "task", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="budget_exhausted"):
            call(manager, ref, "late", operation)
    assert not calls


def test_timed_out_operation_is_cancelled_charged_and_not_retried(runtime, monkeypatch):
    db, settings, body, ref, life, manager, _ = runtime
    short = body.model_copy(
        update={
            "task_id": "short",
            "approval_reference": "short-approval",
            "budget": body.budget.model_copy(update={"timeout_seconds": 1}),
        }
    )
    created = life.create("a" * 64, short)
    ApprovalService(db, settings).issue("a" * 64, short)
    db.commit()
    queued = life.queue("a" * 64, "short", 0)
    import time
    from types import SimpleNamespace

    monkeypatch.setattr(
        "role_ai.task_resources.time",
        SimpleNamespace(time=lambda: created["state"].created_at + 0.25, monotonic=time.monotonic),
    )
    events = []

    async def operation():
        events.append("started")
        try:
            await asyncio.sleep(2)
        finally:
            events.append("cleaned")

    with life.claim("a" * 64, "short", queued["state"].revision):
        with pytest.raises(RuntimeFailure, match="timeout_uncertain"):
            asyncio.run(
                manager.attempt(
                    "a" * 64, "short", "documents.read", ref, "timeout", operation, model=True
                )
            )
    assert events == ["started", "cleaned"]
    assert life.owned("a" * 64, "short")["state"].status == "interrupted"
    data = manager.status("a" * 64, "short")
    assert data["charged_tokens"] == 5120 and data["retries"] == 0 and data["model_tokens"] is None


@pytest.mark.parametrize("damage", ["checksum", "policy", "steps", "pending"])
def test_runtime_checkpoint_corruption_never_resets_budget(runtime, damage):
    import json

    _, _, _, ref, life, manager, queued = runtime

    async def operation():
        return AttemptResult("ok", 2, 1)

    with life.claim("a" * 64, "task", queued["state"].revision):
        call(manager, ref, "initial", operation, model=True)
    path = manager.path("u1", "task")
    item = json.loads(path.read_text())
    if damage == "checksum":
        item["checksum"] = "0" * 64
        path.write_text(json.dumps(item))
    else:
        value = item["payload"]
        if damage == "policy":
            value["policy_digest"] = "0" * 64
        elif damage == "steps":
            value["steps"] = 1000
        else:
            value["pending"] = {"bad": True}
        life.store.files.atomic(path, value)
    before = path.read_bytes()
    from role_ai.coding_jobs import CodingJobError

    with pytest.raises(CodingJobError):
        manager.status("a" * 64, "task")
    assert path.read_bytes() == before
