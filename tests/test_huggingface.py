import asyncio
import json

import httpx
import pytest

from role_ai.config import Settings
from role_ai.huggingface import HuggingFaceClient
from role_ai.learning import HFInput


def test_pinned_bounded_jsonl_import():
    calls = []
    revision = "a" * 40
    data = "\n".join(json.dumps({"prompt": f"q{i}", "completion": f"a{i}"}) for i in range(100))
    def handler(request):
        calls.append(request)
        if "/api/datasets/" in request.url.path:
            return httpx.Response(200, json={"sha": revision, "cardData": {"license": "mit"}})
        return httpx.Response(200, text=data)
    client = HuggingFaceClient(Settings(_env_file=None, hf_download_enabled=True),
                               httpx.MockTransport(handler))
    rows, source = asyncio.run(client.sample("owner/data", revision, "train.jsonl", 10,
                                            "prompt", "completion"))
    assert len(rows) == 10 and source["revision"] == revision
    assert len(calls) == 2 and revision in str(calls[-1].url)


def test_network_disabled_and_scan_limit():
    with pytest.raises(ValueError):
        asyncio.run(HuggingFaceClient(Settings(_env_file=None)).search("test"))
    client = HuggingFaceClient(
        Settings(_env_file=None, hf_download_enabled=True, hf_max_scan_bytes=1024),
        httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 9000))
    )
    with pytest.raises(ValueError):
        asyncio.run(client.bounded("https://huggingface.co/file", 1024, row_limit=10))


def test_token_not_forwarded_to_cdn_and_untrusted_redirect_blocked():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"Location": "https://cdn-lfs.hf.co/data"})
        return httpx.Response(200, json=[])
    settings = Settings(_env_file=None, hf_download_enabled=True, hf_token="fake-hf-secret")
    client = HuggingFaceClient(settings, httpx.MockTransport(handler))
    assert asyncio.run(client.search("test")) == []
    assert "authorization" in calls[0].headers
    assert "authorization" not in calls[1].headers
    evil = HuggingFaceClient(settings, httpx.MockTransport(
        lambda _: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})))
    with pytest.raises(ValueError):
        asyncio.run(evil.search("test"))


@pytest.mark.parametrize("filename", ["../secret.jsonl", "/tmp/data.jsonl", "data.parquet", "load.py"])
def test_unsupported_paths_and_formats_rejected(filename):
    with pytest.raises(ValueError):
        HFInput(name="test", repo_id="owner/data", revision="a" * 40, filename=filename)
