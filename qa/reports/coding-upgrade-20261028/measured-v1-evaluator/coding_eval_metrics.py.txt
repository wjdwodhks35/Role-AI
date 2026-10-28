"""Outcome-based summaries; unknown tokens are not treated as measured zero."""

import math
import statistics


def summarize(rows):
    rows = [r for r in rows if r.get("kind") == "attempt"]
    successful = [r for r in rows if r.get("success") is True]
    measured = [
        r for r in rows if r.get("unreturned_calls", 0) == 0 and r.get("unmeasured_calls", 0) == 0
    ]
    n, k = len(rows), len(successful)
    if n:
        z, p = 1.96, k / n
        center = (p + z * z / (2 * n)) / (1 + z * z / n)
        spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / (1 + z * z / n)
        interval = [round(max(0, center - spread), 3), round(min(1, center + spread), 3)]
    else:
        interval = None

    def mean(items, key):
        values = [r[key] for r in items if r.get(key) is not None]
        return round(statistics.mean(values), 3) if values else None

    return {
        "attempts": n,
        "successes": k,
        "success_fraction": f"{k}/{n}",
        "wilson_95": interval,
        "all_mean_seconds": mean(rows, "wall_sec"),
        "success_mean_seconds": mean(successful, "wall_sec"),
        "measured_token_attempts": len(measured),
        "unknown_token_attempts": n - len(measured),
        "tokens_per_successful_task": mean(
            [r for r in successful if r in measured], "actual_tokens"
        ),
        "all_measured_tokens_per_success": (
            round(sum(r["actual_tokens"] for r in measured) / k, 1) if k else None
        ),
        "load_mean_seconds": mean(measured, "load_seconds"),
        "prompt_eval_mean_seconds": mean(measured, "prompt_eval_seconds"),
        "generation_mean_seconds": mean(measured, "generation_seconds"),
        "thinking_characters": sum(r.get("thinking_chars", 0) for r in rows),
        "thinking_tokens": None,
        "json_valid": sum(r.get("json_valid", 0) for r in rows),
        "replies": sum(r.get("replies", 0) for r in rows),
        "patch_applied": sum(r.get("patch_applied", 0) for r in rows),
        "patch_attempts": sum(r.get("patch_attempts", 0) for r in rows),
        "recovered": sum(bool(r.get("recovered")) for r in rows),
        "had_failure": sum(bool(r.get("had_failure")) for r in rows),
        "repeats": sum(r.get("repeats", 0) for r in rows),
    }


def valid_outcome(final, grade, protected_unchanged):
    return (
        final.get("status") == "completed"
        and protected_unchanged
        and grade.get("status") == "passed"
        and grade.get("count", 0) > 0
        and grade.get("revision") == final.get("revision")
        and final.get("tested_revision") == final.get("revision")
        and final.get("revision") is not None
    )
