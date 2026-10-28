"""Coding task contracts only; no model dispatch, execution or permission grants."""

import hashlib
import json
import re
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator

SHA256 = r"^[0-9a-f]{64}$"
IDENTIFIER = r"^[a-zA-Z0-9_-]{1,64}$"


class CodingBoundaryError(ValueError):
    """Public diagnostic without repository contents, paths or Git stderr."""


def safe_path(value: str) -> str:
    """Conservative MVP path policy; configuration/deployment files are read-only."""
    parts = PurePosixPath(value).parts
    if (
        not value or len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
        or value.startswith("/") or "/".join(parts) != value
        or any(p in {".", ".."} or p.lower() in {
            ".git", ".ssh", ".aws", ".codex", ".agents", "data", "uploads",
            "backups", "models", "datasets", "node_modules", ".venv",
        } for p in parts)
        or any(p.lower().startswith(".env") and p != ".env.example" for p in parts)
        or parts[-1].lower().endswith((".pem", ".key", ".db", ".sqlite", ".sqlite3"))
        or parts[-1].lower() in {"credentials", "id_rsa", "id_ed25519"}
    ):
        raise CodingBoundaryError("허용되지 않은 파일 경로입니다.")
    return value


def editable_path(value: str) -> str:
    safe_path(value)
    parts = PurePosixPath(value).parts
    if (
        any(p.startswith(".") for p in parts)
        or parts[0].lower() in {"config", "prompts"}
        or parts[-1].lower().startswith(("dockerfile", "compose", "render."))
        or parts[-1].lower() in {"agents.md", "pyproject.toml", "package.json"}
        or value in {
            "role_ai/security.py", "role_ai/permissions.py", "role_ai/config.py",
            "role_ai/assistant.py", "role_ai/main.py", "role_ai/database.py",
            "role_ai/migrations.py", "role_ai/auto_approval.py",
        }
    ):
        raise CodingBoundaryError("보안·설정·배포 파일은 코딩 MVP 수정 범위에서 제외됩니다.")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CodingBudget(Contract):
    max_files: int = Field(default=8, ge=1, le=16)
    max_patch_bytes: int = Field(default=65536, ge=1, le=262144)
    max_steps: int = Field(default=4, ge=1, le=8)
    max_retries: int = Field(default=2, ge=0, le=3)
    timeout_seconds: int = Field(default=120, ge=1, le=300)


class CodingTaskSpec(Contract):
    task_id: str = Field(pattern=IDENTIFIER)
    user_id: str = Field(pattern=IDENTIFIER)
    project_id: str = Field(pattern=IDENTIFIER)
    repository_id: str = Field(pattern=IDENTIFIER)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    allowed_files: tuple[str, ...] = Field(min_length=1, max_length=16)
    budget: CodingBudget = Field(default_factory=CodingBudget)

    @field_validator("allowed_files")
    @classmethod
    def paths(cls, values):
        if len(set(values)) != len(values):
            raise CodingBoundaryError("파일 범위가 중복되었습니다.")
        for value in values:
            editable_path(value)
        return values


class FileChange(Contract):
    path: str
    # None means file creation; existing files require their exact previous bytes' hash.
    expected_sha256: str | None = Field(default=None, pattern=SHA256)
    # None means deletion. Empty string is a real, empty file.
    content: str | None = Field(default=None, max_length=262144)

    @field_validator("path")
    @classmethod
    def path_allowed(cls, value):
        return editable_path(value)

    @field_validator("content")
    @classmethod
    def utf8_content(cls, value):
        if value is not None:
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise CodingBoundaryError("UTF-8 텍스트만 변경할 수 있습니다.") from None
        return value


def digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def task_digest(spec: CodingTaskSpec) -> str:
    return digest(spec.model_dump(mode="json"))


def change_digest(spec: CodingTaskSpec, changes: tuple[FileChange, ...]) -> str:
    """Binding for a trusted permission gate, NOT an authentication credential."""
    return digest({"task": task_digest(spec), "changes": [
        change.model_dump(mode="json") for change in changes
    ]})
