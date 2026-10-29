#!/usr/bin/env python3
"""Conservative, offline command log reduction; no shell or model/API calls."""
from __future__ import annotations

import argparse
import json
import locale
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

PROJECT = Path(__file__).resolve().parents[2]
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
DIAGNOSTIC = re.compile(
    r"traceback|assert|error|exception|fail|warn|fatal|panic|caused by|경고|오류|예외|실패|\b[EWF]\d{3,4}\b", re.I
)
# Deliberately narrow: unknown lines (including source, diffs, JSON) are retained.
SUCCESS = re.compile(
    r"^(?:test_[\w.]+ .+ \.\.\. ok|[.s]+|"
    r"(?:tests/|tests\\)[\w./\\-]+\s+[.s]+\s*(?:\[\s*\d+%\])?|"
    r"(?:Downloading|Progress:) .*\d+%.*)$"
)
SECRETS = [
    (re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|\Z)"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)\b[\"']?\s*[:=]\s*)([\"'])(.*?)\2"), r"\1[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED_JWT]"),
    (re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1[REDACTED]@"),
    (re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)\b[\"']?\s*[:=]\s*)([\"']?)[^\s,;\"']+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
]


def redact(text: str) -> str:
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def summarize(text: str, minimum: int = 6000) -> str:
    """Remove known success noise only; never collapse diagnostic/unknown lines."""
    clean = ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    safe = redact(clean)
    if len(clean) <= minimum:
        return safe
    try:
        json.loads(clean)
        return safe  # Complete structured output is kept.
    except (ValueError, TypeError):
        pass
    lines = safe.splitlines(keepends=True)
    # NDJSON, diffs and source-like output are not summarized.
    nonempty = [line.strip() for line in lines if line.strip()]
    if (nonempty and all(line.startswith(("{", "[")) for line in nonempty)) or any(
        line.startswith(("diff --git", "@@ ", "+++ ", "--- ", "def ", "class ", "import ", "from "))
        for line in lines
    ):
        return safe
    output = []
    count = 0
    traceback = False
    for line in lines:
        if "Traceback (most recent call last):" in line:
            traceback = True
        if not traceback and not DIAGNOSTIC.search(line) and SUCCESS.fullmatch(line.strip()):
            count += 1
        else:
            if count:
                output.append(f"[omitted {count} routine success/progress lines]\n")
                count = 0
            output.append(line)
    if count:
        output.append(f"[omitted {count} routine success/progress lines]\n")
    candidate = "".join(output)
    return candidate if len(candidate) < len(safe) else safe


def secure_directory(path: Path) -> None:
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (path, *path.parents)):
        raise OSError("Refusing a linked log directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path / ".role-ai-log-setup"
    deadline = time.monotonic() + 10
    while True:
        try:
            lock.mkdir(mode=0o700)
            break
        except FileExistsError:
            if lock.is_symlink() or time.monotonic() >= deadline:
                raise OSError("Log-directory initialization is locked")
            time.sleep(0.02)
    try:
        marker = path / ".role-ai-command-logs"
        signature = b"role-ai-command-logs-v1\n"
        if marker.is_symlink() or (marker.exists() and marker.read_bytes() != signature):
            raise OSError("Invalid log ownership marker")
        if not marker.exists() and any(p != lock for p in path.iterdir()):
            raise OSError("Refusing to change permissions of an unowned nonempty directory")
        try:
            private_write(marker, signature)
        except FileExistsError:
            if marker.read_bytes() != signature:
                raise OSError("Invalid concurrent log ownership marker")
        if os.name == "nt":
            # Read identities only; icacls modifies the DACL without SACL privileges.
            quoted_path = str(path).replace("'", "''")
            # Use framework ACL APIs directly: inherited PSModulePath can select
            # PowerShell 7 modules incompatible with Windows PowerShell 5.1.
            script = (
                "$ErrorActionPreference='Stop'; "
                f"$acl=[System.IO.Directory]::GetAccessControl('{quoted_path}'); "
                "[Console]::Out.WriteLine([System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value); "
                "foreach($rule in $acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) "
                "{ [Console]::Out.WriteLine($rule.IdentityReference.Value) }"
            )
            query = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, check=True,
            )
            sids = query.stdout.decode("ascii").splitlines()
            if not sids or any(not re.fullmatch(r"S-\d+(?:-\d+)+", sid) for sid in sids):
                raise OSError("Unexpected Windows ACL identity output")
            identities = {"current": sids[0], "identities": sids[1:]}
            current = identities["current"]
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{current}:(OI)(CI)F"],
                capture_output=True, check=True,
            )
            others = sorted(set(identities["identities"]) - {current})
            if others:
                subprocess.run(
                    ["icacls", str(path), "/remove", *["*" + sid for sid in others]],
                    capture_output=True, check=True,
                )
        else:
            os.chmod(path, 0o700)
    finally:
        lock.rmdir()


def private_write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def prune(root: Path, *, keep_days: float = 7, budget: int = 128 * 1024 * 1024) -> None:
    completed = []
    total = 0
    for run in root.glob("run-*"):
        if not run.is_dir() or run.is_symlink():
            continue
        try:
            size = sum(p.stat().st_size for p in run.iterdir() if p.is_file())
            total += size
            marker = run / "result.json"
            if marker.is_file():
                completed.append((marker.stat().st_mtime, run, size))
        except FileNotFoundError:
            continue  # A concurrent launcher may have pruned it.
    for modified, run, size in sorted(completed):
        if time.time() - modified <= keep_days * 86400 and total <= budget:
            continue
        shutil.rmtree(run, ignore_errors=True)
        total -= size
    if total > budget:
        raise OSError("Active log budget exceeded; no command was started")


class Capture:
    def __init__(self, pipe, path: Path, cap: int):
        self.pipe, self.path, self.cap = pipe, path, cap
        self.total = 0
        self.error = None
        self.thread = threading.Thread(target=self.read, daemon=True)

    def read(self):
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            # Full originals remain on disk. The cap limits preview reads, never storage.
            with os.fdopen(fd, "wb") as stream:
                while chunk := self.pipe.read(8192):
                    self.total += len(chunk)
                    stream.write(chunk)
        except Exception as error:
            self.error = error
            # Drain on storage failure so a child cannot deadlock on a full pipe.
            try:
                while self.pipe.read(8192):
                    pass
            except (OSError, ValueError):
                pass
        finally:
            self.pipe.close()

    def render(self):
        with self.path.open("rb") as stream:
            if self.total > self.cap:
                half = self.cap // 2
                head = stream.read(half)
                stream.seek(-half, os.SEEK_END)
                tail = stream.read(half)
                return (decode(head) + "\n[PREVIEW CAP: middle omitted; full original on disk]\n"
                        + decode(tail))
            return decode(stream.read())


def bounded_preview(text: str, limit: int) -> str:
    """Keep head/tail and diagnostic neighborhoods; originals remain authoritative."""
    if len(text) <= limit:
        return text
    lines = text.splitlines(keepends=True)
    selected = set()
    used = 0
    # Prioritize the first and last diagnostics plus their context, then head/tail.
    diagnostic = [i for i, line in enumerate(lines) if DIAGNOSTIC.search(line)]
    candidates = []
    for i in diagnostic[:8] + diagnostic[-8:]:
        candidates.extend(range(max(0, i - 3), min(len(lines), i + 4)))
    candidates.extend(range(min(8, len(lines))))
    candidates.extend(range(max(0, len(lines) - 12), len(lines)))
    for i in candidates:
        if i not in selected and used + min(len(lines[i]), 600) <= limit - 300:
            selected.add(i)
            used += min(len(lines[i]), 600)
    output = ["[DISPLAY CAP: omitted text; inspect private original before diagnosis]\n"]
    previous = -1
    for i in sorted(selected):
        if i != previous + 1:
            output.append("[...]\n")
        output.append(lines[i] if len(lines[i]) <= 600 else lines[i][:300] + "[...]" + lines[i][-295:])
        previous = i
    return "".join(output)[:limit]


def run(command, root: Path, cap: int, display_limit: int = 12000) -> int:
    secure_directory(root)
    prune(root)
    folder = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    # Inherits the private Windows root ACL; mkdtemp uses 0700 on POSIX.
    started = time.monotonic()
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        message = f"Command could not start: {type(error).__name__}\n"
        private_write(folder / "stdout.log", b"")
        private_write(folder / "stderr.log", message.encode())
        private_write(folder / "result.json", json.dumps({"exit_code": 127}).encode())
        print(message + f"Raw logs: {folder.relative_to(root.parent)}", file=sys.stderr)
        return 127
    captures = [Capture(child.stdout, folder / "stdout.log", cap),
                Capture(child.stderr, folder / "stderr.log", cap)]
    for capture in captures:
        capture.thread.start()
    try:
        code = child.wait()
    except KeyboardInterrupt:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        code = 130
    for capture in captures:
        capture.thread.join()
    exit_code = code if code >= 0 else 128 - code
    shown = 0
    storage_failed = False
    summary_failed = False
    changed = False
    for label, capture in zip(("stdout", "stderr"), captures):
        if capture.error:
            storage_failed = True
            print(f"WARNING: {label} capture failed ({type(capture.error).__name__})", file=sys.stderr)
            continue
        try:
            text = capture.render()
        except OSError as error:
            storage_failed = True
            print(f"WARNING: {label} log read failed ({type(error).__name__})", file=sys.stderr)
            continue
        try:
            reduced = bounded_preview(summarize(text), display_limit)
        except Exception as error:
            summary_failed = True
            print(f"WARNING: compression failed ({type(error).__name__}); original retained", file=sys.stderr)
            try:
                reduced = bounded_preview(redact(ANSI.sub("", text)), display_limit)
            except Exception:
                reduced = "WARNING: safe preview failed; public output withheld; inspect private original\n"
        changed |= reduced != text.replace("\r\n", "\n").replace("\r", "\n") or capture.total > cap
        shown += len(reduced)
        if reduced:
            print(reduced, end="", file=sys.stdout if label == "stdout" else sys.stderr)
    result = {
        "exit_code": exit_code, "duration_seconds": round(time.monotonic() - started, 3),
        "captured_bytes": {label: c.total for label, c in zip(("stdout", "stderr"), captures)},
        "preview_read_bytes_per_stream": cap,
        "truncated": False,
        "preview_truncated": any(c.total > cap for c in captures),
        "summary_failed": summary_failed,
        "storage_failed": storage_failed, "shown_characters": shown,
    }
    try:
        private_write(folder / "result.json", json.dumps(result, indent=2).encode())
    except OSError:
        storage_failed = True
        print("WARNING: result metadata could not be saved", file=sys.stderr)
    if changed or storage_failed or summary_failed:
        # Avoid disclosing the username in an absolute home path in terminal context.
        print(f"\n[exit={exit_code}; raw logs: {folder.relative_to(root.parent)}]", file=sys.stderr)
    if storage_failed:
        print("WARNING: incomplete logs; child exit code is preserved", file=sys.stderr)
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-root", type=Path, default=PROJECT / ".codex-local" / "logs")
    parser.add_argument("--preview-read-bytes", "--max-log-bytes", dest="max_log_bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--display-chars", type=int, default=12000)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not 1024 <= args.max_log_bytes <= 16 * 1024 * 1024 or not 1024 <= args.display_chars <= 100000:
        parser.error("command required; preview-read bytes 1024..16777216; display chars 1024..100000")
    try:
        return run(command, args.log_root.absolute(), args.max_log_bytes, args.display_chars)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Log wrapper failed: {type(error).__name__}; command result may be unavailable",
              file=sys.stderr)
        return 125


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
