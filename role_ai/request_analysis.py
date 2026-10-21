"""Local request preview. Suggestions never dispatch or authorize work."""
import asyncio
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator

from role_ai.providers import OllamaProvider
from role_ai.requirements_eval import Analysis, StrictInput, analysis_prompt

ANALYSIS_TIMEOUT_SECONDS = 90


class PreviewInput(StrictInput):
    content: str = Field(min_length=1, max_length=2000)
    approved: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def nonempty(self):
        if not self.content.strip():
            raise ValueError('요청을 입력하세요.')
        return self


def recommend(analysis):
    intents = set(analysis.intents)
    # Multiple roles are shown without inventing confidence scores or silently switching.
    roles = []
    for intent, role in [('test_code', 'code_tester'), ('fix_code', 'developer'),
                         ('write_code', 'developer'), ('explain', 'professor')]:
        if intent in intents and role not in roles:
            roles.append(role)
    return roles or ['assistant']


def request_analysis_router(authenticated, settings, providers):
    router = APIRouter(prefix='/api/request-analysis')
    live_lock = asyncio.Lock()

    @router.post('/preview')
    async def preview(body: PreviewInput, actor=Depends(authenticated)):
        if not actor[0].is_owner or settings.mode != 'local':
            raise HTTPException(403, '요구사항 분석은 로컬 소유자 전용입니다.')
        if not body.approved:
            raise HTTPException(400, '이번 요청을 로컬 Ollama로 보내는 동의가 필요합니다.')
        url = urlsplit(settings.ollama_url)
        provider = providers.get('ollama')
        if (not isinstance(provider, OllamaProvider) or url.scheme != 'http'
                or url.hostname not in {'127.0.0.1', 'localhost', '::1', 'host.docker.internal'}
                or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}):
            raise HTTPException(403, '분석에는 로컬 Ollama만 사용할 수 있습니다.')
        if live_lock.locked():
            raise HTTPException(409, '다른 요청을 분석 중입니다.')
        async with live_lock:
            try:
                async with asyncio.timeout(ANALYSIS_TIMEOUT_SECONDS):
                    async with provider.client() as client:
                        response = await client.post('api/show', json={'model': settings.ollama_model})
                        response.raise_for_status()
                        info = response.json()
                        if (not isinstance(info, dict) or info.get('remote_host') or info.get('remote_model')
                                or 'completion' not in info.get('capabilities', [])):
                            raise ValueError('Local completion model required')
                    reply = await provider.complete(analysis_prompt(body.content))
                    if (reply.simulated or reply.provider != 'ollama' or reply.model != settings.ollama_model
                            or len(reply.text) > 8000):
                        raise ValueError('Invalid local response')
                    result = Analysis.model_validate(json.loads(reply.text))
            except TimeoutError:
                raise HTTPException(504, '분석 시간이 초과됐습니다. 요청을 짧게 나누어 주세요.') from None
            except Exception:
                raise HTTPException(502, '로컬 모델 연결 또는 분석 JSON 검증에 실패했습니다.') from None
        return {'analysis': result.model_dump(), 'suggested_roles': recommend(result),
                'provider': 'ollama', 'model': settings.ollama_model, 'execution': 'none',
                'scope': '분석은 참고용입니다. 역할 적용은 선택 사항이며 실행·학습·유료 API 승인이 아닙니다.'}

    return router
