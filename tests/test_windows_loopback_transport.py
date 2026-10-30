"""Constant-program IPC transport, all model content remains stdin data."""

import base64
import json
from types import SimpleNamespace
import httpx
import pytest
from role_ai.task_windows_transport import WindowsLoopbackTransport, COMMAND


def test_untrusted_model_message_never_becomes_powershell_source_or_credentials():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": 200,
                    "body": base64.b64encode(b'{"message":{"content":"safe"}}').decode(),
                }
            ).encode(),
        )

    transport = WindowsLoopbackTransport("installed", program="fixed-powershell.exe", runner=runner)
    malicious = "'; Remove-Item anything; $(Get-Content secret) 한국어"
    request = httpx.Request(
        "POST",
        "http://127.0.0.1:11434/api/chat",
        headers={"Authorization": "must-not-forward"},
        json={
            "model": "installed",
            "messages": [{"role": "user", "content": malicious}],
            "stream": False,
            "keep_alive": 0,
            "options": {"num_ctx": 4096, "num_predict": 1024, "temperature": 0},
        },
    )
    response = transport.handle_request(request)
    argv, kwargs = calls[0]
    assert argv == [
        "fixed-powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-NoLogo",
        "-EncodedCommand",
        COMMAND,
    ]
    assert malicious not in " ".join(argv)
    envelope = json.loads(kwargs["input"])
    body = json.loads(base64.b64decode(envelope["body"]))
    assert body["messages"][0]["content"] == malicious
    assert "must-not-forward" not in kwargs["input"].decode()
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert response.json()["message"]["content"] == "safe"


@pytest.mark.parametrize(
    "url,method,body",
    [
        ("https://127.0.0.1:11434/api/chat", "POST", {}),
        ("http://example.invalid:11434/api/tags", "GET", None),
        ("http://127.0.0.1:11434/api/pull", "POST", {"model": "installed"}),
        ("http://127.0.0.1:11434/api/generate", "POST", {"model": "installed", "keep_alive": 0}),
        ("http://127.0.0.1:11434/api/show", "POST", {"model": "different"}),
        ("http://127.0.0.1:11434/api/tags?token=secret", "GET", None),
        ("http://user:secret@127.0.0.1:11434/api/tags", "GET", None),
        (
            "http://127.0.0.1:11434/api/chat",
            "POST",
            {
                "model": "installed",
                "messages": [{"role": "user", "content": "x"}],
                "stream": False,
                "keep_alive": 300,
                "options": {"num_ctx": 4096, "num_predict": 1024, "temperature": 0},
            },
        ),
    ],
)
def test_unsupported_network_model_or_cache_operations_never_execute(url, method, body):
    calls = []

    def runner(*args, **kwargs):
        calls.append(True)
        raise AssertionError()

    transport = WindowsLoopbackTransport("installed", runner=runner)
    with pytest.raises(httpx.HTTPError):
        transport.handle_request(
            httpx.Request(method, url, json=body)
            if body is not None
            else httpx.Request(method, url)
        )
    assert not calls


def test_transport_does_not_follow_redirects_or_copy_private_failure_text():
    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b"private-failure")

    transport = WindowsLoopbackTransport("installed", runner=runner)
    with pytest.raises(httpx.ConnectError) as error:
        transport.handle_request(httpx.Request("GET", "http://127.0.0.1:11434/api/ps"))
    assert "private" not in str(error.value)
    code = base64.b64decode(COMMAND).decode("utf-16-le")
    assert (
        "AllowAutoRedirect=$false" in code
        and "UseProxy=$false" in code
        and "UseDefaultCredentials=$false" in code
    )
    assert "Invoke-Expression" not in code
