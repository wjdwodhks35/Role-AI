"""Pinned, offline Docker test templates. No shell, downloads, GPU or host fallback."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from role_ai.code_execution import CodeExecutor, ExecutionUnavailable
from role_ai.coding_jobs import CodingJobError, decode_files

PINNED_IMAGE = re.compile(r"[A-Za-z0-9_./:-]+@sha256:[0-9a-f]{64}\Z")
MARKER = "__ROLEAI_TEST_RESULT__="
RUNNER = '''import json, os, sys, unittest
mode = sys.argv[1]
if mode == "pytest":
    import pytest
sys.path.insert(0, "/work")
if mode == "unittest":
    start = "/work/tests" if os.path.isdir("/work/tests") else "/work"
    suite = unittest.defaultTestLoader.discover(start, pattern="test*.py", top_level_dir=start)
    count = suite.countTestCases()
    if not count:
        code = 5
    else:
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        count = result.testsRun - len(result.skipped) - len(result.expectedFailures)
        code = (0 if result.wasSuccessful() else 1) if count else 5
else:
    class Counts:
        count = 0
        def pytest_runtest_logreport(self, report):
            if report.when == "call" and not report.skipped:
                self.count += 1
    counter = Counts()
    code = int(pytest.main(["-q", "-p", "no:cacheprovider"], plugins=[counter]))
    count = counter.count
    if not count and code == 0:
        code = 5
print("__ROLEAI_TEST_RESULT__=" + json.dumps({"count":count,"code":code}), flush=True)
sys.exit(code)
'''


class RepositoryTestRunner:
    def __init__(self, settings):
        self.settings = settings

    def run(self, state, name, directory):
        settings = self.settings
        if not PINNED_IMAGE.fullmatch(settings.coding_image):
            raise CodingJobError("Configure an installed digest-pinned coding test image")
        socket = settings.code_execution_socket
        executable = shutil.which("docker")
        if (not executable or not socket.startswith("/") or any(c in socket for c in "\r\n\0")):
            raise CodingJobError("Local Docker is unavailable; host execution is forbidden")
        quarantine = Path(settings.coding_root).absolute() / "runner-cleanup-required"
        if quarantine.exists():
            raise CodingJobError("Runner cleanup requires operator inspection")
        budget = state["spec"]["budget"]
        helper = CodeExecutor(SimpleNamespace(
            code_execution_image=settings.coding_image,
            code_execution_timeout_seconds=state.get("command_timeout", budget["command_seconds"]),
        ))
        with tempfile.TemporaryDirectory(prefix="role-ai-coding-") as temporary:
            root = Path(temporary)
            root.chmod(0o711)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
                   "DOCKER_CONFIG": str(root / "docker-config")}
            base = [executable, "--host", "unix://" + socket]
            work = root / "work"
            work.mkdir(mode=0o755)
            files = decode_files(state["files"])
            if any(Path(p).name == "_roleai_runner.py" for p in files):
                raise CodingJobError("Reserved runner path in source")
            for path, raw in files.items():
                destination = work / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
                destination.chmod(0o444)
            (work / "_roleai_runner.py").write_text(RUNNER)
            (work / "_roleai_runner.py").chmod(0o444)
            # Inputs cannot contain .git, sockets, credentials or host volumes.
            helper.preflight(base, env)
            args = helper.create_args(name, work)
            args[args.index("--memory=128m")] = f"--memory={budget['memory_mib']}m"
            args[args.index("--memory-swap=128m")] = f"--memory-swap={budget['memory_mib']}m"
            args[args.index("--cpus=0.5")] = f"--cpus={budget['cpu_millicores'] / 1000}"
            args[-1] = "/work/_roleai_runner.py"
            args.append(state["spec"]["command"])
            # Do not label repository jobs as legacy snippet jobs.
            args[args.index("--label=role-ai.code-execution=true")] = "--label=role-ai.coding-runtime=true"
            try:
                helper.command(base, env, *args)
                reason, output = helper.capture(base, env, name)
                if reason:
                    try:
                        helper.command(base, env, "kill", name)
                    except ExecutionUnavailable:
                        pass
                actual = json.loads(helper.command(base, env, "inspect", "--format", "{{json .State}}", name))
                if actual.get("Running") or actual.get("Error"):
                    raise CodingJobError("Test termination could not be verified")
                if actual.get("OOMKilled"):
                    reason = "memory_limit"
                reports = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
                report = json.loads(reports[-1]) if reports else {}
                count = report.get("count", 0)
                valid = type(count) is int and count > 0 and report.get("code") == actual.get("ExitCode")
                passed = not reason and valid and actual["ExitCode"] == 0
                return {"status": "passed" if passed else "failed", "reason": reason or (
                    "tests" if valid else "missing_test_evidence"), "exit_code": actual["ExitCode"],
                    "count": count if type(count) is int else 0, "output": output,
                    "command": state["spec"]["command"], "image": settings.coding_image,
                    "container": name, "revision": state["revision"]}
            finally:
                try:
                    helper.command(base, env, "rm", "--force", "--volumes", name)
                except ExecutionUnavailable:
                    quarantine.write_text("Inspect labelled coding containers before clearing this guard.\n")
                    quarantine.chmod(0o600)
                    raise CodingJobError("Runner cleanup failed; further commands blocked") from None

    def reconcile(self, pending):
        # Do not guess whether a crashed command completed. Explicit acknowledgement
        # permits killing/removing only the recorded container on the configured daemon.
        name = pending.get("container")
        if not name:
            return
        if not re.fullmatch(r"role-ai-coding-[0-9a-f]{32}", name):
            raise CodingJobError("Invalid interrupted container reference")
        executable = shutil.which("docker")
        if not executable:
            raise CodingJobError("Cannot reconcile interrupted container")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent"}
        base = [executable, "--host", "unix://" + self.settings.code_execution_socket]
        # List first: a missing container is already cleaned; an inaccessible daemon is an error.
        found = CodeExecutor.command(base, env, "ps", "-aq", "--filter", "name=^/" + name + "$")
        if found.strip():
            label = CodeExecutor.command(base, env, "inspect", "--format", '{{index .Config.Labels "role-ai.coding-runtime"}}', name)
            if label.strip() != "true":
                raise CodingJobError("Interrupted container identity mismatch")
            CodeExecutor.command(base, env, "rm", "--force", "--volumes", name)
