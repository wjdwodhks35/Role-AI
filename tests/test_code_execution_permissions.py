"""Regression for restrictive process umasks without changing pytest's process policy."""

import os
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(os.name != "posix", reason="The Docker runner requires POSIX")
@pytest.mark.parametrize("mask", [0o022, 0o077])
def test_runner_mount_readable_without_exposing_private_parent(mask):
    fixture = textwrap.dedent(
        """
        import json
        import os
        from pathlib import Path
        import stat
        from unittest.mock import patch

        from role_ai.code_execution import CodeExecutor, ExecutionRequest
        from role_ai.config import Settings

        executor = CodeExecutor(Settings(
            _env_file=None, code_execution_enabled=True,
            code_execution_image="python:3.12-slim@sha256:" + "a" * 64,
        ))
        mounts = []
        calls = []

        def command(base, env, *args, **kwargs):
            calls.append(args[0])
            if args[0] == "create":
                mount = args[args.index("--mount") + 1]
                assert mount.endswith(",dst=/work,readonly")
                work = Path(mount.split("src=")[1].split(",")[0])
                mounts.append(work)
                assert stat.S_IMODE(work.stat().st_mode) == 0o755
                assert stat.S_IMODE(work.parent.stat().st_mode) == 0o700
                assert {p.name for p in work.iterdir()} == {
                    "submission.py", "test_submission.py", "runner.py"
                }
                for path in work.iterdir():
                    assert stat.S_IMODE(path.stat().st_mode) == 0o444
                assert "--user=65534:65534" in args
                assert "--network=none" in args and "--read-only" in args
                assert "--cap-drop=ALL" in args
                assert "--security-opt=no-new-privileges:true" in args
            if args[0] == "inspect":
                return json.dumps({"ExitCode": 0, "OOMKilled": False,
                                   "Running": False, "Error": ""})
            return ""

        with patch("role_ai.code_execution.shutil.which", return_value="/usr/bin/docker"), \
             patch.object(executor, "preflight"), \
             patch.object(executor, "command", side_effect=command), \
             patch.object(executor, "capture", return_value=(None, "trusted fixture")):
            result = executor.execute(ExecutionRequest(
                code="SYNTHETIC_ONLY", tests="SYNTHETIC_ONLY", approved=True,
            ))
        assert result["status"] == "completed" and result["exit_code"] == 0
        assert calls[-1] == "rm" and mounts
        assert all(not path.exists() for path in mounts)
        current_mask = os.umask(0o077)
        os.umask(current_mask)
        print(current_mask)
        """
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "PYTHONPATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    }
    result = subprocess.run(
        [sys.executable, "-c", fixture],
        capture_output=True,
        text=True,
        env=environment,
        umask=mask,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(mask)
