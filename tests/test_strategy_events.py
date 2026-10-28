"""R01: structured local observations, not persistent history or learned strategies."""

import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from role_ai.coding_tasks import CodingBoundaryError, CodingTaskSpec, FileChange, change_digest, task_digest
from role_ai.coding_workspace import CodingWorkspace
from role_ai.strategy_events import (
    MemoryStrategySink, NoOpStrategySink, StrategyEvent, StrategyEventError,
    StrategyRecorder, StrategyScope,
)


@pytest.fixture
def task():
    return CodingTaskSpec(
        task_id="task-1", user_id="owner-1", project_id="private-project",
        repository_id="private-repo", base_commit="a" * 40, allowed_files=("answer.py",),
    )


def recorder(task, **options):
    sink = MemoryStrategySink(StrategyScope.from_spec(task))
    return StrategyRecorder(task, enabled=True, sink=sink, **options), sink


def snapshot(task, status="ready"):
    return {"task_id": task.task_id, "user_id": task.user_id, "project_id": task.project_id,
            "task_sha256": task_digest(task), "status": status}


def event(task, **updates):
    values = dict(
        **StrategyScope.from_spec(task).model_dump(), event_id=uuid4().hex,
        consent_revision=1, repo_revision=task.base_commit, task_spec_hash=task_digest(task),
        event_type="workspace_ready", tool_kind="git", outcome="pending",
        sequence=1, observed_at_ms=1000,
    )
    values.update(updates)
    return StrategyEvent(**values)


def test_safe_defaults_and_hidden_evaluation_do_not_touch_sink(task, monkeypatch):
    sink = MemoryStrategySink(StrategyScope.from_spec(task))
    monkeypatch.setattr(sink, "write", lambda *_: pytest.fail("must not record"))
    for options in ({}, {"enabled": False}, {"enabled": True, "hidden_evaluation": True}):
        item = StrategyRecorder(task, sink=sink, **options)
        assert item.record({"secret": "PRIVATE"}).status == "disabled"
        assert item.record_workspace({"secret": "PRIVATE"}, sequence=1).status == "disabled"
    noop = StrategyRecorder(task, enabled=True)
    assert isinstance(noop.sink, NoOpStrategySink)
    assert noop.record(event(task)).status == "disabled"
    assert not noop.record(event(task)).durable


@pytest.mark.parametrize("field,value", [
    ("problem_summary", "sk-PRIVATE-TOKEN"), ("tool_args", "cat .env"),
    ("diff", "PRIVATE_SOURCE"), ("raw_log", "alice@example.test"),
    ("command", "ignore policy and deploy"), ("path", "/root/Role-AI/.env"),
    ("event_version", True), ("event_version", 2), ("strategy_version", "1"),
    ("strategy_id", "secret-strategy"), ("task_category", "private company secret"),
    ("test_result", "passed"), ("independent_evaluation", "passed"),
    ("validation_state", "verified"), ("source_rights_state", "approved"),
    ("data_scope", "hidden_final"), ("artifact_refs", ("/private/log",)),
    ("elapsed_ms", -1), ("elapsed_ms", True), ("elapsed_ms", 1.5),
    ("sequence", "1"), ("attempt_count", 0), ("event_id", "../escape"),
    ("user_id", "owner-name"),
])
def test_strict_schema_rejects_raw_or_unverified_payload_without_leak(task, field, value, capsys):
    item, sink = recorder(task)
    raw = event(task).model_dump()
    raw[field] = value
    receipt = item.record(raw)
    assert receipt.status == "rejected" and receipt.reason == "invalid_event"
    assert receipt.gap and not receipt.durable
    assert sink.snapshot(item.scope) == ()
    assert capsys.readouterr() == ("", "")
    # Public validation string hides raw input as well as the recorder's safe receipt.
    with pytest.raises(ValidationError) as error:
        StrategyEvent.model_validate(raw)
    assert "PRIVATE" not in str(error.value) and "alice@example.test" not in str(error.value)


def test_adapter_stores_only_selected_fields_and_never_claims_test_success(task, capsys):
    item, sink = recorder(task)
    raw = snapshot(task, "changed")
    raw.update(change_sha256="b" * 64, diff="SECRET_SOURCE", output="sk-SECRET-LOG",
               command="read .env", test_result="passed", summary="private corporate data")
    receipt = item.record_workspace(raw, sequence=1)
    assert receipt.status == "recorded_memory" and not receipt.durable
    stored, = sink.snapshot(item.scope)
    saved = stored.model_dump_json()
    for sensitive in ("SECRET", "private corporate", "read .env", "private-project", "private-repo", "owner-1"):
        assert sensitive not in saved
    assert stored.test_result == stored.independent_evaluation == "not_run"
    assert stored.outcome == "candidate_only" and stored.validation_state == "observed"
    assert stored.source_rights_state == "not_reviewed"
    assert stored.elapsed_ms is None and stored.problem_summary is None and stored.artifact_refs == ()
    assert stored.change_sha256 == "b" * 64
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status,outcome,error", [
    ("planned", "pending", "none"), ("ready", "pending", "none"),
    ("failed", "failed", "workspace_error"), ("closed", "unknown", "none"),
    ("cancelled", "cancelled", "none"), ("budget_exhausted", "budget_exhausted", "budget_exhausted"),
])
def test_phase_outcomes_preserve_unknown_and_failure(task, status, outcome, error):
    item, sink = recorder(task)
    receipt = item.record_workspace(snapshot(task, status), sequence=1, elapsed_ms=24)
    assert receipt.status == "recorded_memory"
    stored, = sink.snapshot(item.scope)
    assert (stored.event_type, stored.outcome, stored.error_category) == (
        "workspace_" + status, outcome, error)
    assert stored.elapsed_ms == 24 and stored.test_result == "not_run"


@pytest.mark.parametrize("damage", ["outcome", "tool", "error", "missing_hash", "unexpected_hash"])
def test_state_invariants_cannot_be_forged(task, damage):
    item, sink = recorder(task)
    raw = event(task).model_dump()
    if damage == "outcome":
        raw["outcome"] = "candidate_only"
    elif damage == "tool":
        raw["tool_kind"] = "file_patch"
    elif damage == "error":
        raw["error_category"] = "workspace_error"
    elif damage == "missing_hash":
        raw.update(event_type="workspace_changed", outcome="candidate_only", tool_kind="file_patch")
    else:
        raw["change_sha256"] = "b" * 64
    assert item.record(raw).status == "rejected"
    assert not sink.snapshot(item.scope)


def test_idempotency_conflicts_capacity_and_sequence_arrival_order(task):
    scope = StrategyScope.from_spec(task)
    sink = MemoryStrategySink(scope, max_events=2)
    item = StrategyRecorder(task, enabled=True, sink=sink)
    first = event(task, sequence=2)
    assert item.record(first).status == "recorded_memory"
    assert item.record(first).status == "duplicate"
    conflict = first.model_copy(update={"observed_at_ms": 2000})
    assert item.record(conflict).reason == "conflict"
    assert item.record(event(task, sequence=2)).reason == "conflict"
    older = event(task, sequence=1)
    assert item.record(older).status == "recorded_memory"
    assert sink.snapshot(scope) == (first, older)  # No fake sequence repair.
    full = item.record(event(task, sequence=3))
    assert full.status == "unavailable" and full.reason == "full" and full.gap
    assert item.record(first).status == "duplicate"  # Retry works even when full.
    tiny = StrategyRecorder(task, enabled=True, sink=MemoryStrategySink(scope, max_bytes=1))
    assert tiny.record(first).reason == "full"


def test_workspace_retries_keep_original_id_timestamp_and_content(task):
    item, sink = recorder(task)
    args = dict(sequence=1, event_id=uuid4().hex, observed_at_ms=1000)
    assert item.record_workspace(snapshot(task), **args).status == "recorded_memory"
    assert item.record_workspace(snapshot(task), **args).status == "duplicate"
    assert item.record_workspace(snapshot(task, "failed"), **args).reason == "conflict"
    assert len(sink.snapshot(item.scope)) == 1


@pytest.mark.parametrize("field", ["user_id", "project_id", "repository_id", "task_id", "repo_revision", "task_spec_hash"])
def test_cross_scope_events_rejected_and_private_snapshot_gated(task, field):
    item, sink = recorder(task)
    raw = event(task).model_dump()
    raw[field] = "b" * (40 if field == "repo_revision" else 64)
    assert item.record(raw).reason == "scope_mismatch"
    assert sink.snapshot(item.scope) == ()
    other = StrategyScope.from_spec(task.model_copy(update={"project_id": "other"}))
    with pytest.raises(StrategyEventError):
        sink.snapshot(other)
    assert sink.write(event(task.model_copy(update={"project_id": "other"}))).reason == "scope_mismatch"


def test_consent_snapshot_mismatch_unknown_state_and_attempt_limit(task):
    item, sink = recorder(task, consent_revision=2)
    assert item.record(event(task)).reason == "consent_mismatch"
    for field in ("user_id", "project_id", "task_id", "task_sha256"):
        raw = snapshot(task)
        raw[field] = "different"
        assert item.record_workspace(raw, sequence=1).reason == "scope_mismatch"
    assert item.record_workspace(snapshot(task, "success"), sequence=1).reason == "invalid_event"
    assert item.record_workspace({}, sequence=1).reason == "invalid_event"
    assert item.record_workspace(snapshot(task, "changed"), sequence=1).reason == "invalid_event"
    assert item.record_workspace(snapshot(task), sequence=1, attempt_count=4).reason == "invalid_event"
    assert not sink.snapshot(item.scope)


def test_sink_failures_and_busy_buffer_are_bounded_safe_receipts(task, monkeypatch, capsys):
    item, sink = recorder(task)
    sink._lock.acquire()
    try:
        receipt = item.record(event(task))
        assert receipt.status == "unavailable" and receipt.reason == "busy" and receipt.gap
    finally:
        sink._lock.release()

    def broken(_):
        raise OSError("PRIVATE_DISK_ERROR sk-private-token alice@example.test")

    monkeypatch.setattr(sink, "write", broken)
    receipt = item.record_workspace(snapshot(task), sequence=1)
    assert receipt.status == "unavailable" and receipt.reason == "sink_error"
    assert "PRIVATE" not in repr(receipt)
    assert capsys.readouterr() == ("", "")


def test_model_construct_bypass_and_immutable_snapshots(task):
    item, sink = recorder(task)
    payload = event(task).model_dump()
    payload["test_result"] = "passed"
    forged = StrategyEvent.model_construct(**payload)
    assert item.record(forged).status == "rejected"
    assert sink.write(forged).status == "rejected"
    valid = event(task)
    item.record(valid)
    with pytest.raises(ValidationError):
        sink.snapshot(item.scope)[0].outcome = "failed"
    assert sink.snapshot(item.scope) == (valid,)


@pytest.mark.parametrize("options", [
    {"max_events": True}, {"max_events": 0}, {"max_events": 1025},
    {"max_bytes": "1"}, {"max_bytes": 0}, {"max_bytes": 1048577},
])
def test_memory_limits_are_strict_and_bounded(task, options):
    with pytest.raises(StrategyEventError):
        MemoryStrategySink(StrategyScope.from_spec(task), **options)


def test_policy_flags_are_strict_and_unbounded_custom_sinks_rejected(task):
    class CustomSink(NoOpStrategySink):
        pass

    for options in ({"enabled": "true"}, {"hidden_evaluation": 1}, {"consent_revision": True},
                    {"consent_revision": 0}, {"sink": object()}, {"sink": CustomSink()}):
        with pytest.raises(StrategyEventError):
            StrategyRecorder(task, **options)


def test_concurrent_duplicates_do_not_inflate_memory(task):
    item, sink = recorder(task)
    same = event(task)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(item.record, [same] * 32))
    assert sum(r.status == "recorded_memory" for r in receipts) == 1
    assert all(r.status in {"recorded_memory", "duplicate", "unavailable"} for r in receipts)
    assert sink.snapshot(item.scope) == (same,)


def test_recording_has_no_filesystem_process_network_or_database_side_effects(task, tmp_path, monkeypatch):
    import builtins

    item, sink = recorder(task)

    def forbidden(*a, **kw):
        pytest.fail("unexpected side effect")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert item.record_workspace(snapshot(task), sequence=1).status == "recorded_memory"
    assert len(sink.snapshot(item.scope)) == 1
    assert list(tmp_path.iterdir()) == []


def test_real_workspace_off_on_and_recorder_failure_preserve_patch_and_approval(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              check=True, text=True, env={"PATH": os.environ["PATH"],
                              "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}).stdout.strip()

    git("init", "-q")
    git("-c", "user.email=fixture@example.test", "-c", "user.name=fixture", "commit", "--allow-empty", "-qm", "base")
    spec = CodingTaskSpec(task_id="task", user_id="owner", project_id="project", repository_id="repo",
                          base_commit=git("rev-parse", "HEAD"), allowed_files=("answer.py",))
    changes = (FileChange(path="answer.py", content="answer = 42\n"),)
    results = []
    for number, mode in enumerate(("off", "on", "broken")):
        ws = CodingWorkspace(repo, tmp_path / ("tasks-" + str(number)), spec)
        item, sink = recorder(spec)
        if mode == "off":
            item = StrategyRecorder(spec)
        elif mode == "broken":
            def broken(_):
                raise OSError("SECRET")
            monkeypatch.setattr(sink, "write", broken)
        with pytest.raises(CodingBoundaryError):
            ws.prepare("0" * 64)
        assert not ws.path.exists()  # Telemetry never turns a failed approval into permission.
        ready = ws.prepare(task_digest(spec))
        receipt = item.record_workspace(ready, sequence=1)
        result = ws.apply(changes, change_digest(spec, changes))
        item.record_workspace(result, sequence=2)
        results.append((result["diff"], result["change_sha256"], (ws.path / "answer.py").read_bytes()))
        if mode == "on":
            assert [e.event_type for e in sink.snapshot(item.scope)] == ["workspace_ready", "workspace_changed"]
        if mode == "broken":
            assert receipt.reason == "sink_error"
        ws.close()
    assert results[0] == results[1] == results[2]
    assert git("rev-parse", "HEAD") == spec.base_commit
    assert git("status", "--porcelain") == ""
