"""Explicit CI-only real Docker oracle probe, synthetic Git/DB, no model calls."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from role_ai.config import Settings
from role_ai.database import build_database
from role_ai.migrations import initialize_database
from role_ai.models import User, AuthSession, ScopePolicy
from role_ai.project_scopes import ScopeRepository
from role_ai.task_contracts import TaskContract, ResourceRef, ReviewPolicy
from role_ai.task_approvals import ApprovalService
from role_ai.task_evaluation import IndependentGrader
from role_ai.task_evaluation_cases import TASKS, public_task, task_digest, reference_source

receipts = []
output = Path("qa/results/independent-grader-ci.json")
output.parent.mkdir(parents=True, exist_ok=True)
try:
    with tempfile.TemporaryDirectory(prefix="role-ai-independent-ci-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "solution.py").write_text("# synthetic baseline\n")
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        for args in [
            ("init", "-q"),
            ("add", "solution.py"),
            (
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=ci@test.invalid",
                "commit",
                "-qm",
                "synthetic base",
            ),
        ]:
            subprocess.run(["git", *args], cwd=source, env=env, capture_output=True, check=True)
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, env=env, text=True
        ).strip()
        image = os.environ["ROLE_AI_CODE_EXECUTION_IMAGE"]
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{root / 'synthetic.db'}",
            storage_dir=str(root / "artifacts"),
            coding_root=str(root / "jobs"),
            coding_enabled=True,
            coding_repositories={"fixture": str(source)},
            coding_image=image,
            code_execution_enabled=True,
            code_execution_image=image,
            code_execution_timeout_seconds=3,
        )
        engine, factory = build_database(settings.database_url)
        initialize_database(engine)
        with factory() as db:
            db.add(User(id="ci", email="ci@test.invalid", password_hash="synthetic", is_owner=True))
            db.flush()
            db.add(
                AuthSession(
                    token_hash="a" * 64,
                    user_id="ci",
                    csrf_token="synthetic",
                    expires_at=time.time() + 3600,
                )
            )
            db.commit()
            project = ScopeRepository(db).create_project("ci", "Synthetic evaluation")
            db.commit()
            resource = ResourceRef(
                kind="repository", resource_id="fixture", version=revision, paths=("solution.py",)
            )
            grader = IndependentGrader(db, settings)
            contracts = {}
            for definition in TASKS:
                public = public_task(definition.id, 7)
                body = TaskContract(
                    task_id="ci-" + definition.id,
                    user_id="ci",
                    scope="project",
                    project_id=project.id,
                    agent_id="coding",
                    goal=public["goal"],
                    acceptance_criteria=("benchmark_sha256=" + task_digest(definition.id, 7),),
                    allowed_resources=(resource,),
                    allowed_tools=("coding.propose", "coding.sandbox_run"),
                    permissions=("propose", "sandbox_run"),
                    risk_level="low",
                    review_policy=ReviewPolicy(policy_id="ci-synthetic", version=1),
                    policy_version=db.get(ScopePolicy, "ci").generation,
                    approval_reference="ci-" + definition.id,
                )
                ApprovalService(db, settings).issue("a" * 64, body)
                db.commit()
                contracts[definition.id] = body
                report = grader.grade(
                    "a" * 64, body, resource, definition.id, 7, reference_source(definition.id)
                )
                receipts.append({k: v for k, v in report.items() if k != "artifact_directory"})
                assert (
                    report["success"]
                    and report["test_count"] == 6
                    and report["evidence_scope"] == "offline_docker"
                ), report
            for label, code in [
                ("self-score", 'print("all tests passed",flush=True)\nimport os;os._exit(0)\n'),
                (
                    "duplicate-marker",
                    reference_source("budget-check") + '\nprint("__ROLEAI_OBSERVATIONS__=[]")\n',
                ),
                ("timeout", "import time; time.sleep(60)\n"),
            ]:
                report = grader.grade(
                    "a" * 64, contracts["budget-check"], resource, "budget-check", 7, code
                )
                receipts.append(
                    {
                        **{k: v for k, v in report.items() if k != "artifact_directory"},
                        "probe": label,
                    }
                )
                assert not report["success"] and report["status"] in {
                    "invalid_evidence",
                    "interrupted",
                }, report
        engine.dispose()
finally:
    output.write_text(
        json.dumps(
            {"format": "role-ai-grader-ci-v1", "model_calls": 0, "receipts": receipts},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
print(
    "PASS: 10 reference tasks/60 external-oracle cases; self-score/duplicate marker/timeout rejected; no host execution or model calls"
)
