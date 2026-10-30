"""Synthetic end-to-end adapters; no model or host candidate execution."""

import json
import subprocess
import threading

import pytest

from role_ai.coding_jobs import decode_files
from role_ai.task_contracts import ResourceRef, ResourceBudget
from role_ai.task_approvals import ApprovalService, ApprovalDenied
from role_ai.task_delegation import DelegationBoundary, run_delegated
from role_ai.task_evaluation import IndependentGrader, MARKER
from role_ai.task_evaluation_cases import public_task, task_digest, private_cases

pytest_plugins = ("test_task_approvals",)
SOLUTION = "def budget_admission(used, requested, limit):\n    return used + requested <= limit\n"


@pytest.fixture
def delegation(coding_access, tmp_path):
    db, settings, body, _, source = coding_access
    (source / "app.py").unlink()
    public = public_task("budget-check", 7)
    for name, text in public["files"].items():
        (source / name).write_text(text)
    for args in [
        ("add", "-A"),
        (
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@test.invalid",
            "commit",
            "-qm",
            "synthetic benchmark",
        ),
    ]:
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    settings.storage_dir = str(tmp_path / "artifacts")
    settings.code_execution_enabled = True
    settings.code_execution_image = settings.coding_image
    ref = ResourceRef(
        kind="repository",
        resource_id="fixture",
        version=sha,
        paths=("solution.py", "test_public.py"),
    )
    body = body.model_copy(
        update={
            "task_id": "delegate",
            "approval_reference": "delegate",
            "goal": public["goal"],
            "budget": ResourceBudget(max_steps=8, max_retries=0, max_files=2, timeout_seconds=120),
            "allowed_resources": (ref,),
            "acceptance_criteria": ("benchmark_sha256=" + task_digest("budget-check", 7),),
        }
    )
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    from role_ai.coding_workspace import private_directory

    private_directory(settings.coding_root)
    b = DelegationBoundary(db, settings, "a" * 64, body, ref)
    b.life.create("a" * 64, body)
    b.life.queue("a" * 64, body.task_id, 0)
    return b, source


class ScriptedModel:
    def __init__(self, hook=None, actions=None, measured=True):
        self.hook, self.measured = hook, measured
        self.actions = iter(
            actions
            or [
                {"action": "plan", "plan": ["implement", "test"]},
                {"action": "patch", "changes": [{"path": "solution.py", "content": SOLUTION}]},
                {"action": "test"},
                {"action": "finish", "summary": "candidate ready"},
            ]
        )
        self.calls, self.prompts = 0, []

    def complete(self, state, messages):
        self.calls += 1
        self.prompts.append(json.dumps(messages))
        if self.hook:
            self.hook(self.calls)
        return {
            "content": json.dumps(next(self.actions)),
            "prompt_tokens": 11 if self.measured else None,
            "output_tokens": 7 if self.measured else None,
        }


class PublicRunner:
    def run(self, state, name, directory):
        assert set(decode_files(state["files"])) == {"solution.py", "test_public.py"}
        return {
            "status": "passed",
            "count": 2,
            "revision": state["revision"],
            "reason": "completed",
            "exit_code": 0,
            "output": "synthetic public evidence",
        }


def grader(boundary, *, passed=True, hook=None):
    class Executor:
        lock = threading.Lock()
        blocked = False

        def execute(self, request):
            if hook:
                hook()
            answers = [answer for _, answer in private_cases("budget-check", 7)]
            if not passed:
                answers[0] = {"value": "wrong", "arguments_after": []}
            return {
                "status": "completed",
                "executed": True,
                "exit_code": 0,
                "output": MARKER + json.dumps(answers),
                "output_truncated": False,
                "image": boundary.settings.coding_image,
            }

    return IndependentGrader(boundary.db, boundary.settings, executor=Executor())


def execute(b, model=None, **kwargs):
    return run_delegated(
        b,
        "budget-check",
        7,
        model=model or ScriptedModel(),
        runner=PublicRunner(),
        grader=grader(b, **kwargs),
        gpu_required=False,
    )


def test_common_pipeline_requires_independent_success_and_preserves_source(delegation):
    b, source = delegation
    original = (source / "solution.py").read_bytes()
    model = ScriptedModel()
    report = execute(b, model)
    assert report["success"] and report["status"] == "succeeded"
    assert report["model_tokens"] == 72 and report["runtime_steps"] == 4
    assert report["grader"]["hidden_passed"] == 4
    assert report["grader"]["evidence_scope"] == "test_double"
    assert report["codex_tokens"] is None and report["gpu_peak_mib"] is None
    assert (source / "solution.py").read_bytes() == original
    assert all(
        "arguments_after" not in prompt and "hidden" not in prompt for prompt in model.prompts
    )


def test_public_success_cannot_override_hidden_failure(delegation):
    b, _ = delegation
    result = execute(b, passed=False)
    assert not result["success"] and result["candidate_status"] == "completed"
    assert result["status"] == "failed" and result["grader"]["hidden_passed"] is not None


def test_unknown_tokens_remain_unmeasured_and_reserved(delegation):
    b, _ = delegation
    result = execute(b, ScriptedModel(measured=False))
    assert result["success"] and result["model_tokens"] is None
    assert result["charged_tokens"] == 4 * 5120


@pytest.mark.parametrize("path", ["test_public.py", "../secret.py", "other.py"])
def test_model_cannot_write_tests_or_escape_exact_contract(delegation, path):
    b, source = delegation
    model = ScriptedModel(
        actions=[
            {"action": "plan", "plan": ["patch"]},
            {"action": "patch", "changes": [{"path": path, "content": "bad"}]},
        ]
    )
    original = (source / "test_public.py").read_bytes()
    result = execute(b, model)
    assert not result["success"] and result["grader"] is None
    assert model.calls == 2 and (source / "test_public.py").read_bytes() == original


def test_cancel_after_model_discards_output_before_patch(delegation):
    b, _ = delegation

    # Use the common store's fixed task directory; a model callback cannot grant approval.
    def control(calls):
        if calls == 2:
            directory = b.life.store.directory(b.contract.user_id, b.contract.task_id)
            b.life.store.files.atomic(directory / "control.json", {"action": "cancel"})

    result = execute(b, ScriptedModel(hook=control))
    assert not result["success"] and result["grader"] is None
    assert result["status"] == "cancelled"


def test_revocation_during_independent_grading_prevents_completion(delegation):
    b, _ = delegation

    def revoke():
        ApprovalService(b.db, b.settings).revoke("a" * 64, b.contract.approval_reference)
        b.db.commit()

    result = execute(b, hook=revoke)
    assert not result["success"] and result["status"] in {"blocked", "interrupted"}


def test_missing_current_approval_never_creates_legacy_job(delegation):
    b, _ = delegation
    ApprovalService(b.db, b.settings).revoke("a" * 64, b.contract.approval_reference)
    b.db.commit()
    model = ScriptedModel()
    with pytest.raises(ApprovalDenied):
        execute(b, model)
    assert model.calls == 0


def test_no_hidden_grading_or_success_when_step_budget_exhausts(delegation):
    b, _ = delegation
    model = ScriptedModel(actions=[{"action": "list"}] * 8)
    result = execute(b, model)
    assert not result["success"] and result["grader"] is None
    assert result["runtime_steps"] <= b.contract.budget.max_steps
