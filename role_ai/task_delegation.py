"""Opt-in common-contract adapter for the existing local Coding Agent.

Trusted Python callers only: no new HTTP execute endpoint, tool or shell command.
Public candidate checks never stand in for the independent hidden grader.
"""

import asyncio
import hashlib
import time

from role_ai.coding_agent import CodingAgent, runtime_identity
from role_ai.coding_jobs import TaskCreate, TaskBudget, ModelOptions, checksum, decode_files
from role_ai.task_contracts import contract_digest
from role_ai.task_approvals import TaskApprovalGate, ApprovalDenied
from role_ai.task_resources import TaskRuntime, AttemptResult, RuntimeFailure


class DelegationBoundary:
    def __init__(self, db, settings, session_hash, contract, reference, *, runtime=None):
        self.db, self.settings = db, settings
        self.session_hash, self.contract, self.reference = session_hash, contract, reference
        from role_ai.coding_workspace import private_directory

        private_directory(settings.coding_root)
        self.runtime = runtime or TaskRuntime(db, settings)
        self.life = self.runtime.lifecycle

    def check(self, tool):
        # Live auth, common state, pending cancel and policy on every action boundary.
        self.runtime.authorize(self.session_hash, self.contract.task_id, tool, self.reference)
        status = self.runtime.status(self.session_hash, self.contract.task_id)
        if time.time() >= status["created_at"] + self.contract.budget.timeout_seconds:
            self.runtime.terminal(self.contract.user_id, self.contract.task_id, "budget_exhausted")
            raise RuntimeFailure("budget_exhausted")


class ContractModel:
    def __init__(self, boundary, model, *, gpu_required=True):
        self.boundary, self.model = boundary, model
        self.gpu_required = gpu_required

    def complete(self, state, messages):
        b = self.boundary

        async def operation():
            # The old synchronous model performs bounded HTTP I/O. asyncio.run waits
            # for executor shutdown on cancellation; never detach unknown inference.
            reply = await asyncio.to_thread(self.model.complete, state, messages)
            directory = b.life.store.directory(b.contract.user_id, b.contract.task_id)
            b.life.store.files.atomic(directory / ("model-" + str(state["calls"]) + ".json"), reply)
            return AttemptResult(reply, reply.get("prompt_tokens"), reply.get("output_tokens"))

        return asyncio.run(
            b.runtime.attempt(
                b.session_hash,
                b.contract.task_id,
                "coding.read",
                b.reference,
                "model-" + str(state["calls"]),
                operation,
                model=True,
                gpu_required=self.gpu_required,
            )
        )

    def release(self, state):
        # Per-call keep_alive=0. Never invoke legacy generate/unload operations.
        return False


class ContractRunner:
    def __init__(self, boundary, runner):
        self.boundary, self.runner = boundary, runner

    def run(self, state, name, directory):
        self.boundary.check("coding.sandbox_run")
        result = self.runner.run(state, name, directory)
        self.boundary.check("coding.sandbox_run")
        return result

    def reconcile(self, name):
        self.boundary.check("coding.sandbox_run")
        self.runner.reconcile(name)


class ContractCodingAgent(CodingAgent):
    def __init__(self, boundary, *, model, runner, gpu_required=True):
        super().__init__(
            boundary.settings,
            model=ContractModel(boundary, model, gpu_required=gpu_required),
            runner=ContractRunner(boundary, runner),
        )
        self.boundary = boundary

    def messages(self, state):
        self.boundary.check("coding.read")
        # Even list/search/context cannot expose files outside the exact reference.
        if not set(decode_files(state["files"])).issubset(self.boundary.reference.paths):
            raise ApprovalDenied("snapshot_scope_denied")
        return super().messages(state)

    def action(self, state, directory, action):
        b = self.boundary
        tool = {"patch": "coding.propose", "test": "coding.sandbox_run"}.get(
            action.action, "coding.read"
        )
        b.check(tool)
        if action.action == "read" and action.path not in b.reference.paths:
            raise ApprovalDenied("path_denied")
        if action.action == "patch" and any(
            c.path not in b.reference.paths for c in action.changes
        ):
            raise ApprovalDenied("path_denied")
        result = super().action(state, directory, action)
        b.check(tool)
        return result


def legacy_spec(contract, reference, *, request=None):
    """Narrow metadata adaptation after authorization, not an approval mechanism."""
    if (
        contract.budget.max_steps < 2
        or contract.budget.timeout_seconds < 60
        or set(reference.paths) != {"solution.py", "test_public.py"}
    ):
        raise ApprovalDenied("benchmark_scope_denied")
    return TaskCreate(
        repository_id=reference.resource_id,
        project_id=contract.project_id,
        request=request or contract.goal,
        allowed_files=("solution.py",),
        approved=True,
        model=ModelOptions(keep_alive_seconds=0, post_task_keep_alive_seconds=0),
        budget=TaskBudget(
            max_steps=contract.budget.max_steps,
            max_retries=contract.budget.max_retries,
            model_calls=contract.budget.max_steps,
            active_seconds=contract.budget.timeout_seconds,
            deadline_seconds=contract.budget.timeout_seconds,
            command_seconds=min(30, contract.budget.timeout_seconds),
            token_budget=32768,
            max_files=min(2, contract.budget.max_files),
            patch_bytes=contract.budget.max_patch_bytes,
            memory_mib=128,
            cpu_millicores=500,
        ),
    )


def run_delegated(boundary, task_id, seed, *, model, runner, grader, gpu_required=True):
    """One explicitly queued synthetic task; retain interrupted/failed evidence.

    Collaborating callbacks are trusted, never values from a model/request. Tests
    may inject fakes; real receipts identify the grader's offline_docker scope.
    """
    from role_ai.task_evaluation_cases import task_digest

    b = boundary
    contract, ref, life = b.contract, b.reference, b.life
    if "benchmark_sha256=" + task_digest(task_id, seed) not in contract.acceptance_criteria:
        raise ApprovalDenied("benchmark_identity_denied")
    # Authorize before creating even the legacy candidate metadata/worktree.
    life.authorized(b.session_hash, contract)
    queued = life.owned(b.session_hash, contract.task_id)
    started = time.monotonic()
    report = {
        "format": "role-ai-delegation-v1",
        "task_id": task_id,
        "seed": seed,
        "task_digest": task_digest(task_id, seed),
        "contract_digest": contract_digest(contract),
        "source_revision": ref.version,
        "status": "running",
        "success": False,
        "codex_tokens": None,
        "codex_tokens_measured": False,
        "grader": None,
        "gpu_peak_mib": None,
        "gpu_peak_measured": False,
    }
    directory = life.store.directory(contract.user_id, contract.task_id)
    with life.store.files.lock(directory, "receipt"):
        if (directory / "delegation.json").exists():
            raise ApprovalDenied("delegation_receipt_exists_new_contract_required")
        life.store.files.atomic(directory / "delegation.json", report)
    state = None
    try:
        with life.claim(b.session_hash, contract.task_id, queued["state"].revision):
            b.check("coding.read")
            spec = legacy_spec(contract, ref)
            agent = ContractCodingAgent(b, model=model, runner=runner, gpu_required=gpu_required)
            state = agent.store.create(
                contract.user_id,
                spec,
                ref.version,
                checksum(str(b.settings.coding_repositories[ref.resource_id])),
                runtime_hash=runtime_identity(b.settings, ref.resource_id),
            )
            report["legacy_job_id"] = state["id"]
            life.store.files.atomic(directory / "delegation.json", report)
            state = agent.run(contract.user_id, state["id"])
            report["candidate_status"] = state["status"]
            report["iterations"] = state["steps"]
            report["usage"] = state["usage"]
            if state["status"] != "completed":
                # An old worker's completed/public result is never final success.
                b.runtime.terminal(
                    contract.user_id,
                    contract.task_id,
                    {
                        "budget_exhausted": "budget_exhausted",
                        "cancelled": "cancelled",
                        "interrupted": "timeout_uncertain",
                        "waiting": "resource_busy",
                    }.get(state["status"], "internal_error"),
                )
            else:
                b.check("coding.sandbox_run")
                value = life.owned(b.session_hash, contract.task_id)
                value = life.store.transition(
                    contract.user_id, contract.task_id, value["state"].revision, "verifying"
                )
                candidate = decode_files(state["files"])["solution.py"].decode()
                grade = grader.grade(b.session_hash, contract, ref, task_id, seed, candidate)
                report["grader"] = {k: v for k, v in grade.items() if k != "artifact_directory"}
                # Gate and control again; even an authentic passed result can be stale.
                TaskApprovalGate(b.db, b.settings).require(
                    b.session_hash, contract, "coding.sandbox_run", ref
                )
                if time.time() >= value["state"].created_at + contract.budget.timeout_seconds:
                    raise RuntimeFailure("budget_exhausted")
                if grade["success"]:
                    life.complete(
                        b.session_hash,
                        contract.task_id,
                        value["state"].revision,
                        {
                            "contract_digest": contract_digest(contract),
                            "candidate_digest": hashlib.sha256(candidate.encode()).hexdigest(),
                            "exit_code": 0,
                            "tests": grade["test_count"],
                            "runner": "independent",
                        },
                    )
                else:
                    life.store.transition(
                        contract.user_id, contract.task_id, value["state"].revision, "failed"
                    )
    except Exception as error:
        # Raw private details stay in old job/grader artifacts, never in compact reports.
        import traceback

        life.store.files.atomic(
            directory / "private-failure.json", {"traceback": traceback.format_exc()}
        )
        from role_ai.task_resources import failure_kind

        kind, _ = failure_kind(error)
        b.runtime.terminal(contract.user_id, contract.task_id, kind)
        report["error_kind"] = kind
    finally:
        current = life.store.load(contract.user_id, contract.task_id)
        report["status"] = current["state"].status
        report["success"] = current["state"].status == "succeeded"
        report["seconds"] = round(time.monotonic() - started, 6)
        # Direct store access permits failure evidence after a session is revoked.
        ledger = b.runtime.load(current)
        report["model_tokens"] = (
            ledger["measured_prompt_tokens"] + ledger["measured_output_tokens"]
            if ledger["measured_calls"] and not ledger["unmeasured_calls"]
            else None
        )
        report["charged_tokens"] = ledger["charged_tokens"]
        report["charged_tokens_are_measurement"] = False
        report["runtime_steps"] = ledger["steps"]
        life.store.files.atomic(directory / "delegation.json", report)
    return report
