"""Real-model benchmark of the CodingAgent loop (development machines only; calls local Ollama).

Default test runner is the production Docker runner (RepositoryTestRunner) and needs
`--image python:3.12-slim@sha256:<digest>` already pulled locally. `--host-runner` is an explicit
opt-in for machines without Docker: it runs the synthetic fixture tests (and model-written code)
with `python -I -m unittest` in a temp dir on the host. Never use it with real repositories.

Before/after comparison: run once with PYTHONPATH pointing at a snapshot of the old code
(`git archive 955ef4f role_ai | tar -x -C /tmp/old`) and once at this checkout.

    PYTHONPATH=. python qa/bench_coding_models.py --label S1_after --image python:3.12-slim@sha256:... \
        --out qa/reports/coding-bench/S1_after.jsonl
    python qa/bench_coding_report.py qa/reports/coding-bench/*.jsonl

Results and remaining runs: docs/coding-agent-stabilization.md
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

import role_ai.coding_agent as agent_module
from role_ai.coding_agent import CodingAction, CodingAgent, LocalCodingModel, runtime_identity
from role_ai.coding_jobs import CodingJobStore, TaskBudget, TaskCreate, checksum, decode_files
from role_ai.coding_runner import RepositoryTestRunner
from role_ai.config import Settings

NEW = hasattr(agent_module, "parse_reply")

TASKS = {
    "T1_add_bug": {
        "request": "add() returns the wrong result. Fix calculator.py so all tests pass.",
        "allowed": ["calculator.py"],
        "files": {
            "calculator.py": "def add(a, b):\n    return a - b\n",
            "test_calculator.py": (
                "import unittest\nfrom calculator import add\n"
                "class AddTests(unittest.TestCase):\n"
                "    def test_positive(self): self.assertEqual(add(2, 3), 5)\n"
                "    def test_negative(self): self.assertEqual(add(-1, 1), 0)\n"
                "    def test_zero(self): self.assertEqual(add(0, 0), 0)\n"),
        },
    },
    "T2_slugify": {
        "request": "Implement slugify(text) in textutil.py so the tests pass.",
        "allowed": ["textutil.py"],
        "files": {
            "textutil.py": "def slugify(text):\n    raise NotImplementedError\n",
            "test_textutil.py": (
                "import unittest\nfrom textutil import slugify\n"
                "class SlugTests(unittest.TestCase):\n"
                "    def test_basic(self): self.assertEqual(slugify('Hello World'), 'hello-world')\n"
                "    def test_punct(self): self.assertEqual(slugify('Hi, there! 2024'), 'hi-there-2024')\n"
                "    def test_spaces(self): self.assertEqual(slugify('  a   b  '), 'a-b')\n"
                "    def test_dashes(self): self.assertEqual(slugify('a--b__c'), 'a-b-c')\n"
                "    def test_empty(self): self.assertEqual(slugify('!!!'), '')\n"),
        },
    },
    "T3_stats_multi": {
        "request": "stats.py has bugs. Fix median and mode so all tests pass; do not change mean.",
        "allowed": ["stats.py"],
        "files": {
            "stats.py": (
                "def mean(xs):\n    return sum(xs) / len(xs)\n\n\n"
                "def median(xs):\n    xs = sorted(xs)\n    return xs[len(xs) // 2]\n\n\n"
                "def mode(xs):\n    counts = {}\n    for x in xs:\n        counts[x] = counts.get(x, 0) + 1\n"
                "    return max(counts)\n"),
            "test_stats.py": (
                "import unittest\nfrom stats import mean, median, mode\n"
                "class StatsTests(unittest.TestCase):\n"
                "    def test_mean(self): self.assertEqual(mean([1, 2, 3, 4]), 2.5)\n"
                "    def test_median_odd(self): self.assertEqual(median([3, 1, 2]), 2)\n"
                "    def test_median_even(self): self.assertEqual(median([4, 1, 3, 2]), 2.5)\n"
                "    def test_mode(self): self.assertEqual(mode([1, 2, 2, 3]), 2)\n"
                "    def test_mode_tie_smallest(self): self.assertEqual(mode([5, 5, 1, 1, 9]), 1)\n"
                "    def test_median_empty(self):\n"
                "        with self.assertRaises(ValueError): median([])\n"),
        },
    },
    # New tasks (not seen while building the fixes) to check for overfitting.
    "T4_top_words": {
        "request": "Implement top_words(text, n) in words.py so the tests pass.",
        "allowed": ["words.py"],
        "files": {
            "words.py": ("def top_words(text, n):\n"
                         "    \"\"\"Return the n most common words as (word, count) pairs.\"\"\"\n"
                         "    raise NotImplementedError\n"),
            "test_words.py": (
                "import unittest\nfrom words import top_words\n"
                "class WordTests(unittest.TestCase):\n"
                "    def test_counts(self):\n"
                "        self.assertEqual(top_words('b a b c b a', 2), [('b', 3), ('a', 2)])\n"
                "    def test_case_and_punct(self):\n"
                "        self.assertEqual(top_words('Hi, hi! HI. there', 1), [('hi', 3)])\n"
                "    def test_tie_alphabetical(self):\n"
                "        self.assertEqual(top_words('z y x', 2), [('x', 1), ('y', 1)])\n"
                "    def test_n_larger(self):\n"
                "        self.assertEqual(top_words('one', 5), [('one', 1)])\n"
                "    def test_empty(self):\n"
                "        self.assertEqual(top_words('', 3), [])\n"),
        },
    },
    "T5_account_crlf": {
        "request": "account.py has bugs in deposit and withdraw. Fix them so the tests pass.",
        "allowed": ["account.py"],
        "files": {
            "account.py": ("class Account:\r\n"
                           "    def __init__(self, balance=0):\r\n"
                           "        self.balance = balance\r\n\r\n"
                           "    def deposit(self, amount):\r\n"
                           "        self.balance += amount\r\n"
                           "        return self.balance\r\n\r\n"
                           "    def withdraw(self, amount):\r\n"
                           "        self.balance -= amount\r\n"
                           "        return self.balance\r\n"),
            "test_account.py": (
                "import unittest\nfrom account import Account\n"
                "class AccountTests(unittest.TestCase):\n"
                "    def test_deposit(self): self.assertEqual(Account(10).deposit(5), 15)\n"
                "    def test_deposit_negative(self):\n"
                "        with self.assertRaises(ValueError): Account(10).deposit(-1)\n"
                "    def test_withdraw(self): self.assertEqual(Account(10).withdraw(4), 6)\n"
                "    def test_overdraw(self):\n"
                "        a = Account(10)\n"
                "        with self.assertRaises(ValueError): a.withdraw(11)\n"
                "        self.assertEqual(a.balance, 10)\n"
                "    def test_withdraw_negative(self):\n"
                "        with self.assertRaises(ValueError): Account(10).withdraw(-3)\n"),
        },
    },
}


class HostRunner:
    """Opt-in stand-in for the Docker runner: same result shape, host subprocess + timeout."""

    def run(self, state, name, directory):
        with tempfile.TemporaryDirectory(prefix="bench-run-") as tmp:
            for path, raw in decode_files(state["files"]).items():
                (Path(tmp) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / path).write_bytes(raw)
            try:
                p = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test*.py",
                                    "-t", ".", "-v"], cwd=tmp, capture_output=True, text=True, timeout=20,
                                   env={"PATH": os.environ["PATH"], "HOME": tmp, "PYTHONPATH": tmp})
                out, code, reason = (p.stdout + p.stderr)[-16000:], p.returncode, ""
            except subprocess.TimeoutExpired:
                out, code, reason = "timeout", -9, "timeout"
        ran = next((int(line.split()[1]) for line in out.splitlines() if line.startswith("Ran ")), 0)
        passed = not reason and code == 0 and ran > 0
        return {"status": "passed" if passed else "failed", "reason": reason or "tests", "exit_code": code,
                "count": ran, "output": out, "command": "unittest", "image": "host-subprocess",
                "container": name, "revision": state["revision"]}

    def reconcile(self, pending):
        pass


class OldTimedModel(LocalCodingModel):
    """Old request body, byte-for-byte, but keeps Ollama's usage metadata for measurement."""

    def __init__(self, settings):
        super().__init__(settings)
        self.calls = []

    async def _call(self, state, messages):
        import asyncio
        from role_ai.providers import OllamaProvider
        async with asyncio.timeout(state.get("inference_timeout", self.settings.provider_timeout_seconds)), \
                OllamaProvider(self.settings).client() as client:
            (await client.post("api/show", json={"model": self.settings.ollama_model})).raise_for_status()
            r = await client.post("api/chat", json={
                "model": self.settings.ollama_model, "messages": messages,
                "format": CodingAction.model_json_schema(), "stream": False,
                "keep_alive": 0, "options": {"num_ctx": 4096, "temperature": 0,
                                             "num_predict": state["spec"]["budget"]["output_tokens"]}})
            r.raise_for_status()
            data = r.json()
            self.calls.append({"prompt_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"),
                               "load_seconds": data.get("load_duration", 0) / 1e9,
                               "done_reason": data.get("done_reason"), "raw": data["message"]["content"][:1500]})
            return data["message"]["content"]


class NewTimedModel(LocalCodingModel):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = []

    async def _call(self, state, messages):
        reply = await super()._call(state, messages)
        self.calls.append({k: reply.get(k) for k in ("prompt_tokens", "output_tokens", "load_seconds",
                                                    "done_reason", "think", "thinking_chars")}
                          | {"raw": reply["content"][:1500]})
        return reply


class VramSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = 0, threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                used = int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used",
                                                    "--format=csv,noheader,nounits"], timeout=3).split()[0])
                self.peak = max(self.peak, used)
            except Exception:
                pass
            self.stop.wait(0.5)


def git_repo(path, files):
    path.mkdir()
    for name, text in files.items():
        (path / name).write_bytes(text.encode())
    env = {"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    g = lambda *a: subprocess.check_output(["git", "-C", str(path), *a], env=env).decode().strip()  # noqa: E731
    g("init", "-q"); g("config", "core.autocrlf", "false"); g("add", ".")  # noqa: E702
    g("-c", "user.name=b", "-c", "user.email=b@x.test", "commit", "-qm", "base")
    return g("rev-parse", "HEAD")


def unload_all():
    try:
        for m in httpx.get("http://127.0.0.1:11434/api/ps", timeout=5).json().get("models", []):
            httpx.post("http://127.0.0.1:11434/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
    except httpx.HTTPError:
        pass
    time.sleep(2)


def strict_valid(raw):
    try:
        CodingAction.model_validate_json(raw)
        return True
    except Exception:
        return False


def analyse(final, calls, task):
    raws = [c["raw"] for c in calls]
    parsed = []
    for raw in raws:
        try:
            parsed.append(json.loads(raw))
        except Exception:
            parsed.append(None)
    sigs, repeats = [], 0
    for p in parsed:
        if isinstance(p, dict) and p.get("action") in {"plan", "read"}:
            sig = json.dumps({k: p.get(k) for k in ("action", "path", "plan")}, sort_keys=True)
            repeats += sig in sigs
            sigs.append(sig)
    attempts = sum(1 for p in parsed if isinstance(p, dict) and p.get("action") == "patch")
    applied, prev = 0, final.get("original_revision") or (checksum(final["original"]) if final.get("original") else None)
    for h in final["history"]:
        if h["phase"] == "patch" and h["revision"] != prev:
            applied += 1
        prev = h["revision"]
    failures = (final["retries"] > 0 or final.get("tool_errors", 0) > 0
                or any(not strict_valid(r) for r in raws))
    measured = [c for c in calls if c.get("prompt_tokens") is not None and c.get("output_tokens") is not None]
    return {
        "json_valid": sum(map(strict_valid, raws)), "replies": len(raws),
        "patch_attempts": attempts, "patch_applied": applied,
        "had_failure": failures, "recovered": failures and final["status"] == "completed",
        "repeats": repeats,
        "prompt_tokens": sum(c["prompt_tokens"] for c in measured),
        "output_tokens": sum(c["output_tokens"] for c in measured),
        "unmeasured_calls": len(calls) - len(measured),
        "load_seconds": round(sum(c.get("load_seconds") or 0 for c in calls), 1),
        "output_limit_hits": sum(1 for c in calls if c.get("done_reason") == "length"),
    }


def run_one(args, model_name, task_id, root):
    task = TASKS[task_id]
    base = root / f"{model_name.replace(':', '_')}-{task_id}"
    base.mkdir()
    head = git_repo(base / "repo", task["files"])
    settings = Settings(_env_file=None, coding_enabled=True, coding_root=str(base / "jobs"),
                        coding_repositories={"bench": str(base / "repo")}, storage_dir=str(base / "storage"),
                        database_url=f"sqlite:///{base / 'auth.db'}", ollama_model=model_name,
                        provider_timeout_seconds=args.timeout,
                        coding_image=args.image or "python:3.12-slim@sha256:" + "0" * 64)
    Path(settings.storage_dir).mkdir()
    extra = {}
    if NEW:
        extra["model"] = {"think": args.think, "keep_alive_seconds": args.keep, "post_task_keep_alive_seconds": 0}
    body = TaskCreate.model_validate_json(json.dumps({
        "repository_id": "bench", "project_id": "bench", "request": task["request"],
        "allowed_files": task["allowed"], "approved": True, "record_strategy": False,
        "budget": TaskBudget().model_dump(), **extra}))
    store = CodingJobStore(settings.coding_root)
    state = store.create("owner", body, head, checksum(str(base / "repo")),
                         runtime_hash=runtime_identity(settings, "bench"))
    model = (NewTimedModel if NEW else OldTimedModel)(settings)
    runner = HostRunner() if args.host_runner else RepositoryTestRunner(settings)
    agent = CodingAgent(settings, model=model, runner=runner)
    sampler = VramSampler()
    sampler.start()
    t = time.monotonic()
    final = agent.run("owner", state["id"])
    wall = time.monotonic() - t
    sampler.stop.set()
    sampler.join()
    if not NEW:
        unload_all()
    code = {k: v.decode(errors="replace") for k, v in decode_files(final["files"] or {}).items() if k in task["allowed"]}
    return {"label": args.label, "runner": "host" if args.host_runner else "docker", "model": model_name, "task": task_id, "status": final["status"],
            "error": final.get("error"), "tests": (final.get("last_test") or {}).get("status"),
            "tested_current": final.get("tested_revision") == final.get("revision") and final.get("revision") is not None,
            "wall_sec": round(wall, 1), "calls": final["calls"], "steps": final["steps"],
            "reserved_tokens": final["calls"] * (4096 + final["spec"]["budget"]["output_tokens"]),
            "budget_tokens_charged": final["tokens"], "vram_peak_mib": sampler.peak,
            "phases": [h["phase"] for h in final["history"]], **analyse(final, model.calls, task),
            "usage": final.get("usage") and {k: v for k, v in final["usage"].items() if k != "calls"},
            "final_code": code, "raw": [c["raw"] for c in model.calls]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    p.add_argument("--models", default="qwen3.5:4b,qwen3:8b")
    p.add_argument("--tasks", default=",".join(TASKS))
    p.add_argument("--think", default="off")
    p.add_argument("--keep", type=int, default=300)
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--no-normalize", action="store_true")
    p.add_argument("--image", default="", help="digest-pinned local image for the Docker runner")
    p.add_argument("--host-runner", action="store_true",
                   help="run synthetic fixture tests on the host instead of Docker (no Docker available)")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    if not args.host_runner and not args.image:
        p.error("--image is required for the Docker runner (or pass --host-runner explicitly)")
    if args.host_runner:
        print("WARNING: --host-runner executes model-written fixture code on this host", file=sys.stderr)
    if args.no_normalize:
        import role_ai.coding_edits as edits
        original = edits._candidates
        edits._candidates = lambda text, old, new: (c for c in original(text, old, new) if c[0] == "exact")
    root = Path(tempfile.mkdtemp(prefix="bench2-"))
    unload_all()
    for m in args.models.split(","):
        for t in args.tasks.split(","):
            try:
                r = run_one(args, m, t, root)
            except Exception as e:
                r = {"label": args.label, "model": m, "task": t, "status": "harness_error", "error": repr(e)}
            print(json.dumps({k: r.get(k) for k in ("label", "model", "task", "status", "wall_sec", "calls",
                                                    "json_valid", "replies", "patch_applied", "repeats", "error")},
                             ensure_ascii=False), flush=True)
            with open(args.out, "a") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        unload_all()


if __name__ == "__main__":
    main()
