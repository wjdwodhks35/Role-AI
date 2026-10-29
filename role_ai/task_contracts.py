"""Versioned task descriptions only: no authentication, approval or execution authority.

Identity, resource references and permissions are declarations. A trusted policy
adapter must authenticate their owner and verify a current approval before use.
Execution observations are separate and never change the immutable spec digest.
"""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
ToolId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RiskLevel = Literal["low", "medium", "high", "critical"]
Permission = Literal["read", "propose", "sandbox_run"]
StopCondition = Literal[
    "budget_exhausted", "cancel_requested", "scope_changed", "policy_changed",
    "approval_revoked", "no_progress",
]
REQUIRED_STOPS = ("budget_exhausted", "cancel_requested", "scope_changed", "policy_changed")


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, validate_default=True,
        revalidate_instances="always", hide_input_in_errors=True,
    )


class ResourceBudget(ContractModel):
    """Initial limits match CodingBudget; this does not reserve any resources."""

    max_files: int = Field(default=8, ge=1, le=16)
    max_patch_bytes: int = Field(default=65536, ge=1, le=262144)
    max_steps: int = Field(default=4, ge=1, le=8)
    max_retries: int = Field(default=2, ge=0, le=3)
    timeout_seconds: int = Field(default=120, ge=1, le=300)


class ResourceRef(ContractModel):
    kind: Literal["repository", "document", "memory"]
    resource_id: Identifier
    # Exact source version, never a moving branch or an implicit latest version.
    version: str = Field(min_length=1, max_length=64)
    paths: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def version_and_paths(self):
        import re

        pattern = {"repository": r"[0-9a-f]{40}", "document": r"[0-9a-f]{64}",
                   "memory": r"[1-9][0-9]{0,18}"}[self.kind]
        if not re.fullmatch(pattern, self.version):
            raise ValueError("An exact resource version is required")
        if self.kind == "repository":
            if not self.paths or len(set(self.paths)) != len(self.paths):
                raise ValueError("Repository paths must be nonempty and unique")
            for path in self.paths:
                parts = PurePosixPath(path).parts
                if (len(path) > 200 or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
                        or path.startswith("/") or "/".join(parts) != path
                        or any(part in {".", ".."} for part in parts)):
                    raise ValueError("Canonical relative resource paths are required")
        elif self.paths:
            raise ValueError("Document and memory references cannot contain file paths")
        return self


class ReviewPolicy(ContractModel):
    policy_id: Identifier
    version: int = Field(ge=1)
    requires_human: bool = True
    max_revisions: int = Field(default=2, ge=0, le=2)


class TaskContract(ContractModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    task_id: Identifier
    user_id: Identifier
    scope: Literal["project", "personal"]
    project_id: Identifier | None
    agent_id: Identifier
    goal: Text
    acceptance_criteria: tuple[Text, ...] = Field(min_length=1, max_length=16)
    allowed_resources: tuple[ResourceRef, ...] = Field(default=(), max_length=16)
    allowed_tools: tuple[ToolId, ...] = Field(default=(), max_length=16)
    permissions: tuple[Permission, ...] = Field(default=(), max_length=3)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    risk_level: RiskLevel
    review_policy: ReviewPolicy
    policy_version: int = Field(ge=1)
    stop_conditions: tuple[StopCondition, ...] = Field(default=REQUIRED_STOPS, max_length=6)
    # A lookup reference, not proof of approval. It is included in the spec digest.
    approval_reference: Identifier | None = None

    @field_validator("goal", "acceptance_criteria")
    @classmethod
    def valid_text(cls, value):
        for text in (value,) if isinstance(value, str) else value:
            if not text.strip() or "\0" in text:
                raise ValueError("Nonblank text without NUL is required")
            try:
                text.encode("utf-8")
            except UnicodeError:
                raise ValueError("UTF-8 text is required") from None
        return value

    @model_validator(mode="after")
    def consistent_scope(self):
        if (self.scope == "project") != (self.project_id is not None):
            raise ValueError("Project scope needs an ID; personal scope must not have one")
        for values in (self.acceptance_criteria, self.allowed_tools, self.permissions,
                       self.stop_conditions):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate contract entries are forbidden")
        identities = [(r.kind, r.resource_id) for r in self.allowed_resources]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate resource identities are forbidden")
        if not set(REQUIRED_STOPS).issubset(self.stop_conditions):
            raise ValueError("Mandatory stop conditions cannot be removed")
        if self.risk_level in {"high", "critical"} and not self.review_policy.requires_human:
            raise ValueError("High risk cannot omit human review")
        return self


def contract_digest(contract: TaskContract) -> str:
    """Content identity only. Revalidate even unchecked model_copy/model_construct inputs."""
    checked = TaskContract.model_validate_json(contract.model_dump_json())
    raw = json.dumps(checked.model_dump(mode="json"), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TaskExecutionState(ContractModel):
    """An observation, not evidence of success or approval. Transitions belong to P1-06."""

    task_id: Identifier
    contract_digest: Sha256
    status: Literal[
        "proposed", "approved", "queued", "running", "verifying", "awaiting_approval",
        "succeeded", "failed", "blocked", "cancelled", "paused", "interrupted",
        "waiting", "budget_exhausted",
    ] = "proposed"
    # UTC Unix seconds; no clock reads or implicit timestamps during construction.
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def ordered_times(self):
        if self.updated_at < self.created_at:
            raise ValueError("Updated time precedes creation")
        return self
