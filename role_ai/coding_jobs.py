"""Private atomic checkpoints. No application DB schema changes or automatic execution."""

import base64
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from role_ai.coding_tasks import Contract, editable_path, safe_path
from role_ai.coding_workspace import private_directory

MAX_CHECKPOINT = 8 * 1024 * 1024


class CodingJobError(ValueError):
    pass


class JobBusy(CodingJobError):
    pass


class TaskBudget(Contract):
    max_steps: int = Field(default=24, ge=2, le=128)
    max_retries: int = Field(default=3, ge=0, le=10)
    same_error_limit: int = Field(default=2, ge=1, le=5)
    active_seconds: int = Field(default=1800, ge=10, le=14400)
    deadline_seconds: int = Field(default=86400, ge=60, le=604800)
    command_seconds: int = Field(default=30, ge=1, le=300)
    model_calls: int = Field(default=24, ge=1, le=128)
    token_budget: int = Field(default=65536, ge=4096, le=524288)
    output_tokens: int = Field(default=1024, ge=128, le=2048)
    max_files: int = Field(default=8, ge=1, le=16)
    patch_bytes: int = Field(default=65536, ge=1, le=262144)
    memory_mib: int = Field(default=256, ge=128, le=2048)
    cpu_millicores: int = Field(default=500, ge=100, le=2000)


class TaskCreate(Contract):
    repository_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    project_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    request: str = Field(min_length=1, max_length=4000)
    allowed_files: tuple[str, ...] = Field(min_length=1, max_length=16)
    command: str = Field(default="unittest", pattern=r"^(unittest|pytest)$")
    budget: TaskBudget = Field(default_factory=TaskBudget)
    approved: bool = False
    record_strategy: bool = False

    @field_validator("allowed_files")
    @classmethod
    def allowed(cls, paths):
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate paths")
        for path in paths:
            editable_path(path)
            if (Path(path).suffix not in {".py", ".js", ".html", ".css", ".md", ".json"}
                    or any(part.lower() in {"tests", "test"} for part in Path(path).parts[:-1])
                    or Path(path).name.startswith("test") or Path(path).name.endswith("_test.py")
                    or Path(path).name in {"conftest.py", "runner.py", "_roleai_runner.py"}):
                raise ValueError("Tests and runtime files are read-only in this MVP")
        return paths


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def encode_files(files):
    return {name: base64.b64encode(raw).decode("ascii") for name, raw in files.items()}


def decode_files(files):
    if type(files) is not dict or len(files) > 256:
        raise CodingJobError("Invalid source snapshot")
    decoded = {}
    for name, data in files.items():
        safe_path(name)
        decoded[name] = base64.b64decode(data, validate=True)
        if len(decoded[name]) > 1048576:
            raise CodingJobError("Source file exceeds limit")
    if sum(map(len, decoded.values())) > 2 * 1024 * 1024:
        raise CodingJobError("Source snapshot exceeds limit")
    return decoded


class CodingJobStore:
    def __init__(self, root):
        self.root = private_directory(root)

    def directory(self, uid, jid):
        if UUID(jid).hex != jid:
            raise CodingJobError("Invalid task ID")
        directory = self.root / hashlib.sha256(uid.encode()).hexdigest() / jid
        if directory.resolve() != directory:
            raise CodingJobError("Invalid task path")
        return directory

    @contextmanager
    def lock(self, directory, name="task"):
        path = directory / (name + ".lock")
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a+b") as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise JobBusy("Task is busy") from None
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)
        except OSError:
            raise CodingJobError("Cannot lock coding task") from None

    def atomic(self, path, payload):
        if path.resolve() != path:
            raise CodingJobError("Invalid checkpoint path")
        data = json.dumps({"format": 1, "checksum": checksum(payload), "payload": payload},
                          ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) > MAX_CHECKPOINT:
            raise CodingJobError("Checkpoint capacity exceeded")
        temporary = path.parent / (".checkpoint-" + uuid4().hex)
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                import stat
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Not a regular checkpoint")
                raw = stream.read(MAX_CHECKPOINT + 1)
            if len(raw) > MAX_CHECKPOINT:
                raise ValueError("Large checkpoint")
            item = json.loads(raw)
            if item["format"] != 1 or checksum(item["payload"]) != item["checksum"]:
                raise ValueError("Corrupt checkpoint")
            return item["payload"]
        except (OSError, ValueError, KeyError, TypeError):
            raise CodingJobError("Checkpoint missing or corrupt; no action was replayed") from None

    def create(self, uid, body: TaskCreate, revision, source_hash, *, runtime_hash=None):
        if body.approved is not True:
            raise CodingJobError("Explicit task approval is required")
        # Operator root is private and never supplied by an HTTP/model request.
        namespace = private_directory(self.root / hashlib.sha256(uid.encode()).hexdigest())
        with self.lock(self.root, "create"):
            if len(list(namespace.glob("*/checkpoint.json"))) >= 16:
                raise CodingJobError("Task quota reached; archive old tasks before creating more")
            jid = uuid4().hex
            directory = private_directory(namespace / jid)
            state = {
                "version": 1, "id": jid, "user_id": uid, "created_at": time.time(),
                "updated_at": time.time(), "status": "queued", "phase": "prepare",
                "spec": body.model_dump(mode="json"), "repo_revision": revision,
                "source_hash": source_hash, "runtime_hash": runtime_hash,
                "steps": 0, "calls": 0, "tokens": 0,
                "active_seconds": 0.0, "retries": 0, "errors": {}, "cycles": [],
                "plan": [], "observations": [], "history": [], "pending": None,
                "original": None, "files": None, "revision": None, "tested_revision": None,
                "last_test": None, "recorder_gap": False, "event_sequence": 0,
            }
            self.save(directory, state)
            return state

    def load(self, uid, jid):
        directory = self.directory(uid, jid)
        state = self.read(directory / "checkpoint.json")
        if state.get("id") != jid or state.get("user_id") != uid:
            raise CodingJobError("Task owner mismatch")
        TaskCreate.model_validate_json(json.dumps(state["spec"]))
        for field in ("files", "original"):
            if state.get(field) is not None:
                decode_files(state[field])
        return directory, state

    def save(self, directory, state):
        state["updated_at"] = time.time()
        state["history"] = state["history"][-128:]
        state["observations"] = state["observations"][-8:]
        # Bounded task count + bounded checkpoint size; no source/log dump in telemetry.
        self.atomic(directory / "checkpoint.json", state)

    def control(self, directory):
        path = directory / "control.json"
        return self.read(path).get("action") if path.exists() else None

    def request_control(self, uid, jid, action):
        directory, _ = self.load(uid, jid)
        if action not in {"pause", "cancel"}:
            raise CodingJobError("Invalid task control")
        with self.lock(directory, "control"):
            _, latest = self.load(uid, jid)
            if latest["status"] in {"completed", "failed", "cancelled", "budget_exhausted"}:
                raise CodingJobError("Task is already terminal")
            # Cancellation is terminal and cannot be accidentally replaced by pause.
            if self.control(directory) == "cancel":
                action = "cancel"
            self.atomic(directory / "control.json", {"action": action})

    def resume(self, uid, jid, *, acknowledge_unknown=False):
        directory, _ = self.load(uid, jid)
        with self.lock(directory):
            _, state = self.load(uid, jid)
            if state["status"] not in {"paused", "interrupted", "running", "waiting"}:
                raise CodingJobError("This task cannot resume")
            if self.control(directory) == "cancel":
                raise CodingJobError("Cancellation is terminal; create a new approved task")
            if state["pending"]:
                if not acknowledge_unknown:
                    raise CodingJobError("Unknown action outcome requires explicit acknowledgement")
                # Model slots/containers are reconciled by the worker before any replay.
                state["reconcile"] = state["pending"]
                state["pending"] = None
            state["status"] = "queued"
            with self.lock(directory, "control"):
                if self.control(directory) == "cancel":
                    raise CodingJobError("Cancellation is terminal")
                (directory / "control.json").unlink(missing_ok=True)
                self.save(directory, state)
            return state

    def jobs(self, uid):
        namespace = self.root / hashlib.sha256(uid.encode()).hexdigest()
        for path in sorted(namespace.glob("*/checkpoint.json")):
            yield self.load(uid, path.parent.name)[1]
