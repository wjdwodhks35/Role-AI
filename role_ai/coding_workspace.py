"""Bounded text-only Git workspaces. Never run checkout hooks, filters or submitted code.

Internal library for a trusted owner permission gate, not an HTTP/model tool.
The worktree directory must remain private: untrusted code must never run here.
"""

import difflib
import functools
import hashlib
import hmac
import os
import stat
import subprocess
import threading
import time
from pathlib import Path

from role_ai.coding_tasks import (
    CodingBoundaryError, CodingTaskSpec, FileChange, change_digest, safe_path, task_digest,
)

MAX_FILES = 256
MAX_FILE_BYTES = 262144
MAX_REPOSITORY_BYTES = 2 * 1024 * 1024


def exclusive(operation):
    @functools.wraps(operation)
    def guarded(self, *args, **kwargs):
        if not self.lock.acquire(blocking=False):
            raise CodingBoundaryError("동일 작업의 다른 변경이 진행 중입니다.")
        try:
            return operation(self, *args, **kwargs)
        finally:
            self.lock.release()
    return guarded


def git(directory, *args):
    # Do not inherit Git command/config injection, credentials or proxy settings.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
             "-c", "core.untrackedCache=false", "-C", str(directory), *args],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise CodingBoundaryError("격리 Git 작업에 실패했습니다. 결과를 확인하세요.") from None


def private_directory(path):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise CodingBoundaryError("작업 경로에 링크를 사용할 수 없습니다.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise CodingBoundaryError("소유자 전용 작업 디렉터리가 필요합니다.")
    return path


def read_text(path):
    """Read no symlink/special file and bound reads even after file replacement."""
    if not path.exists() and not path.is_symlink():
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise CodingBoundaryError("일반 단일 링크 파일만 사용할 수 있습니다.")
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
            raise CodingBoundaryError("텍스트 파일 크기 또는 형식 제한을 초과했습니다.")
        raw.decode("utf-8")
        return raw
    except (OSError, UnicodeError):
        raise CodingBoundaryError("작업 파일을 안전하게 읽을 수 없습니다.") from None


class CodingWorkspace:
    def __init__(self, repository, root, spec: CodingTaskSpec):
        if os.name != "posix":
            raise CodingBoundaryError("Linux/WSL의 사유 작업공간만 지원합니다.")
        # repository comes from an operator-controlled allowlist, never a model path.
        self.repository = Path(repository).absolute()
        self.root = Path(root).absolute()
        if self.repository.resolve() != self.repository or (
            self.repository.is_relative_to(self.root) or self.root.is_relative_to(self.repository)
        ):
            raise CodingBoundaryError("저장소와 작업 루트는 분리되어야 합니다.")
        self.root = private_directory(self.root)
        self.spec = spec
        self.path = self.root / spec.task_id
        self.status = "planned"
        self.started = time.monotonic()
        self.started_at = time.time()
        self.baseline = {}
        self.modes = {}
        self.git_marker = None
        self.applied = False
        self.lock = threading.Lock()

    def event(self):
        return {
            "task_id": self.spec.task_id, "agent_id": "coding", "status": self.status,
            "user_id": self.spec.user_id, "project_id": self.spec.project_id,
            "phase": "workspace", "requires_approval": self.status in {"planned", "ready"},
            "task_sha256": task_digest(self.spec), "executed": False,
            "tests_state": "not_run", "progress": None,
            "started_at": self.started_at, "updated_at": time.time(),
            "heartbeat": None, "resource_usage": None,
        }

    def check_deadline(self):
        if time.monotonic() - self.started > self.spec.budget.timeout_seconds:
            raise CodingBoundaryError("작업 시간 예산을 초과했습니다.")

    @exclusive
    def prepare(self, approved_task_sha256):
        if self.status != "planned" or not hmac.compare_digest(
            task_digest(self.spec), approved_task_sha256
        ):
            raise CodingBoundaryError("현재 작업 범위에 대한 승인이 필요합니다.")
        self.check_deadline()
        if self.path.exists() or self.path.is_symlink():
            raise CodingBoundaryError("기존 작업 경로를 재사용하지 않습니다.")
        if git(self.repository, "rev-parse", "--show-toplevel").decode().strip() != str(self.repository):
            raise CodingBoundaryError("등록된 저장소 루트만 사용할 수 있습니다.")
        # Exact commit only; no moving refs or user-provided revision expressions.
        if git(self.repository, "rev-parse", self.spec.base_commit + "^{commit}").decode().strip() != self.spec.base_commit:
            raise CodingBoundaryError("기준 커밋이 일치하지 않습니다.")
        entries = git(self.repository, "ls-tree", "-r", "-z", "-l", self.spec.base_commit).split(b"\0")
        if len(entries) - 1 > MAX_FILES:
            raise CodingBoundaryError("소형 저장소 파일 수 제한을 초과했습니다.")
        total = 0
        for entry in filter(None, entries):
            self.check_deadline()
            try:
                meta, raw_path = entry.split(b"\t", 1)
                mode, kind, oid, raw_size = meta.split()
                path = safe_path(raw_path.decode("utf-8"))
                if mode not in {b"100644", b"100755"} or kind != b"blob":
                    raise CodingBoundaryError("링크·서브모듈·특수 파일 저장소는 지원하지 않습니다.")
                size = int(raw_size)
                total += size
                if size > MAX_FILE_BYTES or total > MAX_REPOSITORY_BYTES:
                    raise CodingBoundaryError("소형 저장소 용량 제한을 초과했습니다.")
                raw = git(self.repository, "cat-file", "blob", oid.decode("ascii"))
                if b"\0" in raw:
                    raise CodingBoundaryError("텍스트 저장소만 지원합니다.")
                raw.decode("utf-8")
                self.baseline[path] = raw
                self.modes[path] = mode.decode("ascii")
            except (ValueError, UnicodeError):
                raise CodingBoundaryError("저장소 파일 목록 또는 텍스트 형식을 확인하세요.") from None
        self.check_deadline()
        # --no-checkout prevents post-checkout hooks AND attribute smudge/process filters.
        git(self.repository, "worktree", "add", "--no-checkout", "--detach",
            str(self.path), self.spec.base_commit)
        self.status = "preparing"
        try:
            self.path.chmod(0o700)
            self.git_marker = read_text(self.path / ".git")
            for name, raw in self.baseline.items():
                self.check_deadline()
                target = self.path / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(0o755 if self.modes[name] == "100755" else 0o644)
            git(self.path, "read-tree", self.spec.base_commit)
            self.check_deadline()
            self.status = "ready"
        except (OSError, CodingBoundaryError):
            self.status = "failed"
            raise CodingBoundaryError("작업공간 준비 실패. 원본은 변경하지 않았습니다.") from None
        return self.event()

    def target(self, name):
        safe_path(name)
        target = self.path / name
        # The private worktree must not have been replaced or shared with a runner.
        if self.path.resolve() != self.path or not self.path.is_dir():
            raise CodingBoundaryError("작업공간이 변경되었습니다.")
        if target.resolve() != target:
            raise CodingBoundaryError("링크를 통해 파일에 접근할 수 없습니다.")
        for parent in target.parents:
            if parent == self.path:
                break
            if parent.exists() and not parent.is_dir():
                raise CodingBoundaryError("파일의 상위 경로가 디렉터리가 아닙니다.")
        return target

    def verify_tree(self):
        """Reject unexpected writes before approval/application/artifact production."""
        if self.path.resolve() != self.path or not self.path.is_dir():
            raise CodingBoundaryError("작업공간이 변경되었습니다.")
        if self.git_marker is None or read_text(self.path / ".git") != self.git_marker:
            raise CodingBoundaryError("작업공간 Git 등록 정보가 변경되었습니다.")
        names = set(self.baseline)
        for path in self.path.rglob("*"):
            relative = path.relative_to(self.path).as_posix()
            if relative == ".git":
                continue
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise CodingBoundaryError("작업공간에 허용되지 않은 파일이 있습니다.")
            if path.is_file():
                names.add(relative)
        for name in names:
            self.check_deadline()
            raw = read_text(self.target(name))
            if raw != self.baseline.get(name):
                raise CodingBoundaryError("작업공간이 예상 상태와 다릅니다.")

    @exclusive
    def apply(self, changes: tuple[FileChange, ...], approved_change_sha256):
        if self.status != "ready" or self.applied:
            raise CodingBoundaryError("새 작업공간에서만 한 번 적용할 수 있습니다.")
        self.check_deadline()
        if not hmac.compare_digest(change_digest(self.spec, changes), approved_change_sha256):
            raise CodingBoundaryError("현재 변경 내용에 대한 승인이 필요합니다.")
        names = [c.path for c in changes]
        if (not changes or len(names) != len(set(names))
                or len(names) > self.spec.budget.max_files
                or not set(names).issubset(self.spec.allowed_files)):
            raise CodingBoundaryError("승인된 파일 범위 또는 수정 수 한도를 벗어났습니다.")
        self.verify_tree()
        if any(a != b and b.startswith(a + "/") for a in names for b in names):
            raise CodingBoundaryError("파일과 디렉터리 변경 범위가 충돌합니다.")
        replacements = {}
        patches = []
        changed_bytes = 0
        for change in changes:
            self.target(change.path)
            before = self.baseline.get(change.path)
            actual_hash = hashlib.sha256(before).hexdigest() if before is not None else None
            after = change.content.encode("utf-8") if change.content is not None else None
            if actual_hash != change.expected_sha256 or before == after:
                raise CodingBoundaryError("파일 기준 해시가 다르거나 실질 변경이 없습니다.")
            if after is not None and (len(after) > MAX_FILE_BYTES or b"\0" in after):
                raise CodingBoundaryError("텍스트 변경 크기 또는 형식 제한을 초과했습니다.")
            changed_bytes += len(before or b"") + len(after or b"")
            replacements[change.path] = after
            patches.append(f"diff --git a/{change.path} b/{change.path}\n")
            if before is None:
                patches.append("new file mode 100644\n")
            elif after is None:
                patches.append(f"deleted file mode {self.modes[change.path]}\n")
            lines = difflib.unified_diff(
                (before or b"").decode().splitlines(keepends=True),
                (after or b"").decode().splitlines(keepends=True),
                fromfile="a/" + change.path if before is not None else "/dev/null",
                tofile="b/" + change.path if after is not None else "/dev/null",
            )
            # Preserve valid unified diff records for files lacking a final newline.
            for line in lines:
                patches.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        patch = "".join(patches)
        if max(changed_bytes, len(patch.encode())) > self.spec.budget.max_patch_bytes:
            raise CodingBoundaryError("수정량 예산을 초과했습니다.")
        self.check_deadline()
        try:
            for name, raw in replacements.items():
                target = self.target(name)
                if raw is None:
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # O_NOFOLLOW and prior tree verification protect this private workspace.
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
            self.applied = True
            self.status = "changed"
        except (OSError, CodingBoundaryError):
            # Preserve failed artifacts for inspection. Never report partial work as success.
            self.status = "failed"
            raise CodingBoundaryError("파일 적용 실패. 격리 결과를 보존하고 작업을 차단했습니다.") from None
        return {
            **self.event(), "changed_files": names, "diff": patch,
            "change_sha256": change_digest(self.spec, changes),
            "notice": "격리 파일 수정 결과입니다. 테스트·모델 추론·push·PR 생성은 수행하지 않았습니다.",
        }

    @exclusive
    def close(self):
        # Explicit lifecycle only, limited to this registered private worktree.
        if self.status not in {"ready", "changed", "failed", "preparing"}:
            raise CodingBoundaryError("정리할 등록 작업공간이 없습니다.")
        if self.path.resolve() != self.path:
            raise CodingBoundaryError("교체된 작업 경로는 정리하지 않습니다.")
        if self.git_marker is None or read_text(self.path / ".git") != self.git_marker:
            raise CodingBoundaryError("변경된 Git 등록 정보로는 정리하지 않습니다.")
        git(self.repository, "worktree", "remove", "--force", str(self.path))
        self.status = "closed"
