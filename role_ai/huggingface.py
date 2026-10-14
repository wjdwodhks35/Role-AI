import json
from urllib.parse import quote, urljoin, urlsplit

import httpx


class HuggingFaceClient:
    """Bounded HTTP-only discovery and pinned JSONL prefix import; no repository scripts."""

    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    def ensure_enabled(self):
        if not self.settings.hf_download_enabled:
            raise ValueError("Hugging Face 네트워크 조회가 비활성화되어 있습니다.")

    @staticmethod
    def allowed(url):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and (host == "huggingface.co" or host == "cdn-lfs.huggingface.co"
                     or host.endswith(".hf.co")))

    def headers(self, url):
        if urlsplit(url).hostname == "huggingface.co" and self.settings.hf_token.get_secret_value():
            return {"Authorization": "Bearer " + self.settings.hf_token.get_secret_value()}
        return {}

    async def bounded(self, url, maximum, row_limit=None):
        self.ensure_enabled()
        async with httpx.AsyncClient(timeout=30, follow_redirects=False,
                                     trust_env=False, transport=self.transport) as client:
            for _ in range(4):
                if not self.allowed(url):
                    raise ValueError("허용되지 않은 Hugging Face 다운로드 주소입니다.")
                async with client.stream("GET", url, headers=self.headers(url)) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    data, rows, received = b"", [], 0
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        received += len(chunk)
                        if received > maximum:
                            raise ValueError("다운로드 크기 제한에 도달했습니다. 더 작은 JSONL 파일을 선택하세요.")
                        data += chunk
                        if row_limit is not None:
                            while b"\n" in data:
                                line, data = data.split(b"\n", 1)
                                if line.strip():
                                    rows.append(json.loads(line))
                                if len(rows) >= row_limit:
                                    return rows
                            if len(data) > 32768:
                                raise ValueError("JSONL 한 행의 크기가 너무 큽니다.")
                    if row_limit is None:
                        return json.loads(data)
                    if data.strip() and len(rows) < row_limit:
                        rows.append(json.loads(data))
                    return rows
        raise ValueError("Hugging Face 리다이렉트 한도를 초과했습니다.")

    async def search(self, query):
        data = await self.bounded("https://huggingface.co/api/datasets?limit=10&search=" +
                                  quote(query, safe=""), 262144)
        return [{"repo_id": item["id"]} for item in data[:10]]

    async def sample(self, repo_id, revision, filename, rows, prompt_field, completion_field):
        # Caller validates repo/file segments and requires a full pinned commit.
        info = await self.bounded(
            "https://huggingface.co/api/datasets/" + repo_id + "/revision/" + revision, 262144
        )
        if info["sha"] != revision:
            raise ValueError("데이터셋 revision을 확인할 수 없습니다.")
        license_id = (info.get("cardData") or {}).get("license", "unknown")
        if not isinstance(license_id, str):
            license_id = "unknown"
        source = {"type": "huggingface", "repo_id": repo_id, "revision": revision,
                  "filename": filename, "license": license_id,
                  "prompt_field": prompt_field, "completion_field": completion_field,
                  "requested_rows": rows, "scan_limit_bytes": self.settings.hf_max_scan_bytes}
        url = "https://huggingface.co/datasets/" + repo_id + "/resolve/" + revision + "/" + quote(filename, safe="/")
        raw = await self.bounded(url, self.settings.hf_max_scan_bytes, row_limit=rows)
        mapped = [{"prompt": r.get(prompt_field), "completion": r.get(completion_field)}
                  for r in raw if isinstance(r, dict)]
        return mapped, source
