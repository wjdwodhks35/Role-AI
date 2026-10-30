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
        output = Path("qa/results/delegation-ci.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"scope": "scripted_model_real_docker", "report": report}, indent=2)
        )
        assert report["success"] and report["grader"]["evidence_scope"] == "offline_docker", report
        assert report["runtime_steps"] == 4 and report["grader"]["test_count"] == 6, report
    finally:
        db.close()
        engine.dispose()
print(
    "PASS: common contract -> scripted Coding Agent -> public Docker -> independent Docker -> succeeded; no model calls"
)
