"""Bounded, durable attempts. Reservations are not measured tokens or authority."""

import asyncio
import math
import re
import time

import httpx
from pydantic import BaseModel, ConfigDict, Field

from role_ai.coding_agent import ModelBusy, BudgetStop, Recoverable
from role_ai.coding_jobs import CodingJobError, checksum
from role_ai.coding_tasks import CodingBoundaryError
from role_ai.gpu_resources import GpuPolicy, GpuBudgetError, admission, gpu_snapshot, read_policy
from role_ai.task_approvals import ApprovalDenied
from role_ai.task_errors import failure_code
from role_ai.coding_jobs import CheckpointWriteError
from role_ai.task_checkpoints import TaskLifecycle, TaskStateError


class RuntimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    token_budget: int = Field(default=32768, ge=1, le=65536)
    context_tokens: int = Field(default=4096, ge=1, le=16384)
    output_tokens: int = Field(default=1024, ge=1, le=2048)
    gpu_limit_mib: int = Field(default=6144, ge=512, le=7168)
    gpu_reserve_mib: int = Field(default=1024, ge=1024, le=8192)
    inference_mib: int = Field(default=4096, ge=128, le=7168)


class RuntimeFailure(CodingJobError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__(kind)


class AttemptResult:
    """Trusted transport metadata, never populated from an LLM's JSON body."""

    def __init__(self, value, prompt_tokens=None, output_tokens=None):
        self.value = value
        self.prompt_tokens = prompt_tokens
        self.output_tokens = output_tokens


def failure_kind(error):
    if isinstance(error, RuntimeFailure):
        return error.kind if error.kind in {
            "control_requested",
            "invalid_transport_result",
            "budget_exhausted",
        } else "internal_error", False
    if isinstance(error, ApprovalDenied):
        return "permission_denied", False
    if isinstance(error, CodingBoundaryError):
        return "scope_violation", False
    if isinstance(error, BudgetStop):
        return "budget_exhausted", False
    storage_code = failure_code(error)
    if storage_code in {"file_permission", "checkpoint_io"}:
        return storage_code, False
    if isinstance(error, (ModelBusy, GpuBudgetError)):
        return "resource_busy", True
    if isinstance(error, Recoverable):
        return "recoverable_input", True
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "timeout_uncertain", False
    if isinstance(error, asyncio.CancelledError):
        return "cancelled", False
    if isinstance(error, (TaskStateError, CodingJobError)):
        return "checkpoint_or_runner", False
    return "internal_error", False


class TaskRuntime:
    """Trusted server callbacks only. No HTTP executor, shell, model install or retry of unknown effects."""

    def __init__(self, db, settings, *, limits=None, root=None, probe=None):
        self.lifecycle = TaskLifecycle(db, settings, root=root)
        self.settings = settings
        self.limits = RuntimeLimits.model_validate_json(
            (limits or RuntimeLimits()).model_dump_json()
        )
        self.probe = probe or gpu_snapshot

    def gpu_admission(self):
        configured = read_policy(self.settings)
        policy = GpuPolicy(
            enabled=True,
            limit_mib=min(self.limits.gpu_limit_mib, configured.limit_mib)
            if configured.enabled
            else self.limits.gpu_limit_mib,
            reserve_mib=max(self.limits.gpu_reserve_mib, configured.reserve_mib)
            if configured.enabled
            else self.limits.gpu_reserve_mib,
            inference_mib=max(self.limits.inference_mib, configured.inference_mib)
            if configured.enabled
            else self.limits.inference_mib,
        )
        # Read only: never writes GPU policy, evicts a model, or substitutes a missing measurement with zero.
        try:
            snapshot = self.probe()
            if type(snapshot) is not dict or any(
                type(snapshot.get(k)) is not int for k in ("total_mib", "used_mib")
            ):
                raise ValueError()
            return admission(policy, snapshot, "inference")
        except (KeyError, TypeError, ValueError, OSError):
            raise GpuBudgetError("GPU measurement unavailable; execution denied") from None

    def path(self, uid, task_id):
        return self.lifecycle.store.directory(uid, task_id) / "runtime.json"

    def load(self, value):
        contract = value["contract"]
        state = value["state"]
        path = self.path(contract.user_id, contract.task_id)
        policy = checksum(self.limits.model_dump(mode="json"))
        if not path.exists():
            return {
                "format": 1,
                "contract_digest": state.contract_digest,
                "policy_digest": policy,
                "created_at": state.created_at,
                "steps": 0,
                "retries": 0,
                "charged_tokens": 0,
                "measured_prompt_tokens": 0,
                "measured_output_tokens": 0,
                "measured_calls": 0,
                "unmeasured_calls": 0,
                "active_seconds": 0.0,
                "pending": None,
                "events": [],
            }
        data = self.lifecycle.store.files.read(path)
        keys = {
            "format",
            "contract_digest",
            "policy_digest",
            "created_at",
            "steps",
            "retries",
            "charged_tokens",
            "measured_prompt_tokens",
            "measured_output_tokens",
            "measured_calls",
            "unmeasured_calls",
            "active_seconds",
            "pending",
            "events",
        }
        if (
            type(data) is not dict
            or set(data) != keys
            or data["format"] != 1
            or data["contract_digest"] != state.contract_digest
            or data["policy_digest"] != policy
            or data["created_at"] != state.created_at
            or any(
                type(data[k]) is not int or data[k] < 0
                for k in (
                    "steps",
                    "retries",
                    "charged_tokens",
                    "measured_prompt_tokens",
                    "measured_output_tokens",
                    "measured_calls",
                    "unmeasured_calls",
                )
            )
            or type(data["active_seconds"]) not in (int, float)
            or not math.isfinite(data["active_seconds"])
            or data["active_seconds"] < 0
            or type(data["events"]) is not list
            or len(data["events"]) > 128
        ):
            raise RuntimeFailure("invalid_runtime_checkpoint")
        events = data["events"]
        kinds = {
            "completed",
            "permission_denied",
            "scope_violation",
            "budget_exhausted",
            "resource_busy",
            "recoverable_input",
            "timeout_uncertain",
            "cancelled",
            "checkpoint_or_runner",
            "internal_error",
            "file_permission",
            "checkpoint_io",
            "interrupted_not_replayed",
            "control_requested",
            "invalid_transport_result",
        }
        if any(
            type(e) is not dict
            or set(e) != {"id", "kind", "measured"}
            or type(e["id"]) is not str
            or not re.fullmatch("[A-Za-z0-9_-]{1,64}", e["id"])
            or e["kind"] not in kinds
            or type(e["measured"]) is not bool
            for e in events
        ):
            raise RuntimeFailure("invalid_runtime_checkpoint")
        pending = data["pending"]
        if pending is not None:
            if (
                type(pending) is not dict
                or set(pending) != {"id", "reserved_tokens", "model"}
                or type(pending["id"]) is not str
                or not re.fullmatch("[A-Za-z0-9_-]{1,64}", pending["id"])
                or type(pending["model"]) is not bool
                or type(pending["reserved_tokens"]) is not int
                or pending["reserved_tokens"]
                != (
                    self.limits.context_tokens + self.limits.output_tokens
                    if pending["model"]
                    else 0
                )
            ):
                raise RuntimeFailure("invalid_runtime_checkpoint")
        if (
            data["steps"] != len(events) + int(pending is not None)
            or data["steps"] > contract.budget.max_steps
            or data["retries"] > contract.budget.max_retries
        ):
            raise RuntimeFailure("invalid_runtime_checkpoint")
        return data

    def save(self, contract, data):
        try:
            self.lifecycle.store.files.atomic(self.path(contract.user_id, contract.task_id), data)
        except OSError:
            raise CheckpointWriteError(
                "Runtime checkpoint write failed; inspect storage before resuming."
            ) from None

    def status(self, session_hash, task_id):
        value = self.lifecycle.owned(session_hash, task_id)
        data = self.load(value)
        fully_measured = (
            data["measured_calls"] > 0
            and data["unmeasured_calls"] == 0
            and not (data["pending"] and data["pending"]["model"])
        )
        return {
            **data,
            "model_tokens": data["measured_prompt_tokens"] + data["measured_output_tokens"]
            if fully_measured
            else None,
            "charged_tokens_are_measurement": False,
        }

    def authorize(self, session_hash, task_id, tool, resource):
        value = self.lifecycle.owned(session_hash, task_id)
        if value["state"].status != "running":
            raise RuntimeFailure("task_not_running")
        if self.lifecycle.apply_control(value["contract"].user_id, task_id):
            raise RuntimeFailure("control_requested")
        from role_ai.task_approvals import TaskApprovalGate

        TaskApprovalGate(self.lifecycle.db, self.settings).require(
            session_hash, value["contract"], tool, resource
        )
        return value

    def terminal(self, uid, task_id, kind):
        value = self.lifecycle.store.load(uid, task_id)
        target = {
            "permission_denied": "blocked",
            "scope_violation": "blocked",
            "budget_exhausted": "budget_exhausted",
            "cancelled": "cancelled",
            "timeout_uncertain": "interrupted",
            "resource_busy": "waiting",
        }.get(kind, "failed")
        if value["state"].status in {"running", "verifying"}:
            self.lifecycle.store.transition(uid, task_id, value["state"].revision, target)

    def recover(self, session_hash, task_id):
        """Explicit recovery only: retain charged reservation; never replay a pending operation."""
        value = self.lifecycle.owned(session_hash, task_id)
        contract = value["contract"]
        with self.lifecycle.store.execution_lock(contract.user_id, task_id):
            self.lifecycle.authorized(session_hash, contract)
            directory = self.lifecycle.store.directory(contract.user_id, task_id)
            with self.lifecycle.store.files.lock(directory, "runtime"):
                data = self.load(value)
                if data["pending"] is not None:
                    data["events"].append(
                        {
                            "id": data["pending"]["id"],
                            "kind": "interrupted_not_replayed",
                            "measured": False,
                        }
                    )
                    data["unmeasured_calls"] += int(data["pending"]["model"])
                    data["pending"] = None
                    self.save(contract, data)
                return data

    async def attempt(
        self,
        session_hash,
        task_id,
        tool,
        resource,
        operation_id,
        operation,
        *,
        model=False,
        gpu_required=False,
    ):
        if type(operation_id) is not str or not re.fullmatch("[A-Za-z0-9_-]{1,64}", operation_id):
            raise RuntimeFailure("invalid_operation_id")
        value = self.authorize(session_hash, task_id, tool, resource)
        contract = value["contract"]
        uid = contract.user_id
        directory = self.lifecycle.store.directory(uid, task_id)
        with self.lifecycle.store.files.lock(directory, "runtime"):
            data = self.load(value)
            if data["pending"] is not None:
                raise RuntimeFailure("recovery_required")
            if any(e["id"] == operation_id for e in data["events"]):
                raise RuntimeFailure("operation_already_recorded")
            reserve = self.limits.context_tokens + self.limits.output_tokens if model else 0
            while True:
                self.authorize(session_hash, task_id, tool, resource)
                remaining = min(
                    contract.budget.timeout_seconds - data["active_seconds"],
                    data["created_at"] + contract.budget.timeout_seconds - time.time(),
                )
                if (
                    data["steps"] >= contract.budget.max_steps
                    or data["charged_tokens"] + reserve > self.limits.token_budget
                    or remaining <= 0
                    or len(data["events"]) >= 128
                ):
                    self.terminal(uid, task_id, "budget_exhausted")
                    raise RuntimeFailure("budget_exhausted")
                gpu_before = None
                if gpu_required:
                    try:
                        gpu_before = self.gpu_admission()
                    except GpuBudgetError:
                        # Admission failure never starts a callback or invents a zero VRAM reading.
                        self.lifecycle.store.transition(
                            uid, task_id, value["state"].revision, "waiting"
                        )
                        raise RuntimeFailure("gpu_admission_denied") from None
                remaining = min(
                    remaining, data["created_at"] + contract.budget.timeout_seconds - time.time()
                )
                if remaining <= 0:
                    self.terminal(uid, task_id, "budget_exhausted")
                    raise RuntimeFailure("budget_exhausted")
                data["steps"] += 1
                data["charged_tokens"] += reserve
                data["pending"] = {"id": operation_id, "reserved_tokens": reserve, "model": model}
                self.save(contract, data)  # Durable intent before any model/tool side effect.
                started = time.monotonic()
                result = None
                error = None
                try:
                    result = await asyncio.wait_for(operation(), timeout=remaining)
                    if type(result) is not AttemptResult:
                        raise RuntimeFailure("invalid_transport_result")
                    self.authorize(session_hash, task_id, tool, resource)
                    if gpu_required:
                        snapshot = self.probe()
                        current = read_policy(self.settings)
                        cap = min(
                            gpu_before["limit_mib"],
                            current.limit_mib if current.enabled else self.limits.gpu_limit_mib,
                        )
                        if (
                            type(snapshot) is not dict
                            or type(snapshot.get("used_mib")) is not int
                            or type(snapshot.get("total_mib")) is not int
                            or snapshot["total_mib"] != gpu_before["snapshot"]["total_mib"]
                            or not 0 <= snapshot["used_mib"] <= cap
                        ):
                            raise BudgetStop("GPU admission limit exceeded or measurement invalid")
                except BaseException as failure:
                    error = failure
                data["active_seconds"] += max(0.0, time.monotonic() - started)
                measured = (
                    model
                    and result is not None
                    and type(result) is AttemptResult
                    and type(result.prompt_tokens) is int
                    and type(result.output_tokens) is int
                    and 0 <= result.prompt_tokens <= 1000000000
                    and 0 <= result.output_tokens <= 1000000000
                )
                if model:
                    if measured:
                        data["charged_tokens"] += (
                            result.prompt_tokens + result.output_tokens - reserve
                        )
                        data["measured_prompt_tokens"] += result.prompt_tokens
                        data["measured_output_tokens"] += result.output_tokens
                        data["measured_calls"] += 1
                    else:
                        data["unmeasured_calls"] += 1
                data["pending"] = None
                kind, retry = failure_kind(error) if error else ("completed", False)
                data["events"].append({"id": operation_id, "kind": kind, "measured": measured})
                self.save(contract, data)
                if error is None:
                    if (
                        data["charged_tokens"] > self.limits.token_budget
                        or data["active_seconds"] > contract.budget.timeout_seconds
                        or time.time() >= data["created_at"] + contract.budget.timeout_seconds
                    ):
                        self.terminal(uid, task_id, "budget_exhausted")
                        raise RuntimeFailure("budget_exhausted")
                    return result.value
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    self.terminal(uid, task_id, "timeout_uncertain")
                    raise error
                if isinstance(error, asyncio.CancelledError):
                    self.terminal(uid, task_id, "cancelled")
                    raise error
                if (
                    retry
                    and data["retries"] < contract.budget.max_retries
                    and data["steps"] < contract.budget.max_steps
                ):
                    data["retries"] += 1
                    self.save(contract, data)
                    await asyncio.sleep(min(0.1 * data["retries"], max(0.0, remaining)))
                    continue
                self.terminal(uid, task_id, kind)
                raise RuntimeFailure(kind) from None
