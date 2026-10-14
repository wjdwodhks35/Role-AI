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
    """Owner-only chat client; never silently retries or falls back."""

    def __init__(self, settings=None, transport=None):
        from role_ai.config import Settings
        self.settings = settings or Settings(_env_file=None)
        self.transport = transport

    def check_access(self, context):
        if not context.owner or self.settings.mode != "local":
            raise ProviderUnavailable("OpenClaw는 로컬 소유자 계정만 사용할 수 있습니다.")
        if not self.settings.openclaw_enabled:
            raise ProviderUnavailable("OpenClaw 연결이 비활성화되어 있습니다.")
        if not self.settings.openclaw_token.get_secret_value():
            raise ProviderUnavailable("OpenClaw 서버 인증 토큰이 설정되지 않았습니다.")
        self.validate_policy()

    def validate_policy(self):
        import json
        from pathlib import Path
        from urllib.parse import urlsplit

        url = urlsplit(self.settings.openclaw_url)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username or url.password or url.query or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ProviderUnavailable("이 버전은 루프백 OpenClaw Gateway만 지원합니다.")
        try:
            config = json.loads(Path(self.settings.openclaw_config_path).read_text("utf-8"))
            model = config["agents"]["defaults"]["model"]
            primary = model if isinstance(model, str) else model["primary"]
            fallbacks = [] if isinstance(model, str) else model.get("fallbacks", [])
            safe = (
                isinstance(config["tools"]["deny"], list) and "*" in config["tools"]["deny"]
                and primary.startswith(("ollama/", "vllm/")) and not fallbacks
                and not config["agents"].get("entries")
                and config["gateway"]["http"]["endpoints"]["chatCompletions"]["enabled"] is True
                and config["gateway"]["auth"]["mode"] == "token"
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            safe = False
        if not safe:
            raise ProviderUnavailable(
                "Gateway 설정 검증 실패: JSON 설정, 전체 도구 차단, 로컬 모델, "
                "빈 fallback, 기본 에이전트와 token 인증이 필요합니다."
            )

    def client(self):
        import httpx
        return httpx.AsyncClient(
            base_url=self.settings.openclaw_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + self.settings.openclaw_token.get_secret_value()},
            timeout=self.settings.provider_timeout_seconds,
            follow_redirects=False, trust_env=False, transport=self.transport,
        )

    @staticmethod
    def session_key(context, skill):
        import hashlib
        value = ":".join([context.user_id, context.conversation_id, skill])
        return "roleai-" + hashlib.sha256(value.encode()).hexdigest()

    async def probe(self, context):
        import httpx
        self.check_access(context)
        try:
            async with self.client() as client:
                response = await client.get("v1/models")
                response.raise_for_status()
                ids = [item["id"] for item in response.json()["data"]]
            if "openclaw/default" not in ids:
                raise ValueError("Missing default agent")
            return {"state": "connected", "engine": "openclaw",
                    "agent": "openclaw/default", "inference_verified": False}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderUnavailable(
                "Gateway 연결 테스트 실패: 실행 상태, 인증 및 HTTP endpoint를 확인하세요."
            ) from None

    async def reply(self, context, messages, provider, skill):
        import httpx
        self.check_access(context)
        try:
            async with self.client() as client:
                response = await client.post("v1/chat/completions", json={
                    "model": "openclaw/default", "stream": False,
                    "user": self.session_key(context, skill),
                    "messages": [{"role": "system", "content": system_prompt(skill)},
                                 messages[-1]],
                    "tools": [], "tool_choice": "none", "max_completion_tokens": 512,
                })
                response.raise_for_status()
                data = response.json()
            message = data["choices"][0]["message"]
            content = message["content"]
            if message.get("tool_calls") or not isinstance(content, str):
                raise ValueError("Unsupported tool response")
            if not content.strip() or len(content) > 32000:
                raise ValueError("Invalid content")
            return ModelReply(content, "openclaw", "openclaw/default")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            raise ProviderUnavailable(
                "OpenClaw 응답 실패. 자동 재시도하지 않습니다. Gateway의 처리 결과를 "
                "확인하거나 기본 비서로 전환하세요."
            ) from None


class AssistantManager:
    def __init__(self, settings, providers, transport=None):
        self.settings = settings
        self.fallback = ProviderAssistant(providers)
        self.openclaw = OpenClawGatewayAdapter(settings, transport)

    def available(self, owner):
        return self.settings.mode == "local" and owner and self.settings.openclaw_enabled

    async def reply(self, engine, context, messages, provider, skill):
        selected = self.openclaw if engine == "openclaw" else self.fallback
        return await selected.reply(context, messages, provider, skill)
