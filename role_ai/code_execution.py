"""Explicit owner-approved Python tests in disposable Docker containers, never on the host."""
import json
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

IMAGE = re.compile(r"(?:docker\.io/library/)?python:3\.12-slim@sha256:[0-9a-f]{64}\Z")
OUTPUT_LIMIT = 32768
RUNNER = '''import sys, unittest
sys.path.insert(0, "/work")
suite = unittest.defaultTestLoader.discover("/work", pattern="test_submission.py")
if suite.countTestCases() == 0:
    print("No tests collected", file=sys.stderr)
    sys.exit(5)
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'''


class ExecutionUnavailable(Exception):
    pass


class ExecutionRequest(BaseModel):
    code: str = Field(min_length=1, max_length=16000)
    tests: str = Field(min_length=1, max_length=16000)
    approved: bool = Field(default=False, strict=True)


class CodeExecutor:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.blocked = False

    def configuration_valid(self):
        return bool(
            self.settings.code_execution_enabled and self.settings.mode == "local"
            and IMAGE.fullmatch(self.settings.code_execution_image)
            and self.settings.code_execution_socket.startswith("/")
            and not any(c in self.settings.code_execution_socket for c in "\r\n\0")
            and os.name == "posix"
        )

    @staticmethod
    def command(base, env, *args, timeout=10):
        try:
            result = subprocess.run(
                [*base, *args], stdin=subprocess.DEVNULL, capture_output=True,
                timeout=timeout, env=env, check=True,
            )
            if len(result.stdout) > 262144:
                raise ExecutionUnavailable("Docker 응답이 너무 큽니다.")
            return result.stdout.decode("utf-8")
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise ExecutionUnavailable("Docker 작업 실패. 실행 환경과 이미지를 확인하세요.") from None

    def preflight(self, base, env):
        info = json.loads(self.command(base, env, "info", "--format", "{{json .}}"))
        if not (
            info.get("OSType") == "linux"
            and all(info.get(k) is True for k in ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit"))
            and any("seccomp" in s and "unconfined" not in s for s in info.get("SecurityOptions", []))
        ):
            raise ExecutionUnavailable("Linux Docker의 메모리·CPU·프로세스·seccomp 제한이 필요합니다.")
        image = json.loads(self.command(
            base, env, "image", "inspect", self.settings.code_execution_image,
            "--format", "{{json .}}",
        ))
        if image.get("Os") != "linux" or image.get("Config", {}).get("Volumes"):
            raise ExecutionUnavailable("실행 이미지 설정을 확인하세요.")

    def create_args(self, name, work):
        return [
            "create", "--pull=never", "--name", name, "--network=none", "--read-only",
            "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--memory=128m", "--memory-swap=128m", "--cpus=0.5", "--pids-limit=32",
            "--ulimit=nofile=64:64", "--ulimit=fsize=1048576:1048576",
            "--ulimit=core=0:0", "--ipc=none",
            "--log-driver=none", "--restart=no", "--no-healthcheck",
            "--label=role-ai.code-execution=true", "--stop-timeout=1",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
            "--mount", f"type=bind,src={work},dst=/work,readonly",
            "--workdir=/work", "--entrypoint=python", self.settings.code_execution_image,
            "-I", "-B", "-u", "/work/runner.py",
        ]

    def capture(self, base, env, name):
        output = bytearray()
        reason = None
        process = subprocess.Popen(
            [*base, "start", "--attach", name], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        )
        deadline = time.monotonic() + self.settings.code_execution_timeout_seconds
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        reason = "timeout"
                        break
                    for key, _ in selector.select(timeout=min(0.1, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        remaining = OUTPUT_LIMIT - len(output)
                        output.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            reason = "output_limit"
                            break
                    if reason:
                        break
                if reason is None:
                    process.wait(timeout=max(.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            reason = "timeout"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
        safe_output = output.decode("utf-8", errors="replace").encode("utf-8")[:OUTPUT_LIMIT]
        return reason, safe_output.decode("utf-8", errors="ignore")

    def execute(self, request):
        if not self.configuration_valid() or self.blocked:
            raise ExecutionUnavailable("코드 실행이 비활성화되었거나 설정/정리 상태 확인이 필요합니다.")
        executable = shutil.which("docker")
        if not executable:
            raise ExecutionUnavailable("Docker가 없어 실행하지 않았습니다. 호스트 실행은 지원하지 않습니다.")
        name = "role-ai-test-" + uuid4().hex
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="role-ai-test-") as root:
            root = Path(root)
            # Docker gets no application credentials, proxy variables or Docker context credentials.
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
                   "DOCKER_CONFIG": str(root / "docker-config")}
            base = [executable, "--host", "unix://" + self.settings.code_execution_socket]
            try:
                self.preflight(base, env)
                work = root / "work"
                work.mkdir(mode=0o755)
                # mkdir's mode is filtered by the caller's umask. The container
                # uid needs to traverse the bind source, but host peers must not
                # traverse its private parent. Do not change the process umask.
                work.chmod(0o755)
                root.chmod(0o700)
                for filename, content in (("submission.py", request.code),
                                          ("test_submission.py", request.tests), ("runner.py", RUNNER)):
                    path = work / filename
                    path.write_text(content, encoding="utf-8")
                    path.chmod(0o444)
                try:
                    self.command(base, env, *self.create_args(name, work))
                    reason, output = self.capture(base, env, name)
                    if reason:
                        try:
                            self.command(base, env, "kill", name)
                        except ExecutionUnavailable:
                            # It may have already exited; inspect still must confirm termination.
                            pass
                    state = json.loads(self.command(base, env, "inspect", "--format", "{{json .State}}", name))
                    if state.get("Running") or state.get("Error"):
                        raise ExecutionUnavailable("컨테이너 실행 상태를 확인할 수 없습니다.")
                    if state.get("OOMKilled"):
                        reason = "memory_limit"
                    return {
                        "id": name, "status": reason or "completed", "executed": True,
                        "exit_code": state["ExitCode"], "output": output,
                        "output_truncated": reason == "output_limit",
                        "seconds": round(time.monotonic() - started, 3),
                        "image": self.settings.code_execution_image,
                        "notice": "실제 컨테이너 실행 결과입니다. 종료 코드 0도 테스트의 충분성·정확성을 보장하지 않습니다.",
                    }
                finally:
                    # Fail closed after cleanup failure; do not accept another job in this process.
                    try:
                        self.command(base, env, "rm", "--force", "--volumes", name)
                    except ExecutionUnavailable:
                        self.blocked = True
                        raise ExecutionUnavailable("실행 컨테이너 정리에 실패했습니다. 관리자가 확인할 때까지 실행을 차단합니다.") from None
            except (OSError, subprocess.SubprocessError):
                raise ExecutionUnavailable("실행 프로세스 오류. 호스트에서 대체 실행하지 않습니다.") from None
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ExecutionUnavailable("Docker 상태 응답 검증 실패. 결과를 신뢰할 수 없습니다.") from None


def execution_router(authenticated, settings):
    router = APIRouter(prefix="/api/code-execution")
    executor = CodeExecutor(settings)

    @router.post("/run")
    def run(body: ExecutionRequest, actor=Depends(authenticated)):
        if not actor[0].is_owner or settings.mode != "local":
            raise HTTPException(403, "로컬 소유자만 코드 테스트를 실행할 수 있습니다.")
        if not body.approved:
            raise HTTPException(400, "이번 코드와 테스트의 실행 승인이 필요합니다.")
        if not executor.lock.acquire(blocking=False):
            raise HTTPException(409, "다른 코드 테스트가 실행 중입니다.")
        try:
            return executor.execute(body)
        except ExecutionUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        finally:
            executor.lock.release()

    return router
