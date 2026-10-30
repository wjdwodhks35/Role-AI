"""Synthetic transport tests; actual offline Docker grading has a separate CI probe."""

import json
import threading
from pathlib import Path
import pytest
from role_ai.task_evaluation import IndependentGrader, MARKER, probe_tests
from role_ai.task_evaluation_cases import (
    TASKS,
    task_digest,
    public_task,
    private_cases,
    reference_source,
)
from role_ai.task_approvals import ApprovalService, ApprovalDenied
from role_ai.code_execution import ExecutionUnavailable

pytest_plugins = ("test_task_approvals",)


class FakeExecutor:
    def __init__(self, result):
        self.result = result
        self.lock = threading.Lock()
        self.blocked = False
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return self.result(request) if callable(self.result) else self.result


@pytest.fixture
def grading(coding_access, tmp_path):
    db, settings, body, ref, source = coding_access
    settings.storage_dir = str(tmp_path / "artifacts")
    settings.code_execution_enabled = True
    settings.code_execution_image = settings.coding_image

    def approve(task_id="budget-check", seed=7):
        public = public_task(task_id, seed)
        spec = body.model_copy(
            update={
                "task_id": "grade-" + task_id,
                "approval_reference": "grade-" + task_id,
                "goal": public["goal"],
                "acceptance_criteria": ("benchmark_sha256=" + task_digest(task_id, seed),),
            }
        )
        ApprovalService(db, settings).issue("a" * 64, spec)
        db.commit()
        return spec

    return db, settings, ref, source, approve


def raw(settings, output):
    return {
        "status": "completed",
        "executed": True,
        "exit_code": 0,
        "output": output,
        "output_truncated": False,
        "image": settings.coding_image,
    }


def test_ten_mixed_tasks_are_reproducible_and_public_view_excludes_oracle():
    assert len(TASKS) == 10 and len({t.id for t in TASKS}) == 10
    assert sum(t.category == "role_ai" for t in TASKS) == 5
    for item in TASKS:
        public = public_task(item.id, 7)
        assert set(public["files"]) == {"solution.py", "test_public.py"}
        assert "oracle" not in public and "hidden" not in public
        assert public["public_cases"] == 2 and len(private_cases(item.id, 7)) == 6
        assert task_digest(item.id, 7) == task_digest(item.id, 7)
        assert task_digest(item.id, 7) != task_digest(item.id, 8)
        compile(public["files"]["solution.py"], "trusted stub", "exec")
        compile(public["files"]["test_public.py"], "trusted public tests", "exec")
        probe = probe_tests(item.id, 7)
        assert reference_source(item.id) not in probe
        assert len(probe) < 16000
        compile(probe, "trusted observation probe", "exec")


@pytest.mark.parametrize("task_id", [t.id for t in TASKS])
def test_external_oracle_compares_all_cases_without_accepting_model_scores(grading, task_id):
    db, settings, ref, source, approve = grading
    body = approve(task_id)
    expected = [answer for _, answer in private_cases(task_id, 7)]
    executor = FakeExecutor(raw(settings, MARKER + json.dumps(expected)))
    grader = IndependentGrader(db, settings, executor=executor)
    before = (source / "app.py").read_bytes()
    report = grader.grade("a" * 64, body, ref, task_id, 7, reference_source(task_id), path="app.py")
    assert report["success"] is True and report["test_count"] == 6
    assert report["public_passed"] == 2 and report["hidden_passed"] == 4
    assert report["evidence_scope"] == "test_double" and report["codex_tokens"] is None
    directory = Path(report["artifact_directory"])
    assert (directory / "candidate.py").is_file() and (directory / "raw.log").is_file()
    assert (directory / "report.json").stat().st_mode & 0o777 == 0o600
    assert (source / "app.py").read_bytes() == before


@pytest.mark.parametrize(
    "case",
    ["self_score", "missing", "duplicate", "partial", "wrong", "timeout", "boolean_exit", "image"],
)
def test_fake_scores_missing_evidence_or_failure_cannot_be_success(grading, case):
    db, settings, ref, _, approve = grading
    body = approve()
    expected = [answer for _, answer in private_cases("budget-check", 7)]
    output = MARKER + json.dumps(expected)
    result = raw(settings, output)
    if case == "self_score":
        result["output"] = MARKER + json.dumps({"passed": True})
    elif case == "missing":
        result["output"] = "all tests passed"
    elif case == "duplicate":
        result["output"] = output + "\n" + output
    elif case == "partial":
        result["output"] = MARKER + json.dumps(expected[:-1])
    elif case == "wrong":
        expected[0]["value"] = False
        result["output"] = MARKER + json.dumps(expected)
    elif case == "timeout":
        result["status"] = "timeout"
    elif case == "boolean_exit":
        result["exit_code"] = False
    elif case == "image":
        result["image"] = "unapproved:latest"
    report = IndependentGrader(db, settings, executor=FakeExecutor(result)).grade(
        "a" * 64, body, ref, "budget-check", 7, reference_source("budget-check"), path="app.py"
    )
    assert report["success"] is False
    assert report["status"] in {"invalid_evidence", "failed", "interrupted"}
    assert (Path(report["artifact_directory"]) / "report.json").is_file()


def test_seed_or_task_or_path_cannot_be_changed_after_approval(grading):
    db, settings, ref, _, approve = grading
    body = approve()
    executor = FakeExecutor(None)
    grader = IndependentGrader(db, settings, executor=executor)
    with pytest.raises(ApprovalDenied):
        grader.grade("a" * 64, body, ref, "budget-check", 8, "x", path="app.py")
    with pytest.raises(ApprovalDenied):
        grader.grade("a" * 64, body, ref, "pagination", 7, "x", path="app.py")
    with pytest.raises(ApprovalDenied):
        grader.grade("a" * 64, body, ref, "budget-check", 7, "x", path="other.py")
    assert not executor.calls


def test_revocation_while_runner_executes_retains_audit_but_blocks_result(grading):
    db, settings, ref, _, approve = grading
    body = approve()

    def revoke(_):
        ApprovalService(db, settings).revoke("a" * 64, body.approval_reference)
        db.commit()
        return raw(settings, MARKER + json.dumps([x for _, x in private_cases("budget-check", 7)]))

    report = IndependentGrader(db, settings, executor=FakeExecutor(revoke)).grade(
        "a" * 64, body, ref, "budget-check", 7, reference_source("budget-check"), path="app.py"
    )
    assert report["status"] == "blocked" and not report["success"]
    assert (Path(report["artifact_directory"]) / "raw.log").is_file()


def test_cleanup_uncertainty_blocks_next_runner_without_auto_recovery(grading):
    db, settings, ref, _, approve = grading
    body = approve()

    def failed(_):
        executor.blocked = True
        raise ExecutionUnavailable("private details")

    executor = FakeExecutor(failed)
    grader = IndependentGrader(db, settings, executor=executor)
    first = grader.grade(
        "a" * 64, body, ref, "budget-check", 7, reference_source("budget-check"), path="app.py"
    )
    second = grader.grade(
        "a" * 64, body, ref, "budget-check", 7, reference_source("budget-check"), path="app.py"
    )
    assert first["status"] == second["status"] == "interrupted" and len(executor.calls) == 1
    assert (Path(settings.coding_root) / "runner-cleanup-required").is_file()


def test_input_mutation_and_numeric_type_changes_are_detected(grading):
    db, settings, ref, _, approve = grading
    body = approve("pagination")
    expected = [answer for _, answer in private_cases("pagination", 7)]
    expected[0]["arguments_after"][0].append("mutated")
    report = IndependentGrader(
        db, settings, executor=FakeExecutor(raw(settings, MARKER + json.dumps(expected)))
    ).grade("a" * 64, body, ref, "pagination", 7, reference_source("pagination"), path="app.py")
    assert not report["success"] and report["public_passed"] == 1


def test_real_grader_rejects_nonisolated_application_image_before_execution(grading, monkeypatch):
    db, settings, ref, _, approve = grading
    body = approve()

    def forbidden(*_):
        raise AssertionError("must not execute an application image")

    monkeypatch.setattr("role_ai.code_execution.CodeExecutor.execute", forbidden)
    with pytest.raises(ApprovalDenied, match="isolated_benchmark_runtime_required"):
        IndependentGrader(db, settings).grade(
            "a" * 64, body, ref, "budget-check", 7, reference_source("budget-check"), path="app.py"
        )


def test_oracle_public_answers_match_independent_spec_examples():
    answers = {
        "scope-read": [["doc"], ["p"]],
        "memory-revision": [
            [{"revision": 1, "content": "first"}],
            [{"revision": 1, "content": "old"}, {"revision": 2, "content": "new"}],
        ],
        "conversation-bound": [True, False],
        "budget-check": [True, False],
        "safe-log-fold": [["PASS x3"], ["FAIL", "FAIL"]],
        "stable-dedup": [["b", "a"], []],
        "merge-intervals": [[[1, 5]], []],
        "duration-parser": [3723, 0],
        "stable-topology": [["a", "b"], []],
        "pagination": [
            {"items": [1, 2], "total": 3, "pages": 2},
            {"items": [], "total": 0, "pages": 0},
        ],
    }
    for task_id, expected in answers.items():
        cases = private_cases(task_id, 7)
        assert [item[1].get("value") for item in cases[:2]] == expected
        assert all(item[0] == item[1]["arguments_after"] for item in cases)
