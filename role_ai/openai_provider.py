import httpx

from role_ai.providers import ModelReply, ProviderUnavailable


class OpenAIProvider:
    """Official Responses API; owner-only consent and durable budget reservation."""

    def __init__(self, settings, ledger=None, transport=None):
        self.settings, self.ledger, self.transport = settings, ledger, transport

    def configured(self):
        if self.ledger is None:
            return False
        try:
            self.ledger.ensure_configured()
            return True
        except ProviderUnavailable:
            return False

    def client(self):
        return httpx.AsyncClient(
            base_url="https://api.openai.com/v1/",
            headers={"Authorization": "Bearer " + self.settings.openai_api_key.get_secret_value()},
            timeout=self.settings.provider_timeout_seconds, follow_redirects=False,
            trust_env=False, transport=self.transport,
        )

    async def probe(self):
        if not self.configured():
            raise ProviderUnavailable("OpenAI 설정과 비용 정책을 먼저 완료하세요.")
        try:
            async with self.client() as client:
                response = await client.get("models/" + self.settings.openai_model)
                response.raise_for_status()
                if response.json()["id"] != self.settings.openai_model:
                    raise ValueError("Unexpected model")
            return {"state": "connected", "provider": "openai",
                    "model": self.settings.openai_model, "inference_verified": False}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            raise ProviderUnavailable("OpenAI 인증/모델 접근 테스트에 실패했습니다.") from None

    async def complete(self, messages):
        raise ProviderUnavailable("OpenAI 유료 호출은 소유자 확인과 요청별 동의가 필요합니다.")

    async def complete_for_user(self, messages, user_id, owner, consent):
        if not owner or not consent:
            raise ProviderUnavailable("OpenAI 호출에는 소유자 권한과 요청별 비용 동의가 필요합니다.")
        if not self.configured():
            raise ProviderUnavailable("OpenAI 설정과 비용 정책을 먼저 완료하세요.")
        usage_id = self.ledger.reserve(user_id, messages)
        usage = None
        try:
            async with self.client() as client:
                response = await client.post("responses", json={
                    "model": self.settings.openai_model, "input": messages,
                    "max_output_tokens": self.settings.openai_max_output_tokens,
                    "store": False, "tools": [], "tool_choice": "none",
                })
                response.raise_for_status()
                data = response.json()
            raw_usage = data.get("usage") or {}
            if not isinstance(raw_usage, dict):
                raw_usage = {}
            counts = (raw_usage.get("input_tokens"), raw_usage.get("output_tokens"))
            if all(type(value) is int and 0 <= value <= 100_000_000 for value in counts):
                usage = counts
            actual_model = data.get("model")
            if data.get("status") != "completed" or not isinstance(actual_model, str) or not actual_model or len(actual_model) > 100:
                raise ValueError("Unexpected status/model")
            parts = []
            for item in data["output"]:
                if item["type"] == "reasoning":
                    continue
                if item["type"] != "message" or item.get("role") != "assistant":
                    raise ValueError("Unsupported output")
                for part in item["content"]:
                    if part["type"] != "output_text" or not isinstance(part["text"], str):
                        raise ValueError("Unsupported content")
                    parts.append(part["text"])
            content = "\n".join(parts)
            if not content.strip() or len(content) > 32000:
                raise ValueError("Invalid text")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            self.ledger.finish(usage_id, usage)
            raise ProviderUnavailable(
                "OpenAI 응답 실패. 자동 재시도하지 않습니다. 사용량이 불명확하면 "
                "예약 비용을 유지합니다. 사용량 화면을 확인하세요."
            ) from None
        except BaseException:
            # Cancellation also may occur after the provider accepted the request.
            self.ledger.finish(usage_id, usage)
            raise
        cost, state = self.ledger.finish(usage_id, usage)
        return ModelReply(
            content, "openai", actual_model,
            input_tokens=usage[0] if usage else None,
            output_tokens=usage[1] if usage else None,
            cost_usd=cost, billing_state=state,
        )
