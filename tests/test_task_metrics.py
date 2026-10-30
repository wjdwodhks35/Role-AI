"""Metric provenance/denominators and read-only GPU observation; no model calls."""

import time
import pytest
from pydantic import ValidationError
from role_ai.task_metrics import MetricRecord, GpuSampler, aggregate, measure_report
from role_ai.task_approvals import ApprovalDenied

pytest_plugins = ("test_task_delegation",)


def record(**changes):
    fields = dict(
        run_id="a" * 64,
        task_id="budget-check",
        task_digest="b" * 64,
        seed=7,
        approach="local_delegate",
        evidence="actual_run",
        status="succeeded",
        success=True,
        tests_passed=6,
        tests_total=6,
        test_evidence="offline_docker",
        model_tokens=123,
        charged_tokens=5120,
        model_calls=4,
        runtime_steps=4,
        recovery_attempts=0,
        recovered_success=False,
        wall_seconds=3.0,
        observed_device_peak_mib=None,
        gpu_samples=0,
        gpu_measurement_errors=1,
        gpu_sampling_complete=False,
        model_digest="c" * 64,
        implementation_digest="d" * 64,
        conditions_digest="e" * 64,
    )
    return MetricRecord(**(fields | changes))


def test_unknown_tokens_and_missing_scores_never_become_zero_or_success():
    result = aggregate(
        [
            record(),
            record(
                run_id="f" * 64,
                status="blocked",
                success=False,
                tests_passed=None,
                test_evidence="not_run",
                model_tokens=None,
                model_calls=0,
            ),
        ]
    )
    assert result["success_rate_all_attempts"] == 0.5
    assert result["test_pass_rate_scored"] == 1 and result["unscored_runs"] == 1
    assert result["model_tokens"] is None and result["codex_tokens"] is None
    assert result["codex_token_reduction"] is None


def test_synthetic_runs_cannot_inflate_actual_performance_and_duplicates_rejected():
    r = record(evidence="scripted_ci")
    summary = aggregate([r])
    assert summary["runs"] == 0 and summary["success_rate_all_attempts"] is None
    assert summary["excluded_synthetic_runs"] == 1
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate([r, r])


@pytest.mark.parametrize(
    "changes",
    [
        {"model_tokens": True},
        {"wall_seconds": float("nan")},
        {"tests_passed": 7},
        {"status": "failed"},
        {"codex_tokens": 123},
        {"test_evidence": "test_double"},
        {"gpu_samples": 1},
        {"gpu_sampling_complete": True},
        {"recovered_success": True},
    ],
)
def test_invalid_or_unproven_measurements_are_rejected(changes):
    with pytest.raises(ValidationError):
        record(**changes)


def test_observer_keeps_numeric_samples_and_labels_device_wide_peak():
    counter = []

    def probe():
        counter.append(True)
        return {"total_mib": 8192, "used_mib": 1700 + len(counter)}

    with GpuSampler(probe=probe, interval=0.05) as sampler:
        time.sleep(0.07)
    result = sampler.report()
    assert result["sampling_complete"] and len(result["samples"]) >= 2
    assert result["observed_device_peak_mib"] == 1700 + len(counter)
    assert result["model_vram_mib"] is None and result["physical_peak_guaranteed"] is False


def test_missing_or_bad_gpu_measurements_preserve_null_and_error_counts():
    with GpuSampler(probe=lambda: {"total_mib": 8192, "used_mib": True}) as sampler:
        pass
    result = sampler.report()
    assert result["observed_device_peak_mib"] is None and result["samples"] == []
    assert result["measurement_errors"] == 2 and not result["sampling_complete"]


def test_sampling_capacity_is_bounded_and_partial_coverage_not_complete():
    with GpuSampler(
        probe=lambda: {"total_mib": 8192, "used_mib": 2000}, interval=0.05, max_samples=2
    ) as sampler:
        time.sleep(0.12)
    assert not sampler.report()["sampling_complete"]
    assert len(sampler.report()["samples"]) <= 2


def test_recovery_denominator_counts_attempted_runs_and_preserves_failures():
    summary = aggregate(
        [
            record(recovery_attempts=1, recovered_success=True),
            record(
                run_id="f" * 64,
                status="interrupted",
                success=False,
                tests_passed=None,
                test_evidence="not_run",
                recovery_attempts=2,
            ),
        ]
    )
    assert summary["recovery_runs"] == 2 and summary["recovery_success_rate"] == 0.5


def test_measured_pipeline_receipt_and_second_attempt_preserve_previous_evidence(delegation):
    from test_task_delegation import execute

    b, _ = delegation
    report = execute(b)
    value = b.life.store.load(b.contract.user_id, b.contract.task_id)
    gpu = {
        "samples": [],
        "observed_device_peak_mib": None,
        "measurement_errors": 0,
        "sampling_complete": False,
    }
    result = measure_report(
        report,
        b.runtime.load(value),
        gpu,
        model_digest=None,
        conditions_digest="e" * 64,
        evidence="test_double",
    )
    assert result.model_tokens == 72 and result.codex_tokens is None
    assert aggregate([result])["runs"] == 0
    directory = b.life.store.directory(b.contract.user_id, b.contract.task_id)
    before = (directory / "delegation.json").read_bytes()
    with pytest.raises(ApprovalDenied, match="delegation_receipt_exists"):
        execute(b)
    assert (directory / "delegation.json").read_bytes() == before
