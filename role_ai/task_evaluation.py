"""Independent outside-container oracle; expected answers never enter candidate execution."""

import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace

from role_ai.code_execution import CodeExecutor, ExecutionRequest, ExecutionUnavailable
from role_ai.coding_jobs import CodingJobStore, TaskCreate
from role_ai.coding_workspace import private_directory, git, MAX_FILE_BYTES
from role_ai.coding_project import repository
from role_ai.task_approvals import TaskApprovalGate, ApprovalDenied
from role_ai.task_evaluation_cases import task, private_cases, arguments, task_digest

MARKER = "__ROLEAI_OBSERVATIONS__="


def probe_tests(task_id, seed=0):
    definition = task(task_id)
    # Inputs only: no oracle implementation or expected values in this container.
    inputs = [args for args, _ in private_cases(task_id, seed)]
    return (
        """import copy,json,math,unittest,importlib.util
assert importlib.util.find_spec('role_ai') is None, 'Benchmark oracle package must not be in the candidate image'
import submission

def clean(value):
    if value is None or type(value) in (bool,int,str):return value
    if type(value) is float and math.isfinite(value):return value
    if type(value) is list:return [clean(x) for x in value]
    if type(value) is dict and all(type(k) is str for k in value):return {k:clean(v) for k,v in value.items()}
    raise TypeError('non JSON result')

class Probe(unittest.TestCase):
    def test_observations(self):
        rows=[]
        inputs=json.loads("""
        + repr(json.dumps(inputs, ensure_ascii=False))
        + """)
        for original in inputs:
            args=copy.deepcopy(original)
            try:row={'value':clean(getattr(submission,"""
        + repr(definition.function.__name__)
        + """)(*args))}
            except BaseException as error:row={'error':type(error).__name__}
            rows.append({**row,'arguments_after':clean(args)})
        print("""
        + repr(MARKER)
        + """+json.dumps(rows,sort_keys=True,ensure_ascii=False,allow_nan=False),flush=True)
"""
    )


def observations(output, count):
    if type(output) is not str:
        raise ValueError("invalid runner output")
    lines = [line[len(MARKER) :] for line in output.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise ValueError("one observation vector required")

    def bad_constant(_):
        raise ValueError("nonfinite result")

    value = json.loads(lines[0], parse_constant=bad_constant)
    if type(value) is not list or len(value) != count:
        raise ValueError("incomplete observation vector")
    for item in value:
        if type(item) is not dict or set(item) not in (
            {"value", "arguments_after"},
            {"error", "arguments_after"},
        ):
            raise ValueError("invalid observation shape")
    return value


def equal_observation(actual, expected):
    return json.dumps(
        actual, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ) == json.dumps(
        expected, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


class IndependentGrader:
    """Synthetic benchmark only, actual current approval required before/after Docker.

    Executor injection is trusted test infrastructure, never an HTTP/model argument.
    """

    def __init__(self, db, settings, *, executor=None, output_root=None):
        self.db, self.settings = db, settings
        self.executor = executor or CodeExecutor(settings)
        self.test_double = executor is not None
        self.root = Path(
            output_root or Path(settings.storage_dir) / "coding-evaluations"
        ).absolute()

    def authorize(self, session_hash, contract, reference, task_id, seed, path, candidate):
        body, ref = TaskApprovalGate(self.db, self.settings).require(
            session_hash, contract, "coding.sandbox_run", reference
        )
        if ("benchmark_sha256=" + task_digest(task_id, seed)) not in body.acceptance_criteria:
            raise ApprovalDenied("benchmark_mismatch")
        if (
            path not in ref.paths
            or type(candidate) is not str
            or len(candidate) > 16000
            or len(candidate.encode()) > body.budget.max_patch_bytes
            or not self.settings.code_execution_enabled
            or self.settings.code_execution_image != self.settings.coding_image
        ):
            raise ApprovalDenied("candidate_or_runner_denied")
        source = repository(self.settings, ref.resource_id)
        object_id = ref.version + ":" + path
        if int(git(source, "cat-file", "-s", object_id)) > MAX_FILE_BYTES:
            raise ApprovalDenied("source_budget_exhausted")
        if not self.test_double and not self.settings.coding_image.startswith(
            "python:3.12-slim@sha256:"
        ):
            raise ApprovalDenied("isolated_benchmark_runtime_required")
        original = git(source, "show", object_id)
        if candidate.encode() != original:
            if len(original) + len(candidate.encode()) > body.budget.max_patch_bytes:
                raise ApprovalDenied("patch_budget_exhausted")
            TaskApprovalGate(self.db, self.settings).require(
                session_hash, body, "coding.propose", ref
            )
            TaskCreate(
                repository_id=ref.resource_id,
                project_id=body.project_id,
                request=body.goal,
                allowed_files=(path,),
            )
        return body, ref

    def grade(
        self, session_hash, contract, reference, task_id, seed, candidate, *, path="solution.py"
    ):
        body, reference = self.authorize(
            session_hash, contract, reference, task_id, seed, path, candidate
        )
        expected = private_cases(task_id, seed)
        public, hidden = arguments(task_id, seed)
        # Allocate only after authentication/approval, with server-owned path/ID.
        root = private_directory(self.root)
        files = CodingJobStore(root)
        namespace = private_directory(root / hashlib.sha256(body.user_id.encode()).hexdigest())
        with files.lock(root, "create"):
            if len(list(namespace.iterdir())) >= 64:
                raise ValueError("Evaluation retention quota reached; no previous report removed")
            directory = private_directory(namespace / uuid4().hex)
        report = {
            "format": "role-ai-independent-grade-v1",
            "id": directory.name,
            "task_id": task_id,
            "task_digest": task_digest(task_id, seed),
            "seed": seed,
            "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
            "source_revision": reference.version,
            "status": "running",
            "success": False,
            "executed": None,
            "public_total": len(public),
            "hidden_total": len(hidden),
            "public_passed": None,
            "hidden_passed": None,
            "test_count": 0,
            "seconds": None,
            "evidence_scope": "test_double" if self.test_double else "offline_docker",
            "codex_tokens": None,
        }
        files.atomic(directory / "report.json", report)
        descriptor = os.open(
            directory / "candidate.py",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        started = time.monotonic()
        raw = None
        try:
            # Same digest-pinned, offline Python/unittest template; no host fallback or new shell command.
            if (Path(self.settings.coding_root) / "runner-cleanup-required").exists():
                raise ExecutionUnavailable("Runner cleanup requires inspection")
            if not self.executor.lock.acquire(blocking=False):
                raise ExecutionUnavailable("Runner busy")
            try:
                if not self.test_double:
                    self.executor.settings = SimpleNamespace(
                        mode=self.settings.mode,
                        code_execution_enabled=self.settings.code_execution_enabled,
                        code_execution_image=self.settings.code_execution_image,
                        code_execution_socket=self.settings.code_execution_socket,
                        code_execution_timeout_seconds=min(
                            self.settings.code_execution_timeout_seconds,
                            body.budget.timeout_seconds,
                        ),
                    )
                raw = self.executor.execute(
                    ExecutionRequest(
                        code=candidate, tests=probe_tests(task_id, seed), approved=True
                    )
                )
            finally:
                self.executor.lock.release()
            if type(raw) is not dict:
                raise ValueError("invalid transport")
            report["executed"] = raw.get("executed") if type(raw.get("executed")) is bool else None
            if type(raw.get("output")) is str:
                descriptor = os.open(
                    directory / "raw.log",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(raw["output"])
                    stream.flush()
                    os.fsync(stream.fileno())
            self.authorize(session_hash, body, reference, task_id, seed, path, candidate)
            if (
                raw.get("status") != "completed"
                or raw.get("executed") is not True
                or type(raw.get("exit_code")) is not int
                or raw["exit_code"] != 0
                or raw.get("output_truncated") is not False
                or raw.get("image") != self.settings.coding_image
            ):
                report["status"] = "interrupted"
            else:
                actual = observations(raw["output"], len(expected))
                passed = [equal_observation(row, want) for row, (_, want) in zip(actual, expected)]
                report["public_passed"] = sum(passed[: len(public)])
                report["hidden_passed"] = sum(passed[len(public) :])
                report["test_count"] = len(passed)
                report["success"] = all(passed)
                report["status"] = "passed" if report["success"] else "failed"
        except ApprovalDenied:
            report["status"] = "blocked"
            report["success"] = False
        except ExecutionUnavailable:
            report["status"] = "interrupted"
            if getattr(self.executor, "blocked", False):
                marker = (
                    private_directory(Path(self.settings.coding_root)) / "runner-cleanup-required"
                )
                try:
                    descriptor = os.open(
                        marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                    )
                    with os.fdopen(descriptor, "w") as stream:
                        stream.write(
                            "Independent runner cleanup is uncertain; inspect before any new execution.\n"
                        )
                except FileExistsError:
                    pass
        except (ValueError, TypeError, KeyError, AttributeError):
            report["status"] = "invalid_evidence"
        except BaseException:
            report["status"] = "interrupted"
            report["seconds"] = max(0.0, time.monotonic() - started)
            files.atomic(directory / "report.json", report)
            raise
        report["seconds"] = max(0.0, time.monotonic() - started)
        files.atomic(directory / "report.json", report)
        return {**report, "artifact_directory": str(directory)}
