"""Real offline Docker pipeline probe with a scripted synthetic model, never Ollama."""

import json
import os
from pathlib import Path
import tempfile

from role_ai.models import ScopePolicy
from role_ai.coding_runner import RepositoryTestRunner
from role_ai.task_approvals import ApprovalService
from role_ai.task_contracts import TaskContract, ResourceBudget, ReviewPolicy
from role_ai.task_delegation import DelegationBoundary, run_delegated
from role_ai.task_delegation_lab import synthetic_environment
from role_ai.task_evaluation import IndependentGrader
from role_ai.task_evaluation_cases import reference_source
from role_ai.task_metrics import measure_report, aggregate, MetricRecord
from role_ai.task_comparison import export_public
from role_ai.task_baseline import grade_baseline


class ScriptedModel:
    def __init__(self):
        self.actions = iter(
            [
                {"action": "plan", "plan": ["implement", "public tests", "finish"]},
                {
                    "action": "patch",
                    "changes": [
                        {"path": "solution.py", "content": reference_source("budget-check")}
                    ],
                },
                {"action": "test"},
                {"action": "finish", "summary": "synthetic candidate"},
            ]
        )

    def complete(self, state, messages):
        return {"content": json.dumps(next(self.actions)), "prompt_tokens": 10, "output_tokens": 5}


with tempfile.TemporaryDirectory(prefix="role-ai-delegation-ci-") as temporary:
    engine, db, settings, session, project, ref, public = synthetic_environment(
        temporary,
        "synthetic-not-an-installed-model",
        os.environ["ROLE_AI_CODE_EXECUTION_IMAGE"],
        "budget-check",
    )
    try:
        contract = TaskContract(
            task_id="ci-delegate",
            user_id="lab",
            scope="project",
            project_id=project,
            agent_id="coding",
            goal=public["goal"],
            acceptance_criteria=("benchmark_sha256=" + public["task_digest"],),
            allowed_resources=(ref,),
            allowed_tools=("coding.read", "coding.propose", "coding.sandbox_run"),
            permissions=("read", "propose", "sandbox_run"),
            budget=ResourceBudget(max_files=2, max_steps=8, max_retries=0, timeout_seconds=300),
            risk_level="low",
            review_policy=ReviewPolicy(policy_id="synthetic-ci", version=1),
            policy_version=db.get(ScopePolicy, "lab").generation,
            approval_reference="ci-delegate",
        )
        ApprovalService(db, settings).issue(session, contract)
        db.commit()
        boundary = DelegationBoundary(db, settings, session, contract, ref)
        boundary.life.create(session, contract)
        boundary.life.queue(session, contract.task_id, 0)
        report = run_delegated(
            boundary,
            "budget-check",
            7,
            model=ScriptedModel(),
            runner=RepositoryTestRunner(settings),
            grader=IndependentGrader(db, settings),
            gpu_required=False,
        )
        ledger = boundary.runtime.load(boundary.life.store.load(contract.user_id, contract.task_id))
        metric = measure_report(
            report,
            ledger,
            {
                "samples": [],
                "observed_device_peak_mib": None,
                "measurement_errors": 0,
                "sampling_complete": False,
            },
            model_digest=None,
            conditions_digest=public["task_digest"],
            evidence="scripted_ci",
        )
        assert aggregate([metric])["runs"] == 0, (
            "Synthetic CI must not count as actual LLM performance"
        )
        output = Path("qa/results/delegation-ci.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "scope": "scripted_model_real_docker",
                    "report": report,
                    "metrics": metric.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        assert report["success"] and report["grader"]["evidence_scope"] == "offline_docker", report
        assert report["runtime_steps"] == 4 and report["grader"]["test_count"] == 6, report
    finally:
        db.close()
        engine.dispose()
# A real Docker grade does not establish that a candidate was produced by Codex.

with tempfile.TemporaryDirectory(prefix="role-ai-direct-ci-") as baseline_root:
    export_public(baseline_root, seed=7, image=os.environ["ROLE_AI_CODE_EXECUTION_IMAGE"])
    (Path(baseline_root) / "budget-check" / "solution.py").write_text(
        reference_source("budget-check")
    )
    baseline = grade_baseline(baseline_root, "budget-check")
    baseline_metric = MetricRecord.model_validate_json(json.dumps(baseline["metrics"]))
    assert baseline_metric.success and baseline_metric.test_evidence == "offline_docker"
    assert (
        baseline_metric.evidence == "unverified_submission"
        and aggregate([baseline_metric])["runs"] == 0
    )
    Path("qa/results/direct-baseline-ci.json").write_text(json.dumps(baseline, indent=2))
print(
    "PASS: direct baseline actual Docker grade; unknown producer/Codex usage excluded from actual A/B"
)
print(
    "PASS: common contract -> scripted Coding Agent -> public Docker -> independent Docker -> succeeded; no model calls"
)
