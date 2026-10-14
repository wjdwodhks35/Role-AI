from dataclasses import dataclass
from typing import Protocol

import httpx

from role_ai.config import Settings


class ProviderUnavailable(Exception):
    """Safe public reason only; do not pass upstream bodies or credentials through."""


@dataclass(frozen=True)
class ModelReply:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    simulated: bool = False
    cost_usd: str | None = None
    billing_state: str | None = None


class ModelProvider(Protocol):
    async def complete(self, messages: list[dict[str, str]]) -> ModelReply: ...
    async def probe(self) -> dict: ...


class OllamaProvider:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    def client(self):
        return httpx.AsyncClient(
            base_url=self.settings.ollama_url.rstrip("/") + "/",
            timeout=self.settings.provider_timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        )

    async def complete(self, messages):
        try:
            async with self.client() as client:
                response = await client.post("api/chat", json={
                    "model": self.settings.ollama_model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": "5m",
                    "options": {"num_predict": 512, "num_ctx": 4096},
                })
                response.raise_for_status()
                data = response.json()
            content = data["message"]["content"]
            if not isinstance(content, str) or not content.strip() or len(content) > 32000:
                raise ValueError("Invalid content")
            return ModelReply(
                text=content, provider="ollama", model=self.settings.ollama_model,
                input_tokens=data.get("prompt_eval_count"),
                output_tokens=data.get("eval_count"),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderUnavailable(
                "Ollama 응답을 받지 못했습니다. 서버 실행 상태와 설치된 모델을 확인해 주세요."
            ) from None

    async def probe(self):
        try:
            async with self.client() as client:
                response = await client.get("api/tags")
                response.raise_for_status()
                models = response.json()["models"]
            names = [model["name"] for model in models]
            target = self.settings.ollama_model
            if target not in names and target + ":latest" not in names:
                raise ProviderUnavailable("Ollama는 연결됐지만 선택한 모델이 설치되지 않았습니다.")
            return {"state": "connected", "provider": "ollama", "model": target}
        except ProviderUnavailable:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderUnavailable("Ollama 연결 테스트에 실패했습니다.") from None


class MockProvider:
    async def complete(self, messages):
        return ModelReply(
            text="[테스트 응답 · 실제 AI 아님] " + messages[-1]["content"],
            provider="mock", model="mock-test", simulated=True,
        )

    async def probe(self):
        return {"state": "simulated", "provider": "mock", "model": "mock-test"}


class ReservedProvider:
    def __init__(self, name):
        self.name = name

    async def complete(self, messages):
        raise ProviderUnavailable(f"{self.name}: Phase 2에서 실제 연결을 구현할 예정입니다.")

    async def probe(self):
        raise ProviderUnavailable(f"{self.name}: 실제 연결이 아직 구현되지 않았습니다.")


class ProviderManager:
    def __init__(self, settings: Settings, ollama_transport=None, ledger=None, openai_transport=None):
        from role_ai.openai_provider import OpenAIProvider
        self.settings = settings
        self.providers: dict[str, ModelProvider] = {
            "ollama": OllamaProvider(settings, ollama_transport),
            "vllm": ReservedProvider("vLLM"),
            "openai": OpenAIProvider(settings, ledger, openai_transport),
        }
        if settings.demo_enabled:
            self.providers["mock"] = MockProvider()

    def get(self, name):
        if name not in self.providers:
            raise ProviderUnavailable("이 Provider는 사용할 수 없습니다.")
        return self.providers[name]

    def catalog(self):
        ready = self.providers["openai"].configured()
        items = [
            {"id": "ollama", "model": self.settings.ollama_model,
             "state": "not_verified", "usable": True, "paid_calls": False},
            {"id": "vllm", "model": self.settings.vllm_model,
             "state": "not_implemented", "usable": False, "paid_calls": False},
            {"id": "openai", "model": self.settings.openai_model,
             "state": "not_verified" if ready else "disabled", "usable": ready, "paid_calls": ready},
        ]
        if self.settings.demo_enabled:
            items.append({"id": "mock", "model": "mock-test", "state": "simulated",
                          "usable": True, "paid_calls": False})
        return items
