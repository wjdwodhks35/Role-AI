from dataclasses import dataclass
from typing import Protocol

from role_ai.providers import ModelReply, ProviderManager, ProviderUnavailable
from role_ai.skills import system_prompt


@dataclass(frozen=True)
class AssistantContext:
    user_id: str
    conversation_id: str
    owner: bool = False


class AssistantEngine(Protocol):
    async def reply(
        self, context: AssistantContext, messages: list[dict[str, str]],
        provider: str, skill: str,
    ) -> ModelReply: ...


class ProviderAssistant:
    """Phase 1 fallback; one selected model with an explicit Skill prompt."""

    def __init__(self, providers: ProviderManager):
        self.providers = providers

    async def reply(self, context, messages, provider, skill):
        prompt = [{"role": "system", "content": system_prompt(skill)}] + messages[-20:]
        return await self.providers.get(provider).complete(prompt)


class OpenClawGatewayAdapter:
    """Reserved engine boundary. No shared operator Gateway is exposed to public users."""

    async def reply(self, context, messages, provider, skill):
        raise ProviderUnavailable(
            "OpenClaw 어댑터 인터페이스만 준비됐으며 실제 Gateway 연결은 아직 구현되지 않았습니다."
        )
