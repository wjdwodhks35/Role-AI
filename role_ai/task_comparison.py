"""Frozen public benchmark export and conservative A/B comparison.

No model invocation, hidden oracle export, Git publication or permission grants.
Imported metric receipts are evidence from a trusted runner, not credentials.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

from role_ai.coding_jobs import CodingJobStore, checksum
from role_ai.coding_workspace import private_directory, read_text
from role_ai.task_contracts import ResourceBudget
from role_ai.task_evaluation_cases import TASKS, public_task, task_digest
from role_ai.task_metrics import MetricRecord, aggregate, implementation_digest

JOBS = {
    "tests",
    "container",
    "code-execution",
    "rlaif-contract",
    "command-logs (windows-latest)",
    "command-logs (ubuntu-latest)",
}


def conditions_digest(task_id, seed, image):
    if not re.fullmatch(r"python:3\.12-slim@sha256:[0-9a-f]{64}", image):
        raise ValueError("Exact official Python runtime digest required")
    return checksum(
        {
            "task_digest": task_digest(task_id, seed),
            "image": image,
            "seed": seed,
            "budget": ResourceBudget(
                max_files=2, max_steps=8, max_retries=0, timeout_seconds=300
            ).model_dump(mode="json"),
            "runner_memory_mib": 128,
            "runner_cpu_millicores": 500,
        }
    )


def export_public(root, *, seed, image):
    root = private_directory(root)
    if list(root.iterdir()):
        raise ValueError("Export target must be new/empty; never overwrite a baseline")
    cases = []
    for item in TASKS:
        public = public_task(item.id, seed)
        directory = private_directory(root / item.id)
        for name, text in {**public["files"], "GOAL.md": public["goal"] + "\n"}.items():
            path = directory / name
            path.write_text(text, encoding="utf-8")
            path.chmod(0o600)
        cases.append(
            {
                "id": item.id,
                "task_digest": public["task_digest"],
                "conditions_digest": conditions_digest(item.id, seed, image),
                "public_tests_sha256": hashlib.sha256(
                    public["files"]["test_public.py"].encode()
                ).hexdigest(),
                "goal_sha256": hashlib.sha256((public["goal"] + "\n").encode()).hexdigest(),
            }
        )
    manifest = {
        "format": "role-ai-public-baseline-v1",
        "seed": seed,
        "image": image,
        "implementation_digest": implementation_digest(),
        "cases": cases,
    }
    CodingJobStore(root).atomic(root / "manifest.json", manifest)
    instructions = root / "README.md"
    instructions.write_text(
        "# Codex 직접 기준선 — 공개 자료만\n\n"
        "새 독립 세션에서 이 폴더의 공개 목표와 공개 테스트만 제공한다. "
        "Role-AI 원본 저장소/정답/oracle/이전 세션 답안/숨김 테스트를 열람하지 않는다. "
        "각 과제의 solution.py만 구현하고 GOAL.md/test_public.py/manifest는 변경하지 않는다. "
        "외부 패키지·네트워크·운영 자료·모델 설치/교체를 사용하지 않는다. "
        "후보 코드는 호스트에서 실행하지 않는다. 제출 후 신뢰된 별도 runner가 채점한다.\n\n"
        "Codex 사용량·시간은 실제 작업 범위가 식별되는 클라이언트/API 사용량 원문이 "
        "없으면 미측정으로 남긴다. 문자열 길이/예산을 토큰으로 대신하지 않는다. "
        "이미 oracle을 본 세션의 답안은 공정한 기준선으로 사용하지 않는다. "
        "공개 폴더만 전달했다는 사실은 OS 수준 읽기 격리를 보장하지 않는다. "
        "동일 기기·작업/자원 조건 및 실제 접근 범위는 평가 운영자가 확인해야 한다.\n",
        encoding="utf-8",
    )
    instructions.chmod(0o600)
    return manifest


def baseline_candidate(root, task_id):
    root = Path(root).absolute()
    private_directory(root)
    manifest = CodingJobStore(root).read(root / "manifest.json")
    if (
        set(manifest) != {"format", "seed", "image", "implementation_digest", "cases"}
        or manifest["format"] != "role-ai-public-baseline-v1"
        or type(manifest["seed"]) is not int
        or manifest["implementation_digest"] != implementation_digest()
    ):
        raise ValueError("Baseline source/manifest changed; export a new package")
    if {x["id"] for x in manifest["cases"]} != {t.id for t in TASKS} or len(
        manifest["cases"]
    ) != 10:
        raise ValueError("Baseline task set changed")
    item = next((x for x in manifest["cases"] if x["id"] == task_id), None)
    if item is None:
        raise ValueError("Unknown public task")
    public = public_task(task_id, manifest["seed"])
    if item["task_digest"] != public["task_digest"] or item[
        "conditions_digest"
    ] != conditions_digest(task_id, manifest["seed"], manifest["image"]):
        raise ValueError("Benchmark identity changed")
    directory = private_directory(root / task_id)
    if {p.name for p in directory.iterdir()} != {"solution.py", "test_public.py", "GOAL.md"}:
        raise ValueError("Only approved public files are accepted")
    for name, expected in {
        "test_public.py": public["files"]["test_public.py"],
        "GOAL.md": public["goal"] + "\n",
    }.items():
        if read_text(directory / name) != expected.encode():
            raise ValueError("Read-only public fixture changed")
    candidate = read_text(directory / "solution.py")
    if candidate is None or len(candidate) > 16000:
        raise ValueError("Bounded candidate source required")
    return manifest, candidate.decode("utf-8")


def regression_verified(receipt, implementation):
    if not receipt or set(receipt) != {"implementation_digest", "head_sha", "jobs"}:
        return False
    if (
        receipt["implementation_digest"] != implementation
        or not re.fullmatch(r"[0-9a-f]{40}", str(receipt["head_sha"]))
        or type(receipt["jobs"]) is not list
        or len(receipt["jobs"]) != 6
    ):
        return False
    return {j.get("name") for j in receipt["jobs"] if type(j) is dict} == JOBS and all(
        j.get("conclusion") == "success"
        and j.get("head_sha") == receipt["head_sha"]
        and type(j.get("run_id")) is int
        and j["run_id"] > 0
        for j in receipt["jobs"]
    )


def compare(records, *, seed, image, regression_receipt=None, protocol_receipt=None):
    conditions_digest(TASKS[0].id, seed, image)
    checked = [MetricRecord.model_validate_json(r.model_dump_json()) for r in records]
    if len({r.run_id for r in checked}) != len(checked):
        raise ValueError("Duplicate evaluation run")
    actual = [r for r in checked if r.evidence == "actual_run"]
    a = [r for r in actual if r.approach == "codex_direct"]
    b = [r for r in actual if r.approach == "local_delegate"]
    current = implementation_digest()
    errors = set()
    expected_ids = {t.id for t in TASKS}
    # Fail closed on changed seeds/task/oracle/runtime/code, even if scores look better.
    for r in actual:
        if r.task_id not in expected_ids or r.seed != seed:
            errors.add("task_or_seed_mismatch")
            continue
        if r.task_digest != task_digest(r.task_id, seed):
            errors.add("benchmark_digest_mismatch")
        if r.conditions_digest != conditions_digest(r.task_id, seed, image):
            errors.add("runtime_conditions_mismatch")
        if r.implementation_digest != current:
            errors.add("implementation_mismatch")
        if r.tests_total != 6 or r.test_evidence not in {"offline_docker", "not_run"}:
            errors.add("independent_evidence_mismatch")
    counts = [{k: sum(r.task_id == k for r in rows) for k in expected_ids} for rows in (a, b)]
    coverage = all(counts[0][k] > 0 and counts[0][k] == counts[1][k] for k in expected_ids)
    if not coverage:
        errors.add("ten_task_paired_coverage_missing")
    summary_a, summary_b = aggregate(a), aggregate(b)
    result = {
        "format": "role-ai-comparison-v1",
        "seed": seed,
        "implementation_digest": current,
        "a": summary_a,
        "b": summary_b,
        "synthetic_runs_excluded": sum(
            r.evidence in {"scripted_ci", "test_double"} for r in checked
        ),
        "unverified_submissions_excluded": sum(
            r.evidence == "unverified_submission" for r in checked
        ),
        "paired_coverage_complete": coverage,
        "blocking_reasons": sorted(errors),
        "conclusion": "incomplete" if errors else "inconclusive",
        "improvement": False,
        "codex_token_reduction": None,
        "quality_regression": None,
        "regression_ci_verified": regression_verified(regression_receipt, current),
        "protocol_receipt_present": bool(
            type(protocol_receipt) is dict
            and set(protocol_receipt)
            == {"public_manifest_sha256", "a_context_review_sha256", "hardware_conditions_sha256"}
            and all(re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in protocol_receipt.values())
        ),
        "limitations": [
            "Single-module synthetic tasks do not prove whole-app autonomy",
            "Receipt checksums identify content; they do not authenticate a provider",
            "Matched metadata alone does not prove blindness or hardware equality",
            "Device samples are not per-model VRAM or guaranteed physical peaks",
        ],
    }
    if errors:
        return result
    rates_a = (summary_a["success_rate_all_attempts"], summary_a["test_pass_rate_scored"])
    rates_b = (summary_b["success_rate_all_attempts"], summary_b["test_pass_rate_scored"])
    quality_down = rates_b[0] < rates_a[0] or (
        rates_a[1] is not None and rates_b[1] is not None and rates_b[1] < rates_a[1]
    )
    result["quality_regression"] = quality_down
    if quality_down:
        result["conclusion"] = "regression"
        return result
    if summary_a["unscored_runs"] or summary_b["unscored_runs"]:
        result["blocking_reasons"].append("unscored_evaluation_runs")
        return result
    if not any(r.success for r in b):
        result["blocking_reasons"].append("no_successful_delegate")
        return result
    tokens_a, tokens_b = summary_a["codex_tokens"], summary_b["codex_tokens"]
    if tokens_a is None or tokens_b is None or tokens_a <= 0:
        result["blocking_reasons"].append("codex_usage_unmeasured")
        return result
    result["codex_token_reduction"] = 1 - tokens_b / tokens_a
    if not result["protocol_receipt_present"]:
        result["blocking_reasons"].append("blindness_or_hardware_protocol_unreviewed")
    elif not result["regression_ci_verified"]:
        result["blocking_reasons"].append("regression_ci_unverified")
    elif tokens_b < tokens_a:
        result["conclusion"] = "improved_under_recorded_conditions"
        result["improvement"] = True
    else:
        result["conclusion"] = "no_token_improvement"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export-public")
    export.add_argument("--image", required=True)
    export.add_argument("--seed", type=int, default=7)
    summary = sub.add_parser("compare")
    summary.add_argument("--image", required=True)
    summary.add_argument("--seed", type=int, default=7)
    summary.add_argument("--metrics", nargs="*", default=[])
    summary.add_argument("--regression-receipt")
    summary.add_argument("--protocol-receipt")
    args = parser.parse_args()
    if args.command == "export-public":
        root = Path(tempfile.mkdtemp(prefix="role-ai-public-baseline-"))
        export_public(root, seed=args.seed, image=args.image)
        print(json.dumps({"directory": str(root), "tasks": 10, "hidden_exported": False}))
        return 0
    rows = []
    for filename in args.metrics:
        path = Path(filename).absolute()
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("Bounded trusted runner metric receipt required")
        payload = CodingJobStore(path.parent).read(path)
        rows.append(MetricRecord.model_validate_json(json.dumps(payload)))

    def receipt(filename):
        if filename is None:
            return None
        path = Path(filename).absolute()
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("Bounded trusted receipt required")
        return CodingJobStore(path.parent).read(path)

    report = compare(
        rows,
        seed=args.seed,
        image=args.image,
        regression_receipt=receipt(args.regression_receipt),
        protocol_receipt=receipt(args.protocol_receipt),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["improvement"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
