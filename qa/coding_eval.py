"""One auditable coding evaluation attempt. Run with PYTHONPATH selecting the runtime.

The task fixtures/graders come from this checkout, never from a reference patch.
Host execution requires --host-runner; Docker remains the production default.
"""

import argparse
import copy
import hashlib
import importlib.util
import importlib.metadata
import json
import platform
import subprocess
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = load("evaluation_harness", HERE / "bench_coding_models.py")
metrics = load("evaluation_metrics", HERE / "coding_eval_metrics.py")


def runtime_identity():
    directory = Path(bench.agent_module.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    revision = subprocess.check_output(
        ["git", "-C", str(directory.parent), "rev-parse", "HEAD"], text=True
    ).strip()
    return {
        "revision": revision,
        "runtime_sha256": digest.hexdigest(),
        "root": str(directory.parent),
    }


def instrumentation():
    # The same telemetry-only adapter is applied to baseline and candidate.
    original = bench.agent_module.model_reply

    def with_durations(data, content, think=None):
        result = original(data, content, think)
        for field, key in [
            ("prompt_eval_seconds", "prompt_eval_duration"),
            ("generation_seconds", "eval_duration"),
        ]:
            value = data.get(key)
            result[field] = value / 1e9 if type(value) is int and value >= 0 else None
        return result

    bench.agent_module.model_reply = with_durations


def legacy_overrides(path, context=False, recovery=False):
    if not (context or recovery):
        return
    base = Path(path) / "role_ai"
    legacy = load("legacy_coding_agent", base / "coding_agent.py")
    legacy_memory = load("legacy_coding_memory", base / "coding_memory.py")
    legacy_edits = load("legacy_coding_edits", base / "coding_edits.py")
    legacy.memory_context = legacy_memory.context
    legacy.Recoverable = bench.agent_module.Recoverable
    legacy.BudgetStop = bench.agent_module.BudgetStop
    legacy.apply_changes = legacy_edits.apply_changes
    legacy.EditMismatch = legacy_edits.EditMismatch
    if context:
        bench.agent_module.CodingAgent.messages = legacy.CodingAgent.messages
    if recovery:
        bench.agent_module.CodingAgent.action = legacy.CodingAgent.action
        bench.agent_module.CodingAgent.recoverable = legacy.CodingAgent.recoverable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--condition", required=True)
    parser.add_argument("--repeat-index", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--think", default="off", choices=["off", "on"])
    parser.add_argument("--keep", type=int, default=300)
    parser.add_argument("--output-tokens", type=int, default=1024)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--legacy-root")
    parser.add_argument("--legacy-context", action="store_true")
    parser.add_argument("--legacy-recovery", action="store_true")
    parser.add_argument("--suite", choices=["development", "holdout"], default="development")
    runner = parser.add_mutually_exclusive_group(required=True)
    runner.add_argument("--host-runner", action="store_true")
    runner.add_argument("--image")
    args = parser.parse_args()
    instrumentation()
    if args.legacy_context or args.legacy_recovery:
        if not args.legacy_root:
            parser.error("--legacy-root required")
        legacy_overrides(args.legacy_root, args.legacy_context, args.legacy_recovery)
    if args.no_normalize:
        import role_ai.coding_edits as edits

        original = edits._candidates
        edits._candidates = lambda text, old, new: (
            c for c in original(text, old, new) if c[0] == "exact"
        )
    hidden = {}
    if args.suite == "holdout":
        definitions = json.loads((HERE / "fixtures/coding_eval_holdout.json").read_text())
        bench.TASKS.update(
            {
                t: {k: v for k, v in definition.items() if k != "grader"}
                for t, definition in definitions.items()
            }
        )
        hidden = definitions[args.task]["grader"]
    task = bench.TASKS[args.task]
    task_hash = hashlib.sha256(
        json.dumps({**task, "grader": hidden}, sort_keys=True).encode()
    ).hexdigest()
    identity = runtime_identity()
    with httpx.Client(base_url="http://127.0.0.1:11434", timeout=10, trust_env=False) as client:
        installed = client.get("/api/tags").json()["models"]
        model = next(m for m in installed if m["name"] == args.model)
        server = client.get("/api/version").json()
        capability = client.post("/api/show", json={"model": args.model}).json()
    row = {
        "kind": "attempt",
        "run_id": args.run_id,
        "condition": args.condition,
        "repeat_index": args.repeat_index,
        "suite": args.suite,
        "task_digest": task_hash,
        "model_digest": model["digest"],
        "server": server,
        "runtime": identity,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ["pydantic", "httpx", "fastapi", "sqlalchemy"]
        },
        "python": platform.python_version(),
        "model": args.model,
        "task": args.task,
        "thinking_support": capability.get("thinking"),
        "settings": {
            "think": args.think,
            "keep": args.keep,
            "output_tokens": args.output_tokens,
            "num_ctx": args.num_ctx,
            "timeout": args.timeout,
            "temperature": 0,
            "seed": "backend_default",
        },
        "started_at_unix": time.time(),
    }
    try:
        row["gpu_before"] = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader",
            ],
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        row["gpu_before"] = None
    args.label = args.condition
    args.work.mkdir(parents=True, exist_ok=True)
    bench.unload_all()
    result = bench.run_one(args, args.model, args.task, args.work)
    final, calls = result.pop("final_state"), result.pop("model_calls")
    original_files = bench.decode_files(final["original"] or {})
    candidate_files = bench.decode_files(final["files"] or {})
    protected = all(
        candidate_files.get(p) == raw
        for p, raw in original_files.items()
        if p not in task["allowed"]
    )
    grade_state = copy.deepcopy(final)
    if hidden:
        from role_ai.coding_jobs import encode_files

        grade_state["files"] = encode_files(
            {**candidate_files, **{p: text.encode() for p, text in hidden.items()}}
        )
    grader = (
        bench.HostRunner()
        if args.host_runner
        else bench.RepositoryTestRunner(bench.Settings(_env_file=None, coding_image=args.image))
    )
    grading_started = time.monotonic()
    grade = grader.run(grade_state, "role-ai-eval-" + args.run_id, args.work)
    # Hidden tests only affect the external grade; candidate revision stays the acceptance reference.
    grade["revision"] = final.get("revision")
    row.update(result)
    row.update(
        success=metrics.valid_outcome(final, grade, protected),
        grade=grade,
        grade_seconds=round(time.monotonic() - grading_started, 3),
        protected_unchanged=protected,
        unreturned_calls=max(0, final["calls"] - len(calls)),
        actual_tokens=sum(
            (c.get("prompt_tokens") or 0) + (c.get("output_tokens") or 0) for c in calls
        ),
        prompt_eval_seconds=sum(c.get("prompt_eval_seconds") or 0 for c in calls),
        generation_seconds=sum(c.get("generation_seconds") or 0 for c in calls),
        thinking_chars=sum(c.get("thinking_chars") or 0 for c in calls),
        thinking_tokens=None,
        finished_at_unix=time.time(),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting a failed or interrupted attempt on restart.
    with args.out.open("x") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                k: row[k]
                for k in [
                    "condition",
                    "model",
                    "task",
                    "repeat_index",
                    "success",
                    "status",
                    "wall_sec",
                ]
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
