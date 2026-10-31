"""Real Docker smoke checks. Explicit CI/admin invocation only; never auto-pulls images."""
import os
import subprocess
import tempfile
from pathlib import Path

from role_ai.code_execution import CodeExecutor, ExecutionRequest
from role_ai.config import Settings

settings = Settings(_env_file=None, code_execution_enabled=True,
                    code_execution_image=os.environ["ROLE_AI_CODE_EXECUTION_IMAGE"],
                    code_execution_timeout_seconds=3)
executor = CodeExecutor(settings)


def run(code, tests="import unittest\nclass Smoke(unittest.TestCase):\n def test_run(self):\n  import submission\n"):
    return executor.execute(ExecutionRequest(code=code, tests=tests, approved=True))


# This is a dedicated QA process. Exercise the real non-root container under
# the restrictive policy that previously prevented it from opening runner.py.
original_umask = os.umask(0o077)
try:
    restrictive = run("pass")
    assert restrictive["status"] == "completed" and restrictive["exit_code"] == 0, restrictive
finally:
    os.umask(original_umask)
print("PASS: restrictive umask077 real non-root Docker execution")


with tempfile.TemporaryDirectory(prefix="role-ai-private-") as private:
    sentinel = Path(private) / "secret.txt"
    sentinel.write_text("host-secret-not-for-container")
    os.environ["ROLE_AI_TEST_SECRET"] = "host-secret-not-for-container"
    result = run(f'''import os, socket
from pathlib import Path
assert "ROLE_AI_TEST_SECRET" not in os.environ
assert not Path({str(sentinel)!r}).exists()
assert not Path("/var/run/docker.sock").exists()
assert not Path("/app/data").exists()
assert os.getuid() == 65534
try:
    Path("/work/escape.txt").write_text("write")
except OSError:
    pass
else:
    raise AssertionError("work must be read-only")
s = socket.socket()
s.settimeout(.5)
assert s.connect_ex(("1.1.1.1", 53)) != 0
s.close()
''')
    assert result["status"] == "completed" and result["exit_code"] == 0, result

failure = run("raise AssertionError('expected-failure')")
assert failure["status"] == "completed" and failure["exit_code"] != 0, failure
assert "expected-failure" in failure["output"]
empty = run("pass", "# no tests")
assert empty["exit_code"] == 5, empty
syntax = run("def broken(:")
assert syntax["exit_code"] != 0 and "SyntaxError" in syntax["output"], syntax
timeout = run("import time; time.sleep(60)")
assert timeout["status"] == "timeout", timeout
output = run("while True: print('x' * 4096)")
assert output["status"] == "output_limit" and output["output_truncated"], output
assert len(output["output"].encode()) <= 32768
memory = run("x = bytearray(300 * 1024 * 1024)")
assert memory["status"] == "memory_limit" or memory["exit_code"] != 0, memory
left = subprocess.run(["docker", "ps", "-aq", "--filter", "label=role-ai.code-execution=true"],
                      check=True, capture_output=True, text=True)
assert not left.stdout.strip(), "Runner containers were not removed"
print("PASS: real Docker success/failure, no tests, syntax, timeout, output, memory, isolation, cleanup")
