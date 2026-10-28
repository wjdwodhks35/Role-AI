"""Paired baseline/candidate evaluation, narrow ablations, then a frozen holdout.

No deployment/merging; sequential GPU work, resumable exclusive per-attempt files.
"""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request

from coding_eval_metrics import summarize

HERE = Path(__file__).resolve().parent
TASKS = ["T1_add_bug", "T2_slugify", "T3_stats_multi", "T4_top_words", "T5_account_crlf"]
MODELS = ["qwen3.5:4b", "qwen3:8b"]


def source_hash(root):
    h = hashlib.sha256()
    for p in sorted((root / "role_ai").glob("*.py")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def wait_for_gpu(min_free_mib, max_utilization):
    """Resource gate only; never stop another service or silently switch to CPU."""
    while True:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).splitlines()[0]
        free, utilization = (int(value.strip()) for value in output.split(","))
        if free >= min_free_mib and utilization <= max_utilization:
            return
        print(
            f"WAIT_GPU free_mib={free} utilization={utilization}; other GPU work is preserved",
            flush=True,
        )
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--panels", default="paired,ablations,holdout")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-free-vram-mib", type=int, default=6000)
    parser.add_argument("--max-gpu-utilization", type=int, default=15)
    runner = parser.add_mutually_exclusive_group(required=True)
    runner.add_argument("--host-runner", action="store_true")
    runner.add_argument("--image")
    args = parser.parse_args()
    roots = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    fingerprints = {key: source_hash(root) for key, root in roots.items()}

    def evaluation_hash():
        digest = hashlib.sha256()
        for path in [
            HERE / name
            for name in [
                "coding_eval.py",
                "coding_eval_metrics.py",
                "bench_coding_models.py",
                "run_coding_matrix.py",
                "fixtures/coding_eval_holdout.json",
            ]
        ]:
            digest.update(path.name.encode() + b"\0" + path.read_bytes())
        return digest.hexdigest()

    evaluator_fingerprint = evaluation_hash()
    jobs = []
    panels = args.panels.split(",")
    if "paired" in panels:
        for repeat in range(args.repeats):
            for model in MODELS:
                for task in TASKS:
                    order = (
                        ["baseline", "candidate"] if repeat % 2 == 0 else ["candidate", "baseline"]
                    )
                    jobs += [
                        (condition, model, task, repeat, "development", []) for condition in order
                    ]
    if "ablations" in panels:
        # One-variable comparisons to candidate's paired results for these SAME tasks.
        conditions = [
            ("think_on", ["--think", "on"]),
            ("keep0", ["--keep", "0"]),
            ("no_normalize", ["--no-normalize"]),
            ("legacy_context", ["--legacy-context"]),
            ("legacy_recovery", ["--legacy-recovery"]),
            ("output2048", ["--output-tokens", "2048"]),
        ]
        for condition, options in conditions:
            for repeat in range(args.repeats):
                jobs += [
                    (condition, model, task, repeat, "development", options)
                    for model in MODELS
                    for task in ["T3_stats_multi", "T5_account_crlf"]
                ]
    if "holdout" in panels:
        for repeat in range(args.repeats):
            for model in MODELS:
                for task in ["H1_pairs", "H2_intervals"]:
                    order = (
                        ["baseline", "candidate"] if repeat % 2 == 0 else ["candidate", "baseline"]
                    )
                    jobs += [(condition, model, task, repeat, "holdout", []) for condition in order]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "attempts": len(jobs),
                    "panels": panels,
                    "repeats": args.repeats,
                    "runtime_sha256": fingerprints,
                },
                indent=2,
            )
        )
        return
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:11434/api/tags", timeout=10) as response:
        installed = {m["name"]: m["digest"] for m in json.load(response)["models"]}
    models = {name: installed[name] for name in MODELS}
    with opener.open("http://127.0.0.1:11434/api/version", timeout=10) as response:
        server_version = json.load(response)
    args.out.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema": 1,
        "planned_attempts": len(jobs),
        "repeats": args.repeats,
        "panels": panels,
        "resource_gate": {
            "min_free_vram_mib": args.min_free_vram_mib,
            "max_gpu_utilization": args.max_gpu_utilization,
        },
        "runtime_sha256": fingerprints,
        "evaluation_sha256": evaluator_fingerprint,
        "model_digests": models,
        "server_version": server_version,
        "python": platform.python_version(),
        "design": "paired condition order reversed on second repetition; fixed ablation order; descriptive small samples",
        "holdout_policy": "No runtime changes after matrix start; holdout run last, no reference patches in model input.",
    }
    plan_path = args.out / "plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise SystemExit(
            "Plan/source changed; use a new result directory. Existing evidence is preserved."
        )
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    last_panel = None
    for index, (condition, model, task, repeat, suite, options) in enumerate(jobs, 1):
        runtime = "baseline" if condition == "baseline" else "candidate"
        if evaluation_hash() != evaluator_fingerprint or any(
            source_hash(root) != fingerprints[key] for key, root in roots.items()
        ):
            raise SystemExit("Runtime changed during evaluation; stop instead of mixing revisions.")
        run_id = hashlib.sha256(
            json.dumps(
                [fingerprints, condition, model, task, repeat, suite, options], sort_keys=True
            ).encode()
        ).hexdigest()[:20]
        path = args.out / f"{index:03d}_{condition}_{run_id}.jsonl"
        panel = (
            "holdout"
            if suite == "holdout"
            else "paired"
            if condition in {"baseline", "candidate"}
            else condition
        )
        if last_panel and last_panel != panel:
            print(f"PANEL_COMPLETE {last_panel}", flush=True)
        last_panel = panel
        if path.exists():
            previous = json.loads(path.read_text())
            if previous["run_id"] != run_id:
                raise SystemExit("Attempt identity mismatch")
            print(f"{index}/{len(jobs)} preserved {run_id}", flush=True)
            continue
        wait_for_gpu(args.min_free_vram_mib, args.max_gpu_utilization)
        command = [
            sys.executable,
            str(HERE / "coding_eval.py"),
            "--model",
            model,
            "--task",
            task,
            "--condition",
            condition,
            "--repeat-index",
            str(repeat),
            "--run-id",
            run_id,
            "--work",
            str((args.out / "work" / run_id).resolve()),
            "--out",
            str(path.resolve()),
            "--suite",
            suite,
            "--legacy-root",
            str(roots["baseline"]),
            *options,
        ]
        command += ["--host-runner"] if args.host_runner else ["--image", args.image]
        print(f"{index}/{len(jobs)} {panel} {condition} {model} {task} repeat={repeat}", flush=True)
        subprocess.run(
            command,
            cwd=roots[runtime],
            env={**os.environ, "PYTHONPATH": str(roots[runtime])},
            check=True,
        )
        just_finished = json.loads(path.read_text())
        if (
            just_finished["model_digest"] != models[model]
            or just_finished["server"] != server_version
        ):
            raise SystemExit("Model/server changed during evaluation; preserve attempt and stop.")
        records = [
            json.loads(p.read_text()) for p in sorted(args.out.glob("[0-9][0-9][0-9]_*.jsonl"))
        ]
        groups = defaultdict(list)
        for row in records:
            groups[(row["suite"], row["condition"], row["model"])].append(row)
        summary = [
            {"suite": k[0], "condition": k[1], "model": k[2], **summarize(v)}
            for k, v in groups.items()
        ]
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PANEL_COMPLETE {last_panel}", flush=True)
    print(f"MATRIX_COMPLETE {len(jobs)}", flush=True)


if __name__ == "__main__":
    main()
