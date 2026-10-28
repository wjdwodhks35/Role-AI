"""CPU-only comparison: repeated-line mismatch lookup and adversarial context bounds."""

import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

from role_ai.coding_edits import EditMismatch, replace_once
from role_ai.coding_jobs import TaskCreate
from role_ai.coding_memory import context, new_memory


def measure(function, samples=50):
    for _ in range(3):
        function()
    values = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        function()
        values.append((time.perf_counter_ns() - started) / 1e6)
    return {
        "samples": samples,
        "median_ms": round(statistics.median(values), 4),
        "p95_ms": round(sorted(values)[int(0.95 * (samples - 1))], 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for size in [1000, 10000, 100000]:
        text = "value = 100\n" * (size // 12) + "\ndef target():\n    return 1\n"
        for name, old, new in [
            ("exact", "def target():\n    return 1", "def target():\n    return 2"),
            ("mismatch", "def unknown():\n    return 999", "def unknown():\n    return 1"),
        ]:

            def run():
                try:
                    _, label = replace_once(text, old, new)
                    assert name == "exact" and label == "exact"
                except EditMismatch as exc:
                    assert name == "mismatch" and exc.reason == "not_found"

            results.append({"operation": name, "bytes": len(text.encode()), **measure(run)})
    spec = TaskCreate(
        repository_id="bench",
        project_id="bench",
        request="r" * 4000,
        allowed_files=tuple("p" * 180 + str(i) + ".py" for i in range(16)),
    )
    state = {
        "spec": spec.model_dump(mode="json"),
        "plan": ["p" * 300] * 8,
        "memory": new_memory(),
        "files": {},
        "observations": [],
        "steps": 0,
        "revision": "a",
        "original_revision": "a",
    }
    state["memory"]["done"] = ["patched " + ", ".join(spec.allowed_files[:8])] * 12
    state["memory"]["last_patch"] = {"status": "rejected", "closest": {"text": "x" * 900}}
    state["memory"]["failed"] = ["f" * 300] * 6
    state["observations"] = [{"action": "read", "result": {"text": "t" * 5000}}]
    body = context(state, {})
    fingerprint = hashlib.sha256(
        Path(__import__("role_ai.coding_edits", fromlist=[""]).__file__).read_bytes()
    ).hexdigest()
    report = {
        "python": platform.python_version(),
        "edit_source_sha256": fingerprint,
        "results": results,
        "context_chars": len(json.dumps(body, ensure_ascii=False, separators=(",", ":"))),
        "context_limit": 16000,
        "request_preserved": body["request"] == spec.request,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
