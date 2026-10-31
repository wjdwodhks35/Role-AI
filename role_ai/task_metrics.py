"""Evidence-qualified metrics. Unknown values are null, never inferred as zero.

VRAM sampling is device-wide observed usage, not per-model allocation/hard cap.
Neither character counts nor budget reservations measure Codex input tokens.
"""

import hashlib
from pathlib import Path
import threading
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from role_ai.gpu_resources import gpu_snapshot


class MetricRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    format: Literal["role-ai-metrics-v1"] = "role-ai-metrics-v1"
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1, max_length=100)
    task_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int
    approach: Literal["local_delegate", "codex_direct"]
    evidence: Literal["actual_run", "scripted_ci", "test_double", "unverified_submission"]
    status: Literal[
        "succeeded",
        "failed",
        "blocked",
        "interrupted",
        "waiting",
        "paused",
        "cancelled",
        "budget_exhausted",
    ]
    success: bool
    tests_passed: int | None = Field(default=None, ge=0)
    tests_total: int = Field(ge=1)
    test_evidence: Literal["offline_docker", "test_double", "not_run"]
    model_tokens: int | None = Field(default=None, ge=0)
    charged_tokens: int | None = Field(default=None, ge=0)
    codex_tokens: int | None = Field(default=None, ge=0)
    codex_usage_receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_calls: int | None = Field(default=None, ge=0)
    runtime_steps: int | None = Field(default=None, ge=0)
    recovery_attempts: int | None = Field(default=None, ge=0)
    recovered_success: bool | None = None
    wall_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    observed_device_peak_mib: int | None = Field(default=None, ge=0)
    gpu_samples: int = Field(ge=0)
    gpu_measurement_errors: int = Field(ge=0)
    gpu_sampling_complete: bool
    model_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    implementation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    conditions_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent(self):
        if self.success != (self.status == "succeeded"):
            raise ValueError("Success must agree with terminal state")
        if self.tests_passed is not None and self.tests_passed > self.tests_total:
            raise ValueError("Invalid test count")
        if self.success and self.tests_passed != self.tests_total:
            raise ValueError("Success needs all independent cases")
        if self.test_evidence == "not_run" and self.tests_passed is not None:
            raise ValueError("Unexecuted tests are unknown")
        if self.evidence == "actual_run" and self.test_evidence == "test_double":
            raise ValueError("A fake grader is not actual evaluation")
        if (
            self.success
            and self.evidence == "actual_run"
            and self.approach == "local_delegate"
            and not self.model_digest
        ):
            raise ValueError("Actual model identity required")
        if self.codex_tokens is not None and not self.codex_usage_receipt_sha256:
            raise ValueError("Codex counts require an actual usage receipt")
        if (self.observed_device_peak_mib is None) != (self.gpu_samples == 0):
            raise ValueError("VRAM needs actual samples")
        if self.gpu_sampling_complete and (self.gpu_measurement_errors or not self.gpu_samples):
            raise ValueError("Incomplete sampling is not complete")
        if self.recovered_success and (not self.success or not self.recovery_attempts):
            raise ValueError("Recovery success needs a recovered completed run")
        return self


class GpuSampler:
    """Read-only bounded observer. Failure/partial coverage remains visible."""

    def __init__(self, *, probe=None, interval=0.5, max_samples=1024):
        if not 0.05 <= interval <= 5 or not 2 <= max_samples <= 4096:
            raise ValueError("Invalid bounded sampling profile")
        self.probe = probe or gpu_snapshot
        self.interval, self.max_samples = interval, max_samples
        self.samples, self.errors = [], 0
        self.stop_event = threading.Event()
        self.thread = None
        self.started = None
        self.exhausted = False

    def sample(self):
        try:
            value = self.probe()
            if type(value) is not dict or any(
                type(value.get(k)) is not int for k in ("total_mib", "used_mib")
            ):
                raise ValueError()
            if not 0 <= value["used_mib"] <= value["total_mib"] or value["total_mib"] <= 0:
                raise ValueError()
            if self.samples and value["total_mib"] != self.samples[0]["total_mib"]:
                raise ValueError("GPU identity/total changed")
            self.samples.append(
                {
                    "seconds": round(time.monotonic() - self.started, 6),
                    "total_mib": value["total_mib"],
                    "used_mib": value["used_mib"],
                }
            )
        except Exception:
            self.errors += 1  # No exception text, process list or fabricated zero.

    def loop(self):
        attempts = 1
        while not self.stop_event.wait(self.interval):
            if attempts >= self.max_samples - 1:
                self.exhausted = True
                return
            self.sample()
            attempts += 1

    def __enter__(self):
        if self.started is not None:
            raise ValueError("Sampler cannot be reused")
        self.started = time.monotonic()
        self.sample()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=4)  # Trusted gpu_snapshot has a 3s subprocess bound.
        if self.thread.is_alive():
            self.errors += 1
            return
        if not self.exhausted:
            self.sample()

    def report(self):
        running = self.thread is not None and self.thread.is_alive()
        samples = list(self.samples)
        return {
            "scope": "device_wide_observed_samples",
            "samples": samples,
            "observed_device_peak_mib": max((x["used_mib"] for x in samples), default=None),
            "measurement_errors": self.errors,
            "sampling_complete": bool(samples) and not (self.errors or self.exhausted or running),
            "interval_seconds": self.interval,
            "model_vram_mib": None,
            "physical_peak_guaranteed": False,
        }


def implementation_digest():
    """Hash trusted runtime sources, not the fixture Git SHA or source contents in reports."""
    root = Path(__file__).parent
    names = sorted(
        {
            p.name
            for pattern in (
                "task_*.py",
                "coding_*.py",
                "contract_execution.py",
                "scope_*.py",
                "agent_registry.py",
                "code_execution.py",
                "gpu_resources.py",
                "providers.py",
            )
            for p in root.glob(pattern)
        }
    )
    return hashlib.sha256(
        b"".join(
            n.encode() + b"\0" + hashlib.sha256((root / n).read_bytes()).digest() for n in names
        )
    ).hexdigest()


def measure_report(
    report,
    ledger,
    gpu,
    *,
    model_digest,
    conditions_digest,
    evidence="actual_run",
    wall_seconds=None,
):
    grade = report.get("grader")
    passed = (
        None
        if not grade or grade.get("public_passed") is None or grade.get("hidden_passed") is None
        else grade["public_passed"] + grade["hidden_passed"]
    )
    recoveries = sum(e["kind"] == "interrupted_not_replayed" for e in ledger["events"])
    # No reservation-to-measurement conversion, even when unmeasured=0 after no calls.
    measured = (
        ledger["measured_calls"] > 0 and not ledger["unmeasured_calls"] and not ledger["pending"]
    )
    return MetricRecord(
        run_id=report["contract_digest"],
        task_id=report["task_id"],
        task_digest=report["task_digest"],
        seed=report["seed"],
        approach="local_delegate",
        evidence=evidence,
        status=report["status"],
        success=report["success"],
        tests_passed=passed,
        tests_total=6,
        test_evidence=grade["evidence_scope"] if grade else "not_run",
        model_tokens=ledger["measured_prompt_tokens"] + ledger["measured_output_tokens"]
        if measured
        else None,
        charged_tokens=ledger["charged_tokens"],
        codex_tokens=None,
        model_calls=ledger["measured_calls"] + ledger["unmeasured_calls"],
        runtime_steps=ledger["steps"],
        recovery_attempts=recoveries,
        recovered_success=bool(recoveries and report["success"]),
        wall_seconds=float(report["seconds"] if wall_seconds is None else wall_seconds),
        observed_device_peak_mib=gpu["observed_device_peak_mib"],
        gpu_samples=len(gpu["samples"]),
        gpu_measurement_errors=gpu["measurement_errors"],
        gpu_sampling_complete=gpu["sampling_complete"],
        model_digest=model_digest,
        implementation_digest=implementation_digest(),
        conditions_digest=conditions_digest,
    )


def aggregate(records, *, evidence="actual_run"):
    checked = [MetricRecord.model_validate_json(r.model_dump_json()) for r in records]
    if len({r.run_id for r in checked}) != len(checked):
        raise ValueError("Duplicate run would bias evaluation")
    chosen = [r for r in checked if r.evidence == evidence]
    scored = [r for r in chosen if r.tests_passed is not None]
    recovery = [r for r in chosen if r.recovery_attempts]
    scored_recovery = [r for r in recovery if r.recovered_success is not None]

    def total(field):
        values = [getattr(r, field) for r in chosen]
        return sum(values) if values and all(v is not None for v in values) else None

    return {
        "evidence": evidence,
        "runs": len(chosen),
        "excluded_synthetic_runs": sum(
            r.evidence in {"scripted_ci", "test_double"} for r in checked if r not in chosen
        ),
        "excluded_non_actual_runs": len(checked) - len(chosen),
        "status_counts": {
            s: sum(r.status == s for r in chosen) for s in sorted({r.status for r in chosen})
        },
        "success_rate_all_attempts": sum(r.success for r in chosen) / len(chosen)
        if chosen
        else None,
        "test_pass_rate_scored": sum(r.tests_passed for r in scored)
        / sum(r.tests_total for r in scored)
        if scored
        else None,
        "scored_runs": len(scored),
        "unscored_runs": len(chosen) - len(scored),
        "model_tokens": total("model_tokens"),
        "codex_tokens": total("codex_tokens"),
        "wall_seconds": total("wall_seconds"),
        "model_calls": total("model_calls"),
        "recovery_runs": len(recovery),
        "recovery_unknown_runs": sum(r.recovery_attempts is None for r in chosen),
        "recovery_unscored_runs": len(recovery) - len(scored_recovery),
        "recovery_success_rate": sum(r.recovered_success is True for r in scored_recovery)
        / len(scored_recovery)
        if scored_recovery
        else None,
        "codex_token_reduction": None,
        "reservations_are_measurement": False,
    }
