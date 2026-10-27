"""Validated role definitions. No inference, task dispatch or permission grants."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from role_ai.agent_prompts import PROMPTS_V1

AgentId = Literal["conversation", "coding", "research", "knowledge", "learning", "evaluation"]
CAPABILITIES = {
    "conversation": ("conversation.read",),
    "coding": ("code.propose", "sandbox.request"),
    "research": ("sources.discover",),
    "knowledge": ("knowledge.search",),
    "learning": ("dataset.curate",),
    "evaluation": ("evaluation.run",),
}
PURPOSES = {
    "conversation": "일반 대화와 한국어 응답",
    "coding": "코드 수정안과 승인된 격리 검증 요청",
    "research": "허용된 소스의 근거 탐색",
    "knowledge": "동의한 기억과 문서의 격리 검색",
    "learning": "검증된 학습 후보와 격리 실험 계획",
    "evaluation": "독립 평가와 회귀 검증",
}


class AgentConfigurationError(ValueError):
    """Safe diagnostic: never include configuration contents or secret values."""


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: AgentId
    mode: Literal["definition_only"] = "definition_only"
    model_ref: Literal["current_local", "coding_local"] = "current_local"
    capabilities: tuple[str, ...]
    prompt_version: Literal["v1"] = "v1"
    max_steps: int = Field(default=1, ge=1, le=8)
    timeout_seconds: int = Field(default=60, ge=1, le=300)

    @model_validator(mode="after")
    def validate_boundaries(self):
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Duplicate capability")
        if not set(self.capabilities).issubset(CAPABILITIES[self.id]):
            raise ValueError("Capability exceeds role boundary")
        if self.id != "coding" and self.model_ref != "current_local":
            raise ValueError("Coding reference belongs to the coding role")
        return self


class AgentConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    max_local_active: Literal[1] = 1
    agents: tuple[AgentDefinition, ...] = Field(min_length=6, max_length=6)

    @field_validator("schema_version", "max_local_active", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("An integer policy version/limit is required")
        return value

    @model_validator(mode="after")
    def complete_roles(self):
        if {a.id for a in self.agents} != set(CAPABILITIES):
            raise ValueError("Exactly one definition for each role is required")
        return self


def default_configuration():
    return AgentConfiguration(
        agents=tuple(
            AgentDefinition(
                id=role,
                capabilities=capabilities,
                model_ref="coding_local" if role == "coding" else "current_local",
            )
            for role, capabilities in CAPABILITIES.items()
        )
    )


def load_configuration(directory, filename=""):
    if not filename:
        return default_configuration()
    # This path is an operator setting, never an API argument. Still reject
    # traversal, symlinks and special files rather than trusting string prefixes.
    if (
        Path(filename).name != filename
        or not filename.endswith(".json")
        or "\\" in filename
        or len(filename) > 80
    ):
        raise AgentConfigurationError("Invalid agent configuration filename")
    root = Path(directory).absolute()
    path = root / filename
    try:
        if root.resolve() != root or path.resolve() != path or not path.is_file():
            raise AgentConfigurationError("Agent configuration must be a regular local file")
        with path.open("rb") as stream:
            payload = stream.read(65537)
        if len(payload) > 65536:
            raise AgentConfigurationError("Agent configuration exceeds size limit")

        # Reject duplicate JSON keys as well as unknown schema keys.
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate key")
                result[key] = value
            return result

        json.loads(payload, object_pairs_hook=unique)
        return AgentConfiguration.model_validate_json(payload)
    except AgentConfigurationError:
        raise
    except (OSError, ValueError, ValidationError, RecursionError):
        raise AgentConfigurationError("Invalid agent configuration; no fallback was used") from None


class AgentRegistry:
    def __init__(self, settings):
        self.configuration = load_configuration(
            settings.agent_config_dir, settings.agent_config_file
        )
        self.source = "configured" if settings.agent_config_file else "built_in"
        # Resolve the existing local model setting without selecting a new model.
        self.models = {
            "current_local": settings.ollama_model,
            "coding_local": getattr(settings, "ollama_coding_model", "") or settings.ollama_model,
        }
        self.prompts = {
            role: hashlib.sha256(prompt.encode()).hexdigest() for role, prompt in PROMPTS_V1.items()
        }
        material = {
            "configuration": self.configuration.model_dump(mode="json"),
            "models": self.models,
            "prompt_sha256": self.prompts,
        }
        self.config_hash = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    def describe(self):
        return {
            "schema_version": 1,
            "source": self.source,
            "config_hash": self.config_hash,
            "execution_enabled": False,
            "max_local_active": self.configuration.max_local_active,
            "scheduler_enforced": False,
            "agents": [
                {
                    **agent.model_dump(mode="json"),
                    "purpose": PURPOSES[agent.id],
                    "model": self.models[agent.model_ref],
                    "prompt_sha256": self.prompts[agent.id],
                    "prompt_ref": f"{agent.id}:v1",
                    "log_namespace": f"agent.{agent.id}",
                }
                for agent in self.configuration.agents
            ],
            "notice": "역할 정의만 조회합니다. 모델 실행·도구 권한 부여·학습·배포는 수행하지 않습니다.",
        }


def agents_router(authenticated, settings, registry):
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    @router.get("")
    def listing(actor=Depends(authenticated)):
        if not actor[0].is_owner or settings.mode != "local":
            raise HTTPException(403, "에이전트 구성은 로컬 소유자 전용입니다.")
        return registry.describe()

    return router
