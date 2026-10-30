"""Contract state observations; current DB authority is always checked separately."""

import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

from role_ai.coding_jobs import CodingJobStore, CodingJobError, JobBusy
from role_ai.coding_workspace import private_directory
from role_ai.task_contracts import TaskContract, TaskExecutionState, contract_digest
from role_ai.task_approvals import (
    actor,
    checked_contract,
    validate_scope,
    TaskApprovalGate,
    ApprovalDenied,
    TOOLS,
)

# No success directly from running and no automatic replay of terminal failures.
TRANSITIONS = {
    "proposed": {"approved", "awaiting_approval", "cancelled", "blocked"},
    "approved": {"queued", "cancelled", "blocked"},
    "queued": {"running", "paused", "cancelled", "blocked"},
    "running": {
        "verifying",
        "waiting",
        "paused",
        "interrupted",
        "failed",
        "cancelled",
        "blocked",
        "budget_exhausted",
    },
    "verifying": {
        "succeeded",
        "waiting",
        "paused",
        "interrupted",
        "failed",
        "cancelled",
        "blocked",
        "budget_exhausted",
    },
    "awaiting_approval": {"approved", "cancelled", "blocked"},
    "waiting": {"queued", "cancelled", "blocked", "budget_exhausted"},
    "paused": {"queued", "cancelled", "blocked"},
    "interrupted": {"queued", "cancelled", "blocked"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
    "blocked": set(),
    "budget_exhausted": set(),
}


class TaskStateError(CodingJobError):
    pass


class TaskCheckpointStore:
    """Reuse private directories, nonblocking flock, checksummed fsync+replace writes."""

    def __init__(self, root):
        self.files = CodingJobStore(root)

    def directory(self, uid, task_id):
        identifier = hashlib.sha256(task_id.encode()).hexdigest()[:32]
        return self.files.directory(uid, identifier)

    def create(self, contract):
        contract = checked_contract(contract)
        namespace = private_directory(
            self.files.root / hashlib.sha256(contract.user_id.encode()).hexdigest()
        )
        directory = self.directory(contract.user_id, contract.task_id)
        with self.files.lock(self.files.root, "create"):
            if directory.exists():
                existing = self.load(contract.user_id, contract.task_id)
                if existing["state"].contract_digest != contract_digest(contract):
                    raise TaskStateError("Task ID already belongs to another contract")
                return existing
            if len(list(namespace.glob("*/checkpoint.json"))) >= 16:
                raise TaskStateError("Task quota exceeded")
            private_directory(directory)
            now = int(time.time())
            state = TaskExecutionState(
                task_id=contract.task_id,
                contract_digest=contract_digest(contract),
                created_at=now,
                updated_at=now,
            )
            value = {
                "format": 1,
                "contract": contract.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
                "history": [],
                "evidence": None,
            }
            self.files.atomic(directory / "checkpoint.json", value)
        return self.load(contract.user_id, contract.task_id)

    def load(self, uid, task_id):
        value = self.files.read(self.directory(uid, task_id) / "checkpoint.json")
        try:
            if (
                set(value) != {"format", "contract", "state", "history", "evidence"}
                or value["format"] != 1
            ):
                raise ValueError()
            contract = TaskContract.model_validate_json(json.dumps(value["contract"]))
            state = TaskExecutionState.model_validate_json(json.dumps(value["state"]))
            if (
                contract.user_id != uid
                or contract.task_id != task_id
                or state.task_id != task_id
                or state.contract_digest != contract_digest(contract)
            ):
                raise ValueError()
            history = value["history"]
            if type(history) is not list or len(history) != state.revision or len(history) > 128:
                raise ValueError()
            previous = "proposed"
            prior_time = state.created_at
            for index, item in enumerate(history, 1):
                if (
                    set(item) != {"revision", "from", "to", "at"}
                    or type(item["revision"]) is not int
                    or item["revision"] != index
                    or type(item["at"]) is not int
                    or not prior_time <= item["at"] <= state.updated_at
                    or item["from"] != previous
                    or item["to"] not in TRANSITIONS[previous]
                ):
                    raise ValueError()
                previous = item["to"]
                prior_time = item["at"]
            if previous != state.status or (history and history[-1]["at"] != state.updated_at):
                raise ValueError()
            if state.status == "succeeded" and not self.valid_evidence(
                value["evidence"], state.contract_digest
            ):
                raise ValueError()
            return {**value, "contract": contract, "state": state}
        except (ValueError, TypeError, KeyError, AttributeError):
            raise TaskStateError("Checkpoint invalid; no operation replayed") from None

    @staticmethod
    def valid_evidence(evidence, digest):
        return (
            type(evidence) is dict
            and set(evidence)
            == {"contract_digest", "candidate_digest", "exit_code", "tests", "runner"}
            and evidence["contract_digest"] == digest
            and type(evidence["exit_code"]) is int
            and evidence["exit_code"] == 0
            and type(evidence["tests"]) is int
            and evidence["tests"] > 0
            and type(evidence["candidate_digest"]) is str
            and len(evidence["candidate_digest"]) == 64
            and all(c in "0123456789abcdef" for c in evidence["candidate_digest"])
            and evidence["runner"] == "independent"
        )

    def transition(self, uid, task_id, expected_revision, target, *, evidence=None):
        directory = self.directory(uid, task_id)
        with self.files.lock(directory):
            value = self.load(uid, task_id)
            old = value["state"]
            if type(expected_revision) is not int or old.revision != expected_revision:
                raise TaskStateError("Checkpoint revision changed")
            if target not in TRANSITIONS[old.status] or old.revision >= 128:
                raise TaskStateError("Invalid task transition")
            if target == "succeeded" and not self.valid_evidence(evidence, old.contract_digest):
                raise TaskStateError("Independent verification evidence required")
            now = max(int(time.time()), old.updated_at)
            state = TaskExecutionState(
                task_id=task_id,
                contract_digest=old.contract_digest,
                status=target,
                created_at=old.created_at,
                updated_at=now,
                revision=old.revision + 1,
            )
            history = value["history"] + [
                {"revision": state.revision, "from": old.status, "to": target, "at": now}
            ]
            # Evidence is supplied only by trusted runner code; HTTP/model status updates are absent.
            payload = {
                "format": 1,
                "contract": value["contract"].model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
                "history": history,
                "evidence": evidence if target == "succeeded" else None,
            }
            self.files.atomic(directory / "checkpoint.json", payload)
        return self.load(uid, task_id)

    @contextmanager
    def execution_lock(self, uid, task_id):
        with self.files.lock(self.directory(uid, task_id), "execute"):
            yield


class TaskLifecycle:
    def __init__(self, db, settings, *, root=None):
        self.db, self.settings = db, settings
        self.store = TaskCheckpointStore(root or Path(settings.coding_root) / "contracts")

    def owned(self, session_hash, task_id):
        user = actor(self.db, session_hash)
        return self.store.load(user.id, task_id)

    def create(self, session_hash, contract):
        user = actor(self.db, session_hash)
        contract = checked_contract(contract)
        validate_scope(self.db, user, contract)
        return self.store.create(contract)

    def authorized(self, session_hash, contract):
        if not contract.allowed_tools or not contract.allowed_resources:
            raise ApprovalDenied("no_executable_tool")
        gate = TaskApprovalGate(self.db, self.settings)
        used = set()
        for tool in contract.allowed_tools:
            rule = TOOLS.get(tool)
            matches = [r for r in contract.allowed_resources if rule and r.kind == rule[1]]
            if not matches:
                raise ApprovalDenied("tool_denied")
            for resource in matches:
                gate.require(session_hash, contract, tool, resource)
                used.add(resource)
        if used != set(contract.allowed_resources):
            raise ApprovalDenied("resource_denied")

    def queue(self, session_hash, task_id, revision):
        value = self.owned(session_hash, task_id)
        contract = value["contract"]
        uid = contract.user_id
        with self.store.execution_lock(uid, task_id):
            self.authorized(session_hash, contract)
            value = self.store.load(uid, task_id)
            if value["state"].revision != revision:
                raise TaskStateError("Checkpoint revision changed")
            if value["state"].status in {"running", "verifying"}:
                self.store.transition(uid, task_id, revision, "interrupted")
                raise TaskStateError(
                    "Interrupted checkpoint; inspect revision before explicit resume"
                )
            directory = self.store.directory(uid, task_id)
            with self.store.files.lock(directory, "control"):
                action = self.observe_control(uid, task_id)
                if action == "cancel":
                    raise TaskStateError("Task cancellation requested")
                if action == "pause" and value["state"].status in {
                    "paused",
                    "interrupted",
                    "waiting",
                }:
                    (directory / "control.json").unlink()
                elif action:
                    raise TaskStateError("Task control pending")
                if value["state"].status in {"proposed", "awaiting_approval"}:
                    value = self.store.transition(uid, task_id, revision, "approved")
                    revision = value["state"].revision
                return self.store.transition(uid, task_id, revision, "queued")

    def control(self, session_hash, task_id, action):
        value = self.owned(session_hash, task_id)
        uid = value["contract"].user_id
        if action not in {"pause", "cancel"}:
            raise TaskStateError("Invalid control")
        target = "cancelled" if action == "cancel" else "paused"
        if target not in TRANSITIONS[value["state"].status]:
            raise TaskStateError("Control not available in this state")
        directory = self.store.directory(uid, task_id)
        with self.store.files.lock(directory, "control"):
            current = self.observe_control(uid, task_id)
            # Cancel is sticky and cannot be overwritten by a later pause.
            effective = "cancel" if current == "cancel" else action
            self.store.files.atomic(directory / "control.json", {"action": effective})
        try:
            with self.store.execution_lock(uid, task_id):
                self.apply_control(uid, task_id)
        except JobBusy:
            pass
        return {"requested": effective, "task_id": task_id}

    def apply_control(self, uid, task_id):
        action = self.observe_control(uid, task_id)
        if action:
            value = self.store.load(uid, task_id)
            target = "cancelled" if action == "cancel" else "paused"
            if target in TRANSITIONS[value["state"].status]:
                self.store.transition(uid, task_id, value["state"].revision, target)
            return True
        return False

    def complete(self, session_hash, task_id, revision, evidence):
        """Trusted independent runner only; no HTTP/model completion endpoint."""
        value = self.owned(session_hash, task_id)
        contract = value["contract"]
        self.authorized(session_hash, contract)
        directory = self.store.directory(contract.user_id, task_id)
        with self.store.files.lock(directory, "control"):
            if self.apply_control(contract.user_id, task_id):
                raise TaskStateError("Task control pending")
            return self.store.transition(
                contract.user_id, task_id, revision, "succeeded", evidence=evidence
            )

    def observe_control(self, uid, task_id):
        directory = self.store.directory(uid, task_id)
        return self.store.files.control(directory)

    @contextmanager
    def claim(self, session_hash, task_id, revision):
        value = self.owned(session_hash, task_id)
        contract = value["contract"]
        uid = contract.user_id
        with self.store.execution_lock(uid, task_id):
            self.authorized(session_hash, contract)
            value = self.store.load(uid, task_id)
            if value["state"].revision != revision:
                raise TaskStateError("Checkpoint revision changed")
            if value["state"].status in {"running", "verifying"}:
                self.store.transition(uid, task_id, revision, "interrupted")
                raise TaskStateError("Interrupted checkpoint; explicit resume required")
            if self.apply_control(uid, task_id):
                raise TaskStateError("Task control pending")
            value = self.store.transition(uid, task_id, revision, "running")
            try:
                yield value
            except BaseException:
                current = self.store.load(uid, task_id)
                if current["state"].status in {"running", "verifying"}:
                    self.store.transition(uid, task_id, current["state"].revision, "interrupted")
                raise
            finally:
                self.apply_control(uid, task_id)
                current = self.store.load(uid, task_id)
                if current["state"].status in {"running", "verifying"}:
                    self.store.transition(uid, task_id, current["state"].revision, "interrupted")
