"""Explicit synthetic-only delegation lab. Never loads the application database.

python -m role_ai.task_delegation_lab --model INSTALLED --image PINNED --task budget-check
Use tools/codex/run_logged.py around this command for bounded console context.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import httpx

from role_ai.coding_agent import LocalCodingModel, ModelBusy
from role_ai.coding_runner import RepositoryTestRunner, PINNED_IMAGE
from role_ai.coding_workspace import private_directory
from role_ai.config import Settings
from role_ai.database import build_database
from role_ai.gpu_resources import GpuPolicy, write_policy
from role_ai.migrations import initialize_database
from role_ai.models import User, AuthSession, ScopePolicy
from role_ai.project_scopes import ScopeRepository
from role_ai.task_approvals import ApprovalService
from role_ai.task_contracts import TaskContract, ResourceRef, ResourceBudget, ReviewPolicy
from role_ai.task_delegation import DelegationBoundary, run_delegated
from role_ai.task_evaluation import IndependentGrader
from role_ai.task_evaluation_cases import TASKS, public_task
from role_ai.task_windows_transport import WindowsLoopbackTransport
from role_ai.task_metrics import GpuSampler, measure_report, MetricRecord, implementation_digest


class InstalledModel(LocalCodingModel):
    """Bind installed bytes and deny busy server, without pulling or unloading."""

    def __init__(self, settings, digest, *, transport=None):
        super().__init__(settings, transport=transport, sync_transport=transport)
        self.digest = digest

    def inventory(self, *, empty=False):
        with httpx.Client(
            base_url=self.settings.ollama_url,
            transport=self.sync_transport,
            timeout=10,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            tags = client.get("/api/tags")
            tags.raise_for_status()
            matches = [
                m
                for m in tags.json().get("models", [])
                if m.get("name") == self.settings.ollama_model
            ]
            if len(matches) != 1 or matches[0].get("digest") != self.digest:
                raise ValueError("Installed model digest changed; no fallback")
            if empty:
                resident = client.get("/api/ps")
                resident.raise_for_status()
                if resident.json().get("models") != []:
                    raise ModelBusy("Existing resident model; no eviction")

    def complete(self, state, messages):
        self.inventory(empty=True)
        reply = super().complete(state, messages)
        self.inventory()
        return reply

    def release(self, state):
        return False


def verify_runtime(settings, root):
    """Read-only check before any inference; no pull, chmod, sudo or host fallback."""
    import shutil

    executable = shutil.which("docker")
    if not executable or not settings.code_execution_socket.startswith("/"):
        raise ValueError("Existing Docker runtime unavailable")
    result = subprocess.run(
        [
            executable,
            "--host",
            "unix://" + settings.code_execution_socket,
            "image",
            "inspect",
            settings.coding_image,
            "--format",
            "{{.Id}}",
        ],
        env={"PATH": os.environ["PATH"], "HOME": str(root), "DOCKER_CONFIG": str(root)},
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode or not result.stdout.startswith(b"sha256:"):
        raise ValueError("Existing installed Docker image/socket unavailable; no inference started")


def synthetic_environment(root, model, image, task_id, *, seed=7, timeout=300, socket=None):
    """Return a NEW private synthetic DB/fixture, never an existing path/data set."""
    root = Path(root).absolute()
    # A caller must supply a newly reserved empty directory.
    private_directory(root)
    if list(root.iterdir()):
        raise ValueError("Synthetic lab requires an empty private directory")
    if not PINNED_IMAGE.fullmatch(image) or not image.startswith("python:3.12-slim@sha256:"):
        raise ValueError("Installed digest-pinned official Python runtime required")
    public = public_task(task_id, seed)
    source = private_directory(root / "source")
    for name, content in public["files"].items():
        (source / name).write_text(content)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    for args in [
        ("init", "-q"),
        ("add", "."),
        (
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=lab@test.invalid",
            "commit",
            "-qm",
            "synthetic fixture",
        ),
    ]:
        subprocess.run(
            ["git", *args], cwd=source, env=env, capture_output=True, check=True, timeout=10
        )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, env=env, text=True, timeout=10
    ).strip()
    settings = Settings(
        _env_file=None,
        mode="local",
        ollama_model=model,
        ollama_url="http://127.0.0.1:11434",
        coding_enabled=True,
        database_url=f"sqlite:///{root / 'synthetic.db'}",
        coding_root=str(private_directory(root / "jobs")),
        storage_dir=str(private_directory(root / "artifacts")),
        coding_repositories={"fixture": str(source)},
        coding_image=image,
        code_execution_enabled=True,
        code_execution_image=image,
        code_execution_timeout_seconds=10,
        code_execution_socket=socket or "/var/run/docker.sock",
        provider_timeout_seconds=min(timeout, 120),
    )
    # New synthetic settings only, never an operating GPU policy.
    write_policy(
        settings, GpuPolicy(enabled=True, limit_mib=6144, reserve_mib=1024, inference_mib=4096)
    )
    engine, factory = build_database(settings.database_url)
    initialize_database(engine)
    db = factory()
    db.add(User(id="lab", email="lab@test.invalid", password_hash="synthetic", is_owner=True))
    db.flush()
    # Local auth evidence lives only inside the lab; never sent to the model.
    import secrets

    session_hash = secrets.token_hex(32)
    db.add(
        AuthSession(
            token_hash=session_hash,
            user_id="lab",
            csrf_token="synthetic",
            expires_at=time.time() + 3600,
        )
    )
    db.commit()
    project = ScopeRepository(db).create_project("lab", "Synthetic delegation")
    db.commit()
    reference = ResourceRef(
        kind="repository",
        resource_id="fixture",
        version=revision,
        paths=("solution.py", "test_public.py"),
    )
    return engine, db, settings, session_hash, project.id, reference, public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Already installed model, no download")
    parser.add_argument("--model-digest", required=True)
    parser.add_argument(
        "--image", required=True, help="Already installed official Python pinned image"
    )
    parser.add_argument("--task", choices=[t.id for t in TASKS], required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--windows-loopback",
        action="store_true",
        help="Explicit WSL to existing Windows localhost only, no service/config changes",
    )
    args = parser.parse_args()
    import re

    if not re.fullmatch("[0-9a-f]{64}", args.model_digest):
        parser.error("Exact installed model SHA256 required")
    root = Path(tempfile.mkdtemp(prefix="role-ai-delegation-lab-"))
    engine, db, settings, session_hash, project, ref, public = synthetic_environment(
        root, args.model, args.image, args.task, seed=args.seed
    )
    from role_ai.coding_jobs import CodingJobStore, checksum

    started = time.monotonic()
    from role_ai.task_comparison import conditions_digest

    conditions = conditions_digest(args.task, args.seed, args.image)
    try:
        try:
            verify_runtime(settings, root)
        except (OSError, subprocess.SubprocessError, ValueError):
            blocked = MetricRecord(
                run_id=checksum(root.name + public["task_digest"]),
                task_id=args.task,
                task_digest=public["task_digest"],
                seed=args.seed,
                approach="local_delegate",
                evidence="actual_run",
                status="blocked",
                success=False,
                tests_total=6,
                test_evidence="not_run",
                charged_tokens=0,
                model_calls=0,
                runtime_steps=0,
                recovery_attempts=0,
                recovered_success=False,
                wall_seconds=float(time.monotonic() - started),
                gpu_samples=0,
                gpu_measurement_errors=0,
                gpu_sampling_complete=False,
                model_digest=args.model_digest,
                implementation_digest=implementation_digest(),
                conditions_digest=conditions,
            )
            CodingJobStore(root).atomic(
                root / "blocked-metrics.json", blocked.model_dump(mode="json")
            )
            print(
                json.dumps(
                    {
                        "artifact_root": str(root),
                        "status": "blocked",
                        "reason": "existing_docker_runtime_unavailable",
                        "model_calls": 0,
                    }
                )
            )
            return 2
        transport = WindowsLoopbackTransport(args.model) if args.windows_loopback else None
        model = InstalledModel(settings, args.model_digest, transport=transport)
        model.inventory(empty=True)
        body = TaskContract(
            task_id="lab-" + args.task,
            user_id="lab",
            scope="project",
            project_id=project,
            agent_id="coding",
            goal=public["goal"],
            acceptance_criteria=(
                "benchmark_sha256=" + public["task_digest"],
                "model_sha256=" + args.model_digest,
                "transport=" + ("windows_loopback" if transport else "native_loopback"),
            ),
            allowed_resources=(ref,),
            allowed_tools=("coding.read", "coding.propose", "coding.sandbox_run"),
            permissions=("read", "propose", "sandbox_run"),
            budget=ResourceBudget(max_files=2, max_steps=8, max_retries=0, timeout_seconds=300),
            risk_level="low",
            review_policy=ReviewPolicy(policy_id="synthetic-lab", version=1),
            policy_version=db.get(ScopePolicy, "lab").generation,
            approval_reference="lab-" + args.task,
        )
        ApprovalService(db, settings).issue(session_hash, body)
        db.commit()
        boundary = DelegationBoundary(db, settings, session_hash, body, ref)
        boundary.life.create(session_hash, body)
        boundary.life.queue(session_hash, body.task_id, 0)
        with GpuSampler() as sampler:
            report = run_delegated(
                boundary,
                args.task,
                args.seed,
                model=model,
                runner=RepositoryTestRunner(settings),
                grader=IndependentGrader(db, settings),
            )
        gpu = sampler.report()

        metric = measure_report(
            report,
            boundary.runtime.load(boundary.life.store.load(body.user_id, body.task_id)),
            gpu,
            model_digest=args.model_digest,
            conditions_digest=conditions,
            wall_seconds=time.monotonic() - started,
        )
        directory = boundary.life.store.directory(body.user_id, body.task_id)
        boundary.life.store.files.atomic(directory / "gpu-samples.json", gpu)
        boundary.life.store.files.atomic(directory / "metrics.json", metric.model_dump(mode="json"))
        print(
            json.dumps(
                {
                    "artifact_root": str(root),
                    "report": report,
                    "metrics": metric.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        )
        return 0 if report["success"] else 1
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
