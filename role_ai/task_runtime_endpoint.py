"""Trusted benchmark CLI endpoint selection; never a model-selectable tool.

Explicit endpoints must be canonical, private, user-owned local Unix sockets.
No global Docker context, remote daemon, chmod, sudo, install or service changes.
"""

import os
from pathlib import Path
import shutil
import stat

from role_ai.code_execution import CodeExecutor, ExecutionUnavailable


def socket_identity(value):
    if (
        type(value) is not str
        or not value.startswith("/")
        or any(c in value for c in "\r\n\0")
        or len(os.fsencode(value)) > 107
    ):
        raise ValueError("Explicit local Unix socket required")
    path = Path(value)
    if str(path) != value or ".." in path.parts or path.resolve() != path:
        raise ValueError("Canonical socket path required; links are denied")
    try:
        info = path.lstat()
        parent = path.parent.lstat()
        uid = os.getuid()
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) & 0o007
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != uid
            or stat.S_IMODE(parent.st_mode) & 0o077
        ):
            raise ValueError()
        for ancestor in path.parents:
            entry = ancestor.lstat()
            if stat.S_ISLNK(entry.st_mode):
                raise ValueError()
            if stat.S_IMODE(entry.st_mode) & 0o022 and not (
                entry.st_uid == 0 and entry.st_mode & stat.S_ISVTX
            ):
                raise ValueError()
    except (OSError, ValueError):
        raise ValueError("Private user-owned socket and safe parent directories required") from None
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "parent_device": parent.st_dev,
        "parent_inode": parent.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
    }


def bind_endpoint(settings, value):
    """Only a trusted CLI/operator supplies this value, before approval issuance."""
    identity = socket_identity(value)
    settings.code_execution_socket = value
    settings._benchmark_socket_identity = identity


def endpoint_binding(settings):
    expected = getattr(settings, "_benchmark_socket_identity", None)
    if expected is None:
        return None  # Preserve the existing server/default endpoint configuration.
    current = socket_identity(settings.code_execution_socket)
    if current != expected:
        raise ValueError("Benchmark endpoint changed; new verification and approval required")
    return current


def verify_runtime(settings, root):
    """Reuse the existing read-only Docker preflight before any inference.

    Explicit user sockets additionally require rootless/cgroup-v2/systemd.
    These metadata checks do not replace actual sandbox/limit verification.
    """
    import json

    binding = endpoint_binding(settings)
    executable = shutil.which("docker")
    executor = CodeExecutor(settings)
    if not executable or not executor.configuration_valid():
        raise ValueError("Existing bounded Docker runtime unavailable")
    base = [executable, "--host", "unix://" + settings.code_execution_socket]
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "DOCKER_CONFIG": str(root)}
    try:
        if binding is not None:
            info = json.loads(executor.command(base, env, "info", "--format", "{{json .}}"))
            if type(info) is not dict:
                raise ValueError("Invalid Docker metadata")
            if (
                info.get("CgroupVersion") != "2"
                or info.get("CgroupDriver") != "systemd"
                or not any(x == "name=rootless" for x in info.get("SecurityOptions", []))
            ):
                raise ValueError("Explicit benchmark endpoint needs verified rootless isolation")
        executor.preflight(base, env)
        endpoint_binding(settings)
    except (ExecutionUnavailable, ValueError, TypeError):
        raise ValueError(
            "Existing image/endpoint/resource controls unavailable; no inference started"
        ) from None
