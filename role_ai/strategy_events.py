"""Opt-in, metadata-only strategy observations. No DB, dispatch, retrieval or learning.

Internal API: a trusted permission gate supplies the spec, consent and sequence.
Hashes bind scope; they are pseudonymous references, not authentication credentials.
Only bounded in-process sinks are supported in R01. Persistence belongs to R02.
"""

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from role_ai.coding_tasks import CodingTaskSpec, task_digest

HASH = r"^[0-9a-f]{64}$"


class StrategyEventError(ValueError):
    """Safe diagnostics only; never forward raw inputs or exception text."""


class ExecutionObservation(BaseModel):
    """Runtime v2 metadata only; private checkpoints keep source/log evidence separately."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    event_version: int = Field(default=2, ge=2, le=2)
    task_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=0, le=128)
    phase: Literal["prepare", "model", "plan", "list", "read", "search", "patch", "test", "finish", "control"]
    outcome: Literal["queued", "running", "waiting", "paused", "interrupted", "cancelled", "failed", "budget_exhausted", "completed"]
    test_result: Literal["not_run", "passed", "failed"]
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempts: int = Field(ge=0, le=128)
    calls: int = Field(ge=0, le=128)
    tokens: int = Field(ge=0, le=524288)
    validation_state: Literal["observed"] = "observed"


class Metadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True,
        revalidate_instances="always",
    )


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


class StrategyScope(Metadata):
    user_id: str = Field(pattern=HASH)
    project_id: str = Field(pattern=HASH)
    repository_id: str = Field(pattern=HASH)
    task_id: str = Field(pattern=HASH)

    @classmethod
    def from_spec(cls, spec: CodingTaskSpec):
        # Never copy operator labels, project names, paths or task descriptions.
        user = fingerprint({"user": spec.user_id})
        project = fingerprint({"user": user, "project": spec.project_id})
        repo = fingerprint({"project": project, "repository": spec.repository_id})
        return cls(user_id=user, project_id=project, repository_id=repo,
                   task_id=fingerprint({"repository": repo, "task": spec.task_id}))


class StrategyEvent(StrategyScope):
    event_version: int = Field(default=1, ge=1, le=1)
    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent_id: Literal["coding"] = "coding"
    data_scope: Literal["development"] = "development"
    consent_revision: int = Field(ge=1, le=2147483647)
    repo_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    task_spec_hash: str = Field(pattern=HASH)
    strategy_id: Literal["manual_scoped_patch"] = "manual_scoped_patch"
    strategy_version: int = Field(default=1, ge=1, le=1)
    task_category: Literal["unspecified", "bugfix", "feature", "refactor", "test", "documentation"] = "unspecified"
    problem_summary: None = None
    event_type: Literal[
        "workspace_planned", "workspace_ready", "workspace_changed", "workspace_failed", "workspace_closed",
        "workspace_cancelled", "workspace_budget_exhausted",
    ]
    phase: Literal["workspace"] = "workspace"
    tool_kind: Literal["none", "unknown", "git", "file_patch"]
    attempt_count: int = Field(default=1, ge=1, le=4)
    sequence: int = Field(ge=1, le=10000)
    test_result: Literal["not_run"] = "not_run"
    independent_evaluation: Literal["not_run"] = "not_run"
    outcome: Literal["pending", "candidate_only", "failed", "unknown", "cancelled", "budget_exhausted"]
    error_category: Literal["none", "workspace_error", "budget_exhausted"] = "none"
    elapsed_ms: int | None = Field(default=None, ge=0, le=86400000)
    observed_at_ms: int = Field(ge=0, le=9999999999999)
    change_sha256: str | None = Field(default=None, pattern=HASH)
    # Artifact authorization/ownership is not wired yet; never accept raw refs.
    artifact_refs: tuple[str, ...] = Field(default=(), max_length=0)
    validation_state: Literal["observed"] = "observed"
    source_rights_state: Literal["not_reviewed"] = "not_reviewed"

    @model_validator(mode="after")
    def consistent_observation(self):
        expected = {
            "workspace_planned": ("pending", "none", "none"),
            "workspace_ready": ("pending", "git", "none"),
            "workspace_changed": ("candidate_only", "file_patch", "none"),
            "workspace_failed": ("failed", "unknown", "workspace_error"),
            "workspace_closed": ("unknown", "git", "none"),
            "workspace_cancelled": ("cancelled", "unknown", "none"),
            "workspace_budget_exhausted": ("budget_exhausted", "unknown", "budget_exhausted"),
        }[self.event_type]
        if (self.outcome, self.tool_kind, self.error_category) != expected:
            raise ValueError("Inconsistent observation state")
        if (self.event_type == "workspace_changed") != (self.change_sha256 is not None):
            raise ValueError("Only an observed patch carries a change hash")
        return self

    def scope(self):
        return StrategyScope(**{name: getattr(self, name) for name in StrategyScope.model_fields})


@dataclass(frozen=True)
class RecordingReceipt:
    status: Literal["disabled", "recorded_memory", "duplicate", "rejected", "unavailable"]
    reason: str = "none"
    # A memory write never proves durable storage or reuse/training eligibility.
    durable: bool = False

    @property
    def gap(self):
        return self.status in {"rejected", "unavailable"}


class NoOpStrategySink:
    def write(self, event):
        return RecordingReceipt("disabled", "off")


class MemoryStrategySink:
    """Private bounded task buffer. Not an authenticated query API or durable log."""

    def __init__(self, scope: StrategyScope, *, max_events=128, max_bytes=262144):
        if (type(max_events) is not int or not 1 <= max_events <= 1024
                or type(max_bytes) is not int or not 1 <= max_bytes <= 1048576):
            raise StrategyEventError("Invalid memory capacity")
        self.scope = StrategyScope.model_validate(scope)
        self.max_events, self.max_bytes = max_events, max_bytes
        self._events, self._sequences = {}, {}
        self._bytes = 0
        self._lock = threading.Lock()

    def write(self, event: StrategyEvent):
        if not self._lock.acquire(blocking=False):
            return RecordingReceipt("unavailable", "busy")
        try:
            # Revalidate even direct sink calls and model_construct bypass attempts.
            try:
                event = StrategyEvent.model_validate(event)
            except (ValueError, TypeError):
                return RecordingReceipt("rejected", "invalid_event")
            if event.scope() != self.scope:
                return RecordingReceipt("rejected", "scope_mismatch")
            payload = event.model_dump_json()
            previous = self._events.get(event.event_id)
            if previous is not None:
                return RecordingReceipt("duplicate" if previous == event else "rejected",
                                        "none" if previous == event else "conflict")
            sequence = (event.attempt_count, event.sequence)
            if sequence in self._sequences:
                return RecordingReceipt("rejected", "conflict")
            size = len(payload.encode())
            if len(self._events) >= self.max_events or self._bytes + size > self.max_bytes:
                return RecordingReceipt("unavailable", "full")
            self._events[event.event_id] = event
            self._sequences[sequence] = event.event_id
            self._bytes += size
            return RecordingReceipt("recorded_memory")
        finally:
            self._lock.release()

    def snapshot(self, scope: StrategyScope):
        if scope != self.scope:
            raise StrategyEventError("Scope mismatch")
        if not self._lock.acquire(blocking=False):
            raise StrategyEventError("Memory buffer busy")
        try:
            # Arrival order is retained; missing/reversed sequences are not invented.
            return tuple(self._events.values())
        finally:
            self._lock.release()


class StrategyRecorder:
    """Internal consent-bound adapter. Telemetry errors never authorize or stop coding.

    No automatic Workspace hooks or HTTP routes are enabled by this module.
    Retries must preserve the original event (including its ID and timestamp).
    """

    def __init__(self, spec: CodingTaskSpec, *, enabled=False, consent_revision=1,
                 sink=None, hidden_evaluation=False):
        if (type(enabled) is not bool or type(hidden_evaluation) is not bool
                or type(consent_revision) is not int or not 1 <= consent_revision <= 2147483647):
            raise StrategyEventError("Invalid recorder policy")
        self.spec = CodingTaskSpec.model_validate(spec)
        self.scope = StrategyScope.from_spec(self.spec)
        self.enabled = enabled and not hidden_evaluation
        self.consent_revision = consent_revision
        self.sink = sink if sink is not None else NoOpStrategySink()
        if type(self.sink) not in {NoOpStrategySink, MemoryStrategySink}:
            raise StrategyEventError("Only bounded local sinks are supported")

    def record(self, raw):
        if not self.enabled:
            return RecordingReceipt("disabled", "off")
        try:
            event = StrategyEvent.model_validate(raw)
            if (event.scope() != self.scope or event.repo_revision != self.spec.base_commit
                    or event.task_spec_hash != task_digest(self.spec)):
                return RecordingReceipt("rejected", "scope_mismatch")
            if event.consent_revision != self.consent_revision:
                return RecordingReceipt("rejected", "consent_mismatch")
            if event.attempt_count > self.spec.budget.max_retries + 1:
                return RecordingReceipt("rejected", "invalid_event")
        except Exception:
            # Do not log validation input, traceback, str(exc), or raw tool output.
            return RecordingReceipt("rejected", "invalid_event")
        try:
            return self.sink.write(event)
        except Exception:
            return RecordingReceipt("unavailable", "sink_error")

    def record_workspace(self, snapshot, *, sequence, event_id=None, elapsed_ms=None,
                         observed_at_ms=None, attempt_count=1, task_category="unspecified"):
        if not self.enabled:
            return RecordingReceipt("disabled", "off")
        try:
            if (snapshot["task_sha256"] != task_digest(self.spec)
                    or snapshot["task_id"] != self.spec.task_id
                    or snapshot["user_id"] != self.spec.user_id
                    or snapshot["project_id"] != self.spec.project_id):
                return RecordingReceipt("rejected", "scope_mismatch")
            status = snapshot["status"]
            outcome, tool, error = {
                "planned": ("pending", "none", "none"),
                "ready": ("pending", "git", "none"),
                "changed": ("candidate_only", "file_patch", "none"),
                "failed": ("failed", "unknown", "workspace_error"),
                "closed": ("unknown", "git", "none"),
                "cancelled": ("cancelled", "unknown", "none"),
                "budget_exhausted": ("budget_exhausted", "unknown", "budget_exhausted"),
            }[status]
            # Ignore ALL free text, diff, paths, tool output, claimed test results, etc.
            event = StrategyEvent(
                **self.scope.model_dump(), event_id=event_id if event_id is not None else uuid4().hex,
                consent_revision=self.consent_revision, repo_revision=self.spec.base_commit,
                task_spec_hash=task_digest(self.spec), event_type="workspace_" + status,
                outcome=outcome, tool_kind=tool, error_category=error,
                change_sha256=snapshot["change_sha256"] if status == "changed" else None,
                sequence=sequence, attempt_count=attempt_count, elapsed_ms=elapsed_ms,
                task_category=task_category,
                observed_at_ms=time.time_ns() // 1000000 if observed_at_ms is None else observed_at_ms,
            )
        except Exception:
            return RecordingReceipt("rejected", "invalid_event")
        return self.record(event)
