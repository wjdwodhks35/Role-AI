"""Local checkout identity only. Never probe a model, DB, Docker or credentials."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from role_ai.config import Settings

SOURCE_FILES = (
    "role_ai/main.py",
    "role_ai/config.py",
    "role_ai/agent_registry.py",
    "role_ai/agent_prompts.py",
    "role_ai/baseline_manifest.py",
    "role_ai/providers.py",
    "role_ai/assistant.py",
    "role_ai/models.py",
    "role_ai/migrations.py",
    "role_ai/memory.py",
    "role_ai/documents.py",
)


def git_value(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode:
        raise ValueError("Cannot read checkout identity")
    return result.stdout.decode("utf-8", errors="replace").strip()


def collect_manifest(root, settings):
    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError("A regular checkout directory is required")
    files = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if path.resolve() != path or not path.is_file():
            raise ValueError("A required baseline source is missing or is a symlink")
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("Baseline source exceeds size limit")
        files[relative] = hashlib.sha256(data).hexdigest()
    identity = {
        "mode": settings.mode,
        "model": settings.ollama_model,
        "embedding_model": settings.embedding_model,
        "training_enabled": settings.training_enabled,
        "code_execution_enabled": settings.code_execution_enabled,
        "openclaw_enabled": settings.openclaw_enabled,
    }
    return {
        "format": "role-ai-baseline-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "local_checkout_not_verified_runtime",
        "commit": git_value(root, "rev-parse", "HEAD"),
        "branch": git_value(root, "branch", "--show-current"),
        "dirty": bool(git_value(root, "status", "--porcelain", "--untracked-files=normal")),
        "source_sha256": files,
        "nonsecret_settings_sha256": hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest(),
        "performance": {"state": "not_measured", "actual_model_calls": 0},
        "limitations": [
            "No runtime image, DB or Gateway was inspected.",
            "Settings hash covers only the documented non-secret identity fields.",
            "Source hashes and dirty flag do not preserve a complete dirty patch.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = collect_manifest(args.checkout, Settings(_env_file=None))
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            # Explicit, new local report only; never replace an existing artifact.
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
        else:
            print(payload, end="")
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(2, "기준선을 수집하지 못했습니다. 경로와 새 출력 파일을 확인하세요.\n")


if __name__ == "__main__":
    main()
