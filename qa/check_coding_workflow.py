"""No model/server calls: scripted decisions plus real fixture unittest execution."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from role_ai.coding_agent import CodingAgent, runtime_identity
from role_ai.coding_jobs import CodingJobStore, TaskBudget, TaskCreate, checksum
from role_ai.config import Settings
from bench_coding_models import HostRunner, git_repo


def measure(root, actions, automatic):
    repo = root / "source"
    head = git_repo(
        repo,
        {
            "calc.py": "answer = 0\n",
            "test_calc.py": "import unittest\nfrom calc import answer\nclass Answer(unittest.TestCase):\n def test_answer(self): self.assertEqual(answer,42)\n",
        },
    )
    settings = Settings(
        _env_file=None,
        coding_enabled=True,
        coding_root=str(root / "jobs"),
        coding_repositories={"fixture": str(repo)},
        storage_dir=str(root / "storage"),
        database_url=f"sqlite:///{root / 'auth.db'}",
    )
    model_options = {}
    from role_ai.coding_jobs import ModelOptions

    supported = "automatic_tests" in ModelOptions.model_fields
    if supported:
        model_options["automatic_tests"] = automatic
    body = TaskCreate(
        repository_id="fixture",
        project_id="bench",
        request="Set answer to 42 and pass tests.",
        allowed_files=("calc.py",),
        approved=True,
        model=model_options,
        budget=TaskBudget(),
    )
    store = CodingJobStore(settings.coding_root)
    state = store.create(
        "owner", body, head, checksum(str(repo)), runtime_hash=runtime_identity(settings, "fixture")
    )

    class Scripted:
        def __init__(self):
            self.actions = iter(actions)

        def complete(self, state, messages):
            return json.dumps(next(self.actions))

    class Runner(HostRunner):
        runs = 0

        def run(self, *args):
            self.runs += 1
            return super().run(*args)

    runner = Runner()
    result = CodingAgent(settings, model=Scripted(), runner=runner).run("owner", state["id"])
    assert result["status"] == "completed" and result["last_test"]["status"] == "passed"
    assert result["tested_revision"] == result["revision"]
    assert (repo / "calc.py").read_bytes() == b"answer = 0\n"
    return {
        "completed": True,
        "actual_fixture_tests": result["last_test"]["count"],
        "model_requests": result["calls"],
        "tool_steps": result["steps"],
        "test_runner_executions": runner.runs,
        "reserved_tokens_not_measured_usage": result["tokens"],
        "real_model_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    from role_ai.coding_jobs import ModelOptions

    automatic = "automatic_tests" in ModelOptions.model_fields
    plan = {"action": "plan", "plan": ["Fix and test"]}
    patch = {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 42\n"}]}
    test = {"action": "test"}
    finish = {"action": "finish", "summary": "fixed"}
    with tempfile.TemporaryDirectory(prefix="coding-workflow-cpu-") as tmp:
        root = Path(tmp)
        (root / "normal").mkdir()
        (root / "duplicate").mkdir()
        normal = measure(
            root / "normal", [plan, patch, *([] if automatic else [test]), finish], automatic
        )
        repeated = measure(root / "duplicate", [plan, patch, test, test, finish], False)
    import role_ai.coding_agent as module

    data = {
        "scope": "Scripted decisions and real local unittest; no inference or model server.",
        "source_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        "automatic_supported": automatic,
        "normal": normal,
        "duplicate_test_request": repeated,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
