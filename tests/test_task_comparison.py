"""Synthetic comparison/protocol tests, never reported as real A/B performance."""

import hashlib
import json
import threading

import pytest

from role_ai.coding_jobs import CodingJobStore
from role_ai.task_comparison import (
    export_public,
    baseline_candidate,
    compare,
    conditions_digest,
    JOBS,
)
from role_ai.task_evaluation_cases import TASKS, task_digest, private_cases
from role_ai.task_evaluation import MARKER
from role_ai.task_metrics import MetricRecord, implementation_digest, aggregate
from role_ai.task_baseline import grade_baseline

IMAGE = "python:3.12-slim@sha256:" + "1" * 64


def dataset(**changes):
    result = []
    for approach in ("codex_direct", "local_delegate"):
        for item in TASKS:
            result.append(
                MetricRecord(
                    run_id=hashlib.sha256((approach + item.id).encode()).hexdigest(),
                    task_id=item.id,
                    task_digest=task_digest(item.id, 7),
                    seed=7,
                    approach=approach,
                    evidence="actual_run",
                    status="succeeded",
                    success=True,
                    tests_passed=6,
                    tests_total=6,
                    test_evidence="offline_docker",
                    model_tokens=None,
                    charged_tokens=None,
                    codex_tokens=None,
                    model_calls=None,
                    runtime_steps=None,
                    recovery_attempts=None,
                    recovered_success=None,
                    wall_seconds=None,
                    gpu_samples=0,
                    gpu_measurement_errors=0,
                    gpu_sampling_complete=False,
                    model_digest="c" * 64 if approach == "local_delegate" else None,
                    implementation_digest=implementation_digest(),
                    conditions_digest=conditions_digest(item.id, 7, IMAGE),
                )
            )
    return [r.model_copy(update=changes) for r in result] if changes else result


def ci_receipt():
    return {
        "implementation_digest": implementation_digest(),
        "head_sha": "1" * 40,
        "jobs": [
            {"name": name, "conclusion": "success", "head_sha": "1" * 40, "run_id": i + 1}
            for i, name in enumerate(sorted(JOBS))
        ],
    }


def protocol():
    return {
        "public_manifest_sha256": "a" * 64,
        "a_context_review_sha256": "b" * 64,
        "hardware_conditions_sha256": "c" * 64,
    }


def measured_dataset():
    return [
        r.model_copy(
            update={
                "codex_tokens": 100 if r.approach == "codex_direct" else 20,
                "codex_usage_receipt_sha256": "f" * 64,
            }
        )
        for r in dataset()
    ]


def test_public_package_has_ten_goals_stubs_public_tests_and_no_hidden_source(tmp_path):
    root = tmp_path / "public"
    manifest = export_public(root, seed=7, image=IMAGE)
    assert len(manifest["cases"]) == 10
    assert len(list(root.rglob("*.py"))) == 20
    assert (root / "manifest.json").stat().st_mode & 0o777 == 0o600
    for item in TASKS:
        _, source = baseline_candidate(root, item.id)
        assert "raise NotImplementedError" in source
        assert "arguments_after" not in (root / item.id / "test_public.py").read_text()
    with pytest.raises(ValueError, match="overwrite"):
        export_public(root, seed=7, image=IMAGE)


@pytest.mark.parametrize("kind", ["tests", "goal", "symlink", "extra", "manifest"])
def test_public_fixture_tampering_or_extra_sources_fail_closed(tmp_path, kind):
    root = tmp_path / "public"
    export_public(root, seed=7, image=IMAGE)
    directory = root / "budget-check"
    if kind == "tests":
        (directory / "test_public.py").write_text("pass")
    elif kind == "goal":
        (directory / "GOAL.md").write_text("unsafe override")
    elif kind == "extra":
        (directory / "credentials.json").write_text("synthetic")
    elif kind == "symlink":
        (directory / "solution.py").unlink()
        (directory / "solution.py").symlink_to(root / "README.md")
    else:
        store = CodingJobStore(root)
        manifest = store.read(root / "manifest.json")
        manifest["implementation_digest"] = "0" * 64
        store.atomic(root / "manifest.json", manifest)
    with pytest.raises(ValueError):
        baseline_candidate(root, "budget-check")


def test_missing_real_data_cannot_be_called_complete_or_improved():
    report = compare([], seed=7, image=IMAGE)
    assert report["conclusion"] == "incomplete" and not report["improvement"]
    assert report["codex_token_reduction"] is None


def test_synthetic_records_are_excluded_even_when_scores_look_perfect():
    report = compare(dataset(evidence="scripted_ci"), seed=7, image=IMAGE)
    assert report["synthetic_runs_excluded"] == 20 and report["conclusion"] == "incomplete"
    assert report["a"]["runs"] == report["b"]["runs"] == 0


def test_equal_quality_without_codex_usage_remains_inconclusive():
    report = compare(
        dataset(), seed=7, image=IMAGE, regression_receipt=ci_receipt(), protocol_receipt=protocol()
    )
    assert report["paired_coverage_complete"] and report["conclusion"] == "inconclusive"
    assert report["blocking_reasons"] == ["codex_usage_unmeasured"]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("seed", 8, "task_or_seed_mismatch"),
        ("task_digest", "0" * 64, "benchmark_digest_mismatch"),
        ("conditions_digest", "0" * 64, "runtime_conditions_mismatch"),
        ("implementation_digest", "0" * 64, "implementation_mismatch"),
        ("tests_total", 7, "independent_evidence_mismatch"),
    ],
)
def test_changed_tasks_conditions_or_code_are_not_comparable(field, value, reason):
    rows = measured_dataset()
    rows[0] = rows[0].model_copy(update={field: value})
    # tests_total 7 also needs a coherent success value for schema validation.
    if field == "tests_total":
        rows[0] = rows[0].model_copy(update={"tests_passed": 7})
    result = compare(rows, seed=7, image=IMAGE)
    assert reason in result["blocking_reasons"] and not result["improvement"]


def test_lower_token_count_cannot_hide_quality_regression():
    rows = measured_dataset()
    rows[-1] = rows[-1].model_copy(update={"status": "failed", "success": False, "tests_passed": 3})
    result = compare(
        rows, seed=7, image=IMAGE, regression_receipt=ci_receipt(), protocol_receipt=protocol()
    )
    assert result["conclusion"] == "regression" and not result["improvement"]


def test_unequal_repeats_or_duplicate_runs_cannot_bias_comparison():
    rows = measured_dataset()
    assert compare(rows[:-1], seed=7, image=IMAGE)["conclusion"] == "incomplete"
    with pytest.raises(ValueError, match="Duplicate"):
        compare(rows + rows[:1], seed=7, image=IMAGE)


def test_actual_usage_still_requires_blind_protocol_and_all_six_ci_checks():
    rows = measured_dataset()
    no_protocol = compare(rows, seed=7, image=IMAGE, regression_receipt=ci_receipt())
    assert "blindness_or_hardware_protocol_unreviewed" in no_protocol["blocking_reasons"]
    no_ci = compare(rows, seed=7, image=IMAGE, protocol_receipt=protocol())
    assert no_ci["conclusion"] == "inconclusive" and not no_ci["regression_ci_verified"]
    invalid_ci = ci_receipt()
    invalid_ci["jobs"][0]["conclusion"] = "failure"
    assert not compare(
        rows, seed=7, image=IMAGE, regression_receipt=invalid_ci, protocol_receipt=protocol()
    )["improvement"]


def test_complete_synthetic_fixture_proves_logic_only_not_actual_performance():
    result = compare(
        measured_dataset(),
        seed=7,
        image=IMAGE,
        regression_receipt=ci_receipt(),
        protocol_receipt=protocol(),
    )
    assert result["conclusion"] == "improved_under_recorded_conditions"
    assert result["codex_token_reduction"] == pytest.approx(0.8)
    # These are fabricated test fixtures, never exported as benchmark measurements.


def test_direct_baseline_unknown_calls_time_and_recovery_remain_null(tmp_path):
    root = tmp_path / "public"
    export_public(root, seed=7, image=IMAGE)
    (root / "budget-check" / "solution.py").write_text("def budget_admission(*args): return True\n")

    class Executor:
        lock = threading.Lock()
        blocked = False

        def execute(self, request):
            return {
                "status": "completed",
                "executed": True,
                "exit_code": 0,
                "output": MARKER + json.dumps([v for _, v in private_cases("budget-check", 7)]),
                "output_truncated": False,
                "image": IMAGE,
            }

    result = grade_baseline(root, "budget-check", executor=Executor())
    metric = MetricRecord.model_validate_json(json.dumps(result["metrics"]))
    assert metric.success and metric.evidence == "test_double"
    assert (
        metric.model_calls is None and metric.wall_seconds is None and metric.codex_tokens is None
    )
    assert aggregate([metric])["runs"] == 0
    assert aggregate([metric], evidence="test_double")["recovery_unknown_runs"] == 1


def test_all_failed_delegations_never_count_as_token_improvement():
    rows = [
        r.model_copy(update={"status": "failed", "success": False, "tests_passed": 0})
        for r in measured_dataset()
    ]
    result = compare(
        rows, seed=7, image=IMAGE, regression_receipt=ci_receipt(), protocol_receipt=protocol()
    )
    assert not result["improvement"] and "no_successful_delegate" in result["blocking_reasons"]


def test_cli_regression_has_nonzero_exit_even_when_receipts_parse(tmp_path, monkeypatch, capsys):
    from role_ai.task_comparison import main

    rows = measured_dataset()
    rows[-1] = rows[-1].model_copy(update={"status": "failed", "success": False, "tests_passed": 0})
    root = tmp_path / "receipts"
    store = CodingJobStore(root)
    paths = []
    for r in rows:
        path = root / (r.run_id + ".json")
        store.atomic(path, r.model_dump(mode="json"))
        paths.append(str(path))
    monkeypatch.setattr("sys.argv", ["compare", "compare", "--image", IMAGE, "--metrics", *paths])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["conclusion"] == "regression"


def test_producer_receipt_cannot_be_reused_for_different_candidate(tmp_path):
    root = tmp_path / "public"
    export_public(root, seed=7, image=IMAGE)
    with pytest.raises(ValueError, match="does not match"):
        grade_baseline(root, "budget-check", producer_receipt={"format": "stale"})
