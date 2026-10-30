"""Grade a submitted public-only Codex baseline in the existing offline runner.

No Codex/model API, host source execution or operating DB access. The producer's
token count/time remain unknown unless a separate genuine usage receipt exists.
"""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from role_ai.models import ScopePolicy
from role_ai.coding_jobs import checksum
import re
from role_ai.task_approvals import ApprovalService
from role_ai.task_checkpoints import TaskLifecycle
from role_ai.task_contracts import TaskContract, ResourceBudget, ReviewPolicy, contract_digest
from role_ai.task_comparison import baseline_candidate
from role_ai.task_evaluation import IndependentGrader
from role_ai.task_metrics import MetricRecord, implementation_digest


def grade_baseline(root, task_id, *, executor=None, producer_receipt=None, socket=None):
    # Verify exact public fixture/code identity before reading candidate source.
    manifest, candidate = baseline_candidate(root, task_id)
    verified = bool(
        type(producer_receipt) is dict
        and set(producer_receipt)
        == {
            "format",
            "task_id",
            "candidate_sha256",
            "public_manifest_sha256",
            "context_review_sha256",
        }
        and producer_receipt["format"] == "role-ai-codex-baseline-context-v1"
        and producer_receipt["task_id"] == task_id
        and producer_receipt["candidate_sha256"] == hashlib.sha256(candidate.encode()).hexdigest()
        and producer_receipt["public_manifest_sha256"] == checksum(manifest)
        and re.fullmatch(r"[0-9a-f]{64}", str(producer_receipt["context_review_sha256"]))
    )
    if producer_receipt is not None and not verified:
        raise ValueError("Producer context receipt does not match submitted source")
    from role_ai.task_delegation_lab import synthetic_environment

    artifact = Path(tempfile.mkdtemp(prefix="role-ai-direct-baseline-grade-"))
    engine, db, settings, session, project, ref, public = synthetic_environment(
        artifact,
        "no-local-model-in-direct-baseline",
        manifest["image"],
        task_id,
        seed=manifest["seed"],
        socket=socket,
    )
    try:
        if socket is not None and executor is None:
            from role_ai.task_runtime_endpoint import verify_runtime

            verify_runtime(settings, artifact)
        contract = TaskContract(
            task_id="direct-" + task_id,
            user_id="lab",
            scope="project",
            project_id=project,
            agent_id="coding",
            goal=public["goal"],
            acceptance_criteria=("benchmark_sha256=" + public["task_digest"],),
            allowed_resources=(ref,),
            allowed_tools=("coding.propose", "coding.sandbox_run"),
            permissions=("propose", "sandbox_run"),
            budget=ResourceBudget(max_files=2, max_steps=8, max_retries=0, timeout_seconds=300),
            risk_level="low",
            review_policy=ReviewPolicy(policy_id="direct-synthetic-baseline", version=1),
            policy_version=db.get(ScopePolicy, "lab").generation,
            approval_reference="direct-" + task_id,
        )
        ApprovalService(db, settings).issue(session, contract)
        db.commit()
        life = TaskLifecycle(db, settings)
        life.create(session, contract)
        queued = life.queue(session, contract.task_id, 0)
        grade = None
        with life.claim(session, contract.task_id, queued["state"].revision):
            value = life.owned(session, contract.task_id)
            verifying = life.store.transition(
                "lab", contract.task_id, value["state"].revision, "verifying"
            )
            grade = IndependentGrader(db, settings, executor=executor).grade(
                session, contract, ref, task_id, manifest["seed"], candidate
            )
            if grade["success"]:
                life.complete(
                    session,
                    contract.task_id,
                    verifying["state"].revision,
                    {
                        "contract_digest": contract_digest(contract),
                        "candidate_digest": hashlib.sha256(candidate.encode()).hexdigest(),
                        "exit_code": 0,
                        "tests": grade["test_count"],
                        "runner": "independent",
                    },
                )
            else:
                life.store.transition(
                    "lab",
                    contract.task_id,
                    verifying["state"].revision,
                    "interrupted" if grade["status"] == "interrupted" else "failed",
                )
        status = life.owned(session, contract.task_id)["state"].status
        passed = (
            grade["public_passed"] + grade["hidden_passed"]
            if grade["public_passed"] is not None and grade["hidden_passed"] is not None
            else None
        )
        item = next(x for x in manifest["cases"] if x["id"] == task_id)
        metric = MetricRecord(
            run_id=contract_digest(contract),
            task_id=task_id,
            task_digest=public["task_digest"],
            seed=manifest["seed"],
            approach="codex_direct",
            evidence="test_double"
            if executor
            else "actual_run"
            if verified
            else "unverified_submission",
            status=status,
            success=status == "succeeded",
            tests_passed=passed,
            tests_total=6,
            test_evidence=grade["evidence_scope"],
            charged_tokens=None,
            model_calls=None,
            runtime_steps=None,
            recovery_attempts=None,
            recovered_success=None,
            wall_seconds=None,
            gpu_samples=0,
            gpu_measurement_errors=0,
            gpu_sampling_complete=False,
            implementation_digest=implementation_digest(),
            conditions_digest=item["conditions_digest"],
        )
        directory = life.store.directory("lab", contract.task_id)
        life.store.files.atomic(directory / "metrics.json", metric.model_dump(mode="json"))
        return {
            "artifact_root": str(artifact),
            "metrics": metric.model_dump(mode="json"),
            "producer_usage_measured": False,
            "producer_context_receipt_present": verified and executor is None,
        }
    finally:
        db.close()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--producer-receipt")
    parser.add_argument("--socket", help="Explicit private user-owned rootless Unix socket")
    args = parser.parse_args()
    producer_receipt = None
    if args.producer_receipt:
        from role_ai.coding_jobs import CodingJobStore

        path = Path(args.producer_receipt).absolute()
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("Bounded trusted receipt required")
        producer_receipt = CodingJobStore(path.parent).read(path)
    report = grade_baseline(
        args.public_root, args.task, producer_receipt=producer_receipt, socket=args.socket
    )
    print(json.dumps(report))
    return 0 if report["metrics"]["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
