"""Actual Docker E2E with a scripted model and synthetic repository; no pretrained model."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from role_ai.coding_agent import CodingAgent, runtime_identity
from role_ai.coding_jobs import CodingJobStore, TaskBudget, TaskCreate, checksum, decode_files
from role_ai.config import Settings


class ScriptedModel:
    def __init__(self):
        self.actions = iter([
            {"action": "plan", "plan": ["Patch, run tests, inspect failure, repair and retest"]},
            {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 41\n"}]},
            {"action": "test"},
            {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 42\n"}]},
            {"action": "test"}, {"action": "finish", "summary": "Fixed using actual Docker tests"},
        ])

    def complete(self, state, messages):
        return json.dumps(next(self.actions))


def main():
    image = os.environ["ROLE_AI_CODE_EXECUTION_IMAGE"]
    with tempfile.TemporaryDirectory(prefix="role-ai-runtime-ci-") as temporary:
        root = Path(temporary)
        repo = root / "source"
        repo.mkdir()
        (repo / "calc.py").write_text("answer = 0\n")
        (repo / "test_calc.py").write_text(
            "import unittest\nfrom calc import answer\nclass Answer(unittest.TestCase):\n"
            " def test_answer(self): self.assertEqual(answer, 42)\n")
        (repo / "sample.xlsx").write_bytes(b"PK\x00\xffpreserved")
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=CI", "-c", "user.email=ci@example.test", "commit", "-qm", "fixture")
        settings = Settings(_env_file=None, coding_enabled=True, coding_root=str(root / "jobs"),
                            coding_repositories={"fixture": str(repo)}, coding_image=image,
                            storage_dir=str(root / "artifacts"))
        store = CodingJobStore(settings.coding_root)
        body = TaskCreate(repository_id="fixture", project_id="ci", request="Fix answer", allowed_files=("calc.py",),
                          approved=True, record_strategy=True, budget=TaskBudget(token_budget=131072))
        state = store.create("owner", body, git("rev-parse", "HEAD"), checksum(str(repo)),
                             runtime_hash=runtime_identity(settings, "fixture"))
        model = ScriptedModel()
        paused = CodingAgent(settings, model=model).run("owner", state["id"], max_actions=3)
        assert paused["status"] == "paused", paused.get("error")
        assert paused["last_test"]["status"] == "failed"
        before = paused["tokens"]
        store.resume("owner", state["id"])
        result = CodingAgent(settings, model=model).run("owner", state["id"])
        assert result["status"] == "completed", result.get("error")
        assert result["tokens"] > before and result["last_test"]["count"] == 1
        assert decode_files(result["files"])["calc.py"] == b"answer = 42\n"
        assert decode_files(result["files"])["sample.xlsx"] == b"PK\x00\xffpreserved"
        assert (repo / "calc.py").read_text() == "answer = 0\n"
        assert git("status", "--porcelain") == ""
        print("Actual Docker coding loop: failed test -> checkpoint/pause -> restart/resume -> repaired patch -> passing test")


if __name__ == "__main__":
    main()
