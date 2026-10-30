"""Opt-in benchmark-only Windows loopback HTTP transport, never a model shell tool.

The PowerShell program is constant; JSON request bytes are base64 stdin data.
No profiles/proxy/redirect/default credentials, arbitrary URL, pull or server startup.
"""

import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess

import httpx

SCRIPT = r"""$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
try {
 $r=[Console]::In.ReadToEnd()|ConvertFrom-Json
 $allowed=@('/api/version','/api/tags','/api/ps','/api/show','/api/chat')
 if($r.path -notin $allowed -or $r.method -notin @('GET','POST')){throw 'denied'}
 Add-Type -AssemblyName System.Net.Http
 $handler=[System.Net.Http.HttpClientHandler]::new();$handler.AllowAutoRedirect=$false;$handler.UseProxy=$false;$handler.UseCookies=$false;$handler.UseDefaultCredentials=$false
 $client=[System.Net.Http.HttpClient]::new($handler);$client.Timeout=[TimeSpan]::FromSeconds([Math]::Min(300,[Math]::Max(1,$r.timeout)))
 $method=[System.Net.Http.HttpMethod]::new($r.method)
 $request=[System.Net.Http.HttpRequestMessage]::new($method,('http://127.0.0.1:11434'+$r.path))
 if($r.method -eq 'POST'){$bytes=[Convert]::FromBase64String($r.body);$request.Content=[System.Net.Http.ByteArrayContent]::new($bytes);$request.Content.Headers.ContentType=[System.Net.Http.Headers.MediaTypeHeaderValue]::new('application/json')}
 $response=$client.SendAsync($request,[System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
 $stream=$response.Content.ReadAsStreamAsync().GetAwaiter().GetResult();$buffer=New-Object byte[] 8192;$memory=[System.IO.MemoryStream]::new()
 while(($n=$stream.Read($buffer,0,$buffer.Length)) -gt 0){if($memory.Length+$n -gt 2097152){throw 'response limit'};$memory.Write($buffer,0,$n)}
 @{status=[int]$response.StatusCode;body=[Convert]::ToBase64String($memory.ToArray())}|ConvertTo-Json -Compress
 $stream.Dispose();$response.Dispose();$client.Dispose();$handler.Dispose()
} catch { @{status=502;body='eyJlcnJvciI6IndpbmRvd3NfbG9vcGJhY2tfdHJhbnNwb3J0X2ZhaWxlZCJ9'}|ConvertTo-Json -Compress }
"""
COMMAND = base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")


class WindowsLoopbackTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Trusted caller opts in; no global HTTP interception or settings changes."""

    def __init__(self, model, *, program=None, runner=None):
        self.model = model
        self.program = str(
            program or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        self.runner = runner or subprocess.run
        if runner is None and not Path(self.program).is_file():
            raise ValueError("Windows loopback transport unavailable")

    def envelope(self, request):
        url = request.url
        if (
            url.scheme != "http"
            or url.host not in {"127.0.0.1", "localhost", "::1"}
            or url.port != 11434
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.raw_path != url.path.encode()
        ):
            raise httpx.UnsupportedProtocol("Only fixed Windows Ollama loopback is allowed")
        read = {"/api/version", "/api/tags", "/api/ps"}
        write = {"/api/show", "/api/chat"}
        if (
            (request.method == "GET" and url.path not in read)
            or (request.method == "POST" and url.path not in write)
            or request.method not in {"GET", "POST"}
        ):
            raise httpx.UnsupportedProtocol("Ollama operation is not allowed")
        body = request.content
        if len(body) > 131072:
            raise httpx.ProtocolError("Request exceeds benchmark limit")
        if request.method == "POST":
            try:
                value = json.loads(body)
            except ValueError:
                raise httpx.ProtocolError("Invalid local model request") from None
            if type(value) is not dict or value.get("model") != self.model:
                raise httpx.ProtocolError("Model identity mismatch")
            if url.path == "/api/show" and set(value) != {"model"}:
                raise httpx.ProtocolError("Invalid show request")
            if url.path == "/api/chat":
                if (
                    set(value)
                    - {"model", "messages", "format", "stream", "keep_alive", "options", "think"}
                    or value.get("stream") is not False
                    or value.get("keep_alive") != 0
                    or type(value.get("keep_alive")) is not int
                    or type(value.get("messages")) is not list
                    or not 1 <= len(value["messages"]) <= 32
                ):
                    raise httpx.ProtocolError("Invalid bounded chat request")
                options = value.get("options")
                if (
                    type(options) is not dict
                    or set(options) - {"num_ctx", "num_predict", "temperature"}
                    or type(options.get("num_ctx")) is not int
                    or not 1 <= options["num_ctx"] <= 4096
                    or type(options.get("num_predict")) is not int
                    or not 1 <= options["num_predict"] <= 1024
                    or options.get("temperature") != 0
                ):
                    raise httpx.ProtocolError("Model resource scope exceeded")
        raw_timeout = request.extensions.get("timeout", {}).get("read", 90)
        timeout = min(300, max(1, float(raw_timeout or 90)))
        return {
            "path": url.path,
            "method": request.method,
            "body": base64.b64encode(body).decode(),
            "timeout": timeout,
        }

    def handle_request(self, request):
        envelope = self.envelope(request)
        env = {
            key: os.environ[key]
            for key in ("PATH", "WSL_INTEROP", "WSL_DISTRO_NAME", "LANG")
            if key in os.environ
        }
        env["WSLENV"] = ""
        try:
            result = self.runner(
                [
                    self.program,
                    "-NoProfile",
                    "-NonInteractive",
                    "-NoLogo",
                    "-EncodedCommand",
                    COMMAND,
                ],
                input=json.dumps(envelope, ensure_ascii=True).encode(),
                capture_output=True,
                timeout=envelope["timeout"] + 5,
                env=env,
                check=False,
            )
            if result.returncode != 0 or len(result.stdout) > 3000000:
                raise ValueError()
            response = json.loads(result.stdout.decode("utf-8-sig"))
            status = response.get("status")
            payload = base64.b64decode(response["body"], validate=True)
            if type(status) is not int or not 100 <= status <= 599 or len(payload) > 2097152:
                raise ValueError()
            return httpx.Response(
                status,
                content=payload,
                headers={"content-type": "application/json"},
                request=request,
            )
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            raise httpx.ConnectError(
                "Windows loopback transport failed; no external fallback", request=request
            ) from None

    async def handle_async_request(self, request):
        return await asyncio.to_thread(self.handle_request, request)

    def close(self):
        pass

    async def aclose(self):
        pass
