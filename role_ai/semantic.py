"""On-demand local embeddings; no remote provider, auto-download, or hidden fallback."""
import asyncio
import json
import math
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from role_ai.artifacts import storage_lock
from role_ai.gpu_resources import GpuBudgetError, preflight


class SemanticRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    project: str = Field(default="", max_length=80)
    document_ids: list[str] = Field(min_length=1, max_length=5)


class SemanticSearch:
    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    async def search(self, query, sources):
        if not self.settings.embedding_model:
            raise HTTPException(503, "로컬 임베딩 모델이 설정되지 않았습니다. 키워드 검색은 사용할 수 있습니다.")
        url = urlsplit(self.settings.ollama_url)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
                or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"}):
            raise HTTPException(503, "의미 검색은 HTTP 로컬 또는 Docker 호스트 Ollama만 지원합니다.")
        if not query.strip():
            raise HTTPException(422, "검색어를 입력하세요.")
        if not sources:
            return []
        if len(sources) > 128:
            raise HTTPException(409, "검색할 문서를 줄여주세요.")
        try:
            with storage_lock(self.settings.storage_dir):
                preflight(self.settings, "embedding")
                async with asyncio.timeout(self.settings.provider_timeout_seconds):
                    async with httpx.AsyncClient(
                        base_url=self.settings.ollama_url.rstrip("/") + "/",
                        timeout=self.settings.provider_timeout_seconds,
                        transport=self.transport, trust_env=False, follow_redirects=False,
                    ) as client:
                        # Refuse cloud-backed Ollama models even behind a loopback endpoint.
                        shown = await client.post("api/show", json={"model": self.settings.embedding_model})
                        shown.raise_for_status()
                        metadata = shown.json()
                        if not isinstance(metadata, dict):
                            raise ValueError("invalid metadata")
                        if metadata.get("remote_host") or metadata.get("remote_model"):
                            raise ValueError("remote model is forbidden")
                        if "embedding" not in metadata.get("capabilities", []):
                            raise ValueError("not an embedding model")
                        async with client.stream("POST", "api/embed", json={
                            "model": self.settings.embedding_model,
                            "input": [query] + [s["text"] for s in sources],
                            "truncate": False, "keep_alive": 0,
                        }) as response:
                            response.raise_for_status()
                            payload = bytearray()
                            async for chunk in response.aiter_bytes():
                                payload.extend(chunk)
                                if len(payload) > 8 * 1024 * 1024:
                                    raise ValueError("oversized embeddings")
                data = json.loads(payload)
                if not isinstance(data, dict):
                    raise ValueError("invalid response")
                if data.get("model") != self.settings.embedding_model:
                    raise ValueError("unexpected model")
                vectors = data["embeddings"]
                if not isinstance(vectors, list) or len(vectors) != len(sources) + 1:
                    raise ValueError("wrong vector count")
                normalized, dimension = [], None
                for vector in vectors:
                    if not isinstance(vector, list) or not 1 <= len(vector) <= 4096:
                        raise ValueError("bad dimensions")
                    if dimension is not None and len(vector) != dimension:
                        raise ValueError("dimension mismatch")
                    dimension = len(vector)
                    if any(type(v) not in {float, int} or not math.isfinite(v)
                           or abs(v) > 1e6 for v in vector):
                        raise ValueError("invalid numbers")
                    norm = math.hypot(*vector)
                    if norm == 0:
                        raise ValueError("zero vector")
                    normalized.append([v / norm for v in vector])
        except GpuBudgetError as exc:
            raise HTTPException(503, str(exc)) from None
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            raise HTTPException(503, "로컬 임베딩 연결·응답 또는 자원 상태를 확인하세요. 검색을 대신 실행하지 않았습니다.") from None
        ranked = []
        for source, vector in zip(sources, normalized[1:], strict=True):
            score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(normalized[0], vector))))
            if score >= self.settings.embedding_min_score:
                ranked.append({**source, "score": round(score, 6)})
        ranked.sort(key=lambda item: (-item["score"], item["document_id"], item["position"]))
        return ranked[:5]
