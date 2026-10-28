"""Context boundaries, atomic recovery and auditable inference accounting."""

import copy
import json
import importlib.util
from pathlib import Path

import httpx
import pytest

from role_ai.coding_agent import CodingAction, CodingAgent, model_reply
from role_ai.coding_context import ContextBudgetError, input_bound
from role_ai.coding_edits import EditMismatch, replace_once
from role_ai.coding_jobs import CheckpointWriteError, decode_files
from role_ai.coding_memory import context, note_action
from test_coding_runtime import FixtureRunner, ScriptModel, coding  # noqa: F401


def qa_module(name):
    """QA is source tooling, deliberately excluded from the application wheel."""
    path = Path(__file__).resolve().parents[1] / "qa" / (name + ".py")
    spec = importlib.util.spec_from_file_location("evaluation_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLAN = {"action": "plan", "plan": ["Fix, test"]}
FIX = {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 42\n"}]}
END = [{"action": "test"}, {"action": "finish", "summary": "done"}]


def test_context_enforces_character_and_utf8_budgets_without_altering_goal(coding):  # noqa: F811
    _, _, initial, *_ = coding
    state = copy.deepcopy(initial)
    state.update(plan=["p" * 300] * 8, observations=[], steps=1)
    state["spec"]["request"] = "한글 목표" * 500
    state["files"] = {}
    state["memory"] = {"done": ["x" * 200] * 20, "seen": {}, "failed": ["f" * 900] * 20}
    result = context(state, {}, max_chars=16000, max_bytes=13000)
    assert result["request"] == state["spec"]["request"]
    assert len(json.dumps(result, ensure_ascii=False)) <= 16000
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 13000
    assert result["context_policy"]["recovery"]
    assert len(result["progress"]) <= 1


def test_core_context_overflow_stops_before_inference_or_reservation(coding):  # noqa: F811
    settings, store, state, *_ = coding
    directory, state = store.load("owner", state["id"])
    state["spec"]["request"] = "한" * 4000
    store.save(directory, state)
    model = ScriptModel([])
    result = CodingAgent(settings, model=model, runner=FixtureRunner()).run("owner", state["id"])
    assert result["diagnostic"]["code"] == "context_budget_exhausted"
    assert result["calls"] == result["tokens"] == 0 and model.prompts == []


def test_source_windows_are_verbatim_and_budgeted_including_system_and_output(coding):  # noqa: F811
    settings, _, state, *_ = coding
    from role_ai.coding_jobs import encode_files

    source = ("한글 = 'quoted\\n'\n" * 400).encode()
    state.update(files=encode_files({"calc.py": source}), revision="r", original_revision="r")
    state["memory"] = {"seen": {"calc.py": "r"}, "windows": {"calc.py": 1500}}
    agent = CodingAgent(settings, model=ScriptModel([]), runner=FixtureRunner())
    messages = agent.messages(state)
    body = json.loads(messages[1]["content"])
    view = body["file_views"]["calc.py"]
    assert view["text"] == source.decode()[view["offset"] : view["next_offset"]]
    assert view["offset"] == 1500 and view["next_offset"] > 1500 and view["truncated"]
    assert input_bound(messages) + state["spec"]["budget"]["output_tokens"] <= 4096
    assert state["context_usage"]["exact_input_tokens"] is None


def test_context_does_not_silently_truncate_irreducible_task(coding):  # noqa: F811
    _, _, state, *_ = coding
    with pytest.raises(ContextBudgetError):
        context(state, {}, max_bytes=1)


def test_noop_patch_is_recoverable_and_later_real_fix_completes(coding):  # noqa: F811
    settings, _, state, repo, git = coding
    model = ScriptModel(
        [
            PLAN,
            {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 0\n"}]},
            FIX,
            *END,
        ]
    )
    result = CodingAgent(settings, model=model, runner=FixtureRunner()).run("owner", state["id"])
    assert result["status"] == "completed" and result["tool_errors"] == 1
    assert '"code":"patch_no_change"' in json.dumps(result["observations"], separators=(",", ":"))
    assert decode_files(result["files"])["calc.py"] == b"answer = 42\n"
    assert (repo / "calc.py").read_bytes() == b"answer = 0\n" and git("status", "--porcelain") == ""


def test_normalization_preserves_literal_escapes_inside_quoted_source():
    text = 'def f():\n    return "a\\nb"\n'
    old = 'def f():\\n    return "a\\nb"\\n'
    new = 'def f():\\n    return "c\\nd"\\n'
    result, label = replace_once(text, old, new)
    assert label == "unescaped" and result == 'def f():\n    return "c\\nd"\n'
    assert '"c\nd"' not in result


def test_patch_mismatch_lists_attempts_but_never_changes_input():
    text = "x = 1\r\ny = 2\r\n"
    with pytest.raises(EditMismatch) as caught:
        replace_once(text, "x = 9\ny = 3\n", "x = 7\ny = 8\n")
    assert caught.value.reason == "not_found"
    assert "exact+crlf" in caught.value.detail["normalization_attempts"]
    assert text == "x = 1\r\ny = 2\r\n"


def test_measured_settlement_is_idempotent_and_missing_usage_stays_reserved(coding):  # noqa: F811
    settings, _, state, *_ = coding
    agent = CodingAgent(settings, model=ScriptModel([]), runner=FixtureRunner())
    state.update(calls=1, tokens=5120)
    agent.reserve_usage(state, 5120)
    reply = {
        "prompt_tokens": 100,
        "output_tokens": 20,
        "prompt_eval_seconds": 0.2,
        "generation_seconds": 0.4,
    }
    agent.settle(state, 5120, reply, 0.7)
    snapshot = copy.deepcopy(state)
    agent.settle(state, 5120, reply, 0.7)
    assert state == snapshot and state["tokens"] == 120 and state["usage"]["charged_tokens"] == 120
    state["calls"] += 1
    state["tokens"] += 5120
    agent.reserve_usage(state, 5120)
    agent.settle(state, 5120, "unmeasured", 0.1)
    assert state["tokens"] == 5240 and state["usage"]["unmeasured_calls"] == 1
    assert state["usage"]["prompt_eval_seconds"] == 0.2


def test_timeout_persists_unknown_usage_and_does_not_expose_private_text(coding):  # noqa: F811
    settings, store, state, *_ = coding

    class Timeout:
        def complete(self, state, messages):
            raise httpx.ReadTimeout("SECRET source /private/path")

    result = CodingAgent(settings, model=Timeout(), runner=FixtureRunner()).run(
        "owner", state["id"]
    )
    assert result["status"] == "interrupted" and result["usage"]["unmeasured_calls"] == 1
    assert result["tokens"] == result["usage"]["charged_tokens"] == 5120
    assert "SECRET" not in json.dumps(result)
    _, saved = store.load("owner", state["id"])
    assert saved["usage"]["calls"][0]["status"] == "reserved"


def test_failed_post_call_checkpoint_keeps_disk_reservation_and_typed_error(coding, monkeypatch):  # noqa: F811
    settings, store, state, *_ = coding
    agent = CodingAgent(settings, model=ScriptModel([PLAN]), runner=FixtureRunner())
    atomic = agent.store.atomic

    def fail_after_response(path, value):
        if value.get("steps", 0) >= 1:
            raise PermissionError("SECRET storage path")
        return atomic(path, value)

    monkeypatch.setattr(agent.store, "atomic", fail_after_response)
    with pytest.raises(CheckpointWriteError, match="Checkpoint write failed") as caught:
        agent.run("owner", state["id"])
    assert caught.value.code == "checkpoint_io" and "SECRET" not in str(caught.value)
    _, saved = store.load("owner", state["id"])
    assert saved["pending"] == {"kind": "model"} and saved["tokens"] == 5120
    assert saved["usage"]["calls"][0]["status"] == "reserved"


def test_repeated_plan_after_real_new_revision_is_not_identical(coding):  # noqa: F811
    _, _, state, *_ = coding
    action = CodingAction(action="plan", plan=("Fix",))
    state["revision"] = "a"
    assert note_action(state, action) == 0
    assert note_action(state, action) == 2
    state["revision"] = "b"
    assert note_action(state, action) == 0


def test_reply_reports_duration_and_thinking_characters_without_invented_token_count():
    reply = model_reply(
        {
            "message": {"thinking": "reasoning"},
            "prompt_eval_count": 100,
            "eval_count": 20,
            "prompt_eval_duration": 200000000,
            "eval_duration": 400000000,
        },
        "{}",
        True,
    )
    assert reply["thinking_chars"] == 9 and reply["prompt_eval_seconds"] == 0.2
    assert reply["generation_seconds"] == 0.4 and "thinking_tokens" not in reply


def test_benchmark_metrics_separate_success_latency_and_unknown_tokens():
    summarize = qa_module("coding_eval_metrics").summarize

    rows = [
        dict(kind="attempt", success=True, wall_sec=20, actual_tokens=100, unreturned_calls=0),
        dict(kind="attempt", success=False, wall_sec=1, actual_tokens=5, unreturned_calls=0),
        dict(kind="attempt", success=False, wall_sec=60, actual_tokens=0, unreturned_calls=1),
    ]
    result = summarize(rows)
    assert result["success_fraction"] == "1/3" and result["success_mean_seconds"] == 20
    assert result["all_mean_seconds"] == 27 and result["unknown_token_attempts"] == 1
    assert (
        result["tokens_per_successful_task"] == 100
        and result["all_measured_tokens_per_success"] == 105
    )
    assert (
        result["thinking_tokens"] is None
        and result["wilson_95"][0] < 1 / 3 < result["wilson_95"][1]
    )


def test_grader_rejects_self_report_without_current_revision_or_protected_files():
    valid_outcome = qa_module("coding_eval_metrics").valid_outcome

    final = {"status": "completed", "revision": "a", "tested_revision": "a"}
    grade = {"status": "passed", "count": 3, "revision": "a"}
    assert valid_outcome(final, grade, True)
    assert not valid_outcome(final, grade, False)
    assert not valid_outcome({**final, "tested_revision": "old"}, grade, True)
    assert not valid_outcome(final, {**grade, "revision": "old"}, True)
    assert not valid_outcome(final, {**grade, "count": 0}, True)


def test_host_evaluation_rejects_all_skipped_tests(tmp_path):
    HostRunner = qa_module("bench_coding_models").HostRunner
    from role_ai.coding_jobs import encode_files

    source = "import unittest\n@unittest.skip('not executed')\nclass Skipped(unittest.TestCase):\n def test_one(self): pass\n"
    state = {"files": encode_files({"test_skip.py": source.encode()}), "revision": "a"}
    result = HostRunner().run(state, "fixture", tmp_path)
    assert result["count"] == result["skipped"] == 1 and result["status"] == "failed"


def test_proven_no_inference_refunds_reservation_and_slot(coding):  # noqa: F811
    from role_ai.coding_agent import InferenceNotStarted

    settings, _, state, *_ = coding

    class Unavailable:
        def complete(self, state, messages):
            raise InferenceNotStarted("No inference sent")

    result = CodingAgent(settings, model=Unavailable(), runner=FixtureRunner()).run(
        "owner", state["id"]
    )
    assert result["status"] == "failed" and result["diagnostic"]["code"] == "inference_not_started"
    assert result["calls"] == result["tokens"] == result["usage"]["charged_tokens"] == 0
    assert result["pending"] is None and result["usage"]["unmeasured_calls"] == 0


def test_deadline_before_send_does_not_charge_unmade_call(coding, monkeypatch):  # noqa: F811
    settings, _, state, *_ = coding
    model = ScriptModel([])
    agent = CodingAgent(settings, model=model, runner=FixtureRunner())
    clock = [0.0]
    original = agent.messages

    def late_context(current):
        messages = original(current)
        clock[0] = 2000.0
        return messages

    monkeypatch.setattr("role_ai.coding_agent.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(agent, "messages", late_context)
    result = agent.run("owner", state["id"])
    assert result["status"] == "budget_exhausted" and model.prompts == []
    assert result["tokens"] == result["calls"] == 0 and result["pending"] is None
    assert result["usage"]["charged_tokens"] == 0


def _automatic(fixture, *, enabled=True, calls=24, steps=24):
    settings, store, initial, *_ = fixture
    directory, state = store.load("owner", initial["id"])
    state["spec"]["model"]["automatic_tests"] = enabled
    state["spec"]["budget"].update(model_calls=calls, max_steps=steps)
    store.save(directory, state)
    return settings, store, state


def test_automatic_tests_complete_with_three_calls_and_real_unittest_evidence(coding):  # noqa: F811
    HostRunner = qa_module("bench_coding_models").HostRunner

    settings, store, state = _automatic(coding, calls=3)

    class Counting(HostRunner):
        calls = 0

        def run(self, *args):
            self.calls += 1
            return super().run(*args)

    runner = Counting()
    model = ScriptModel([PLAN, FIX, {"action": "finish", "summary": "fixed"}])
    result = CodingAgent(settings, model=model, runner=runner).run("owner", state["id"])
    assert result["status"] == "completed" and result["calls"] == 3 and result["steps"] == 4
    assert len(model.prompts) == 3 and runner.calls == 1
    assert result["last_test"]["status"] == "passed" and result["last_test"]["count"] == 1
    assert result["tested_revision"] == result["revision"]
    assert json.loads(model.prompts[-1][1]["content"])["last_test"]["for_current_code"]
    _, saved = store.load("owner", state["id"])
    assert saved["pending"] is None and saved["report"]["test"]["status"] == "passed"


def test_automatic_test_saves_model_slot_relative_to_same_legacy_budget(coding):  # noqa: F811
    settings, _, state = _automatic(coding, enabled=False, calls=3)
    model = ScriptModel([PLAN, FIX, *END])
    result = CodingAgent(settings, model=model, runner=FixtureRunner()).run("owner", state["id"])
    assert result["status"] == "budget_exhausted" and result["calls"] == 3
    assert result["last_test"]["status"] == "passed"  # finish still needs another model response


def test_automatic_test_respects_pause_resume_and_keeps_original_source(coding):  # noqa: F811
    settings, store, state = _automatic(coding)

    class Counting(FixtureRunner):
        calls = 0

        def run(self, *args):
            self.calls += 1
            return super().run(*args)

    runner = Counting()
    model = ScriptModel([PLAN, FIX, {"action": "finish"}])
    first = CodingAgent(settings, model=model, runner=runner).run(
        "owner", state["id"], max_actions=2
    )
    assert first["status"] == "paused" and runner.calls == 0 and first["last_test"] is None
    store.resume("owner", state["id"])
    final = CodingAgent(settings, model=model, runner=runner).run("owner", state["id"])
    assert final["status"] == "completed" and final["calls"] == 3 and runner.calls == 1
    assert (coding[3] / "calc.py").read_bytes() == b"answer = 0\n"


def test_automatic_test_cannot_bypass_step_budget(coding):  # noqa: F811
    settings, _, state = _automatic(coding, steps=2)

    class NeverRun(FixtureRunner):
        def run(self, *args):
            pytest.fail("Test runner must not exceed the approved step budget")

    result = CodingAgent(settings, model=ScriptModel([PLAN, FIX]), runner=NeverRun()).run(
        "owner", state["id"]
    )
    assert result["status"] == "budget_exhausted" and result["steps"] == 2
    assert result["last_test"] is None


def test_interrupted_automatic_test_keeps_pending_and_requires_acknowledgement(coding):  # noqa: F811
    from role_ai.coding_jobs import CodingJobError

    settings, store, state = _automatic(coding)

    class Interrupted(FixtureRunner):
        def run(self, *args):
            raise OSError("SECRET private runner path")

    result = CodingAgent(settings, model=ScriptModel([PLAN, FIX]), runner=Interrupted()).run(
        "owner", state["id"]
    )
    assert result["status"] == "interrupted" and result["pending"]["kind"] == "test"
    assert result["calls"] == 2 and result["steps"] == 3
    assert result["diagnostic"]["code"] == "tool_io" and "SECRET" not in json.dumps(result)
    with pytest.raises(CodingJobError, match="acknowledgement"):
        store.resume("owner", state["id"])


def test_repeated_tests_on_same_revision_reuse_evidence_without_rerunning(coding):  # noqa: F811
    settings, _, state = _automatic(coding, enabled=False)

    class Counting(FixtureRunner):
        calls = 0

        def run(self, *args):
            self.calls += 1
            return super().run(*args)

    runner = Counting()
    model = ScriptModel([PLAN, FIX, {"action": "test"}, {"action": "test"}, {"action": "finish"}])
    result = CodingAgent(settings, model=model, runner=runner).run("owner", state["id"])
    assert result["status"] == "completed" and runner.calls == 1
    assert any((o.get("result") or {}).get("reused") for o in result["observations"])


def test_old_approval_does_not_gain_automatic_execution_and_new_tasks_opt_in_by_default(coding):  # noqa: F811
    from role_ai.coding_agent import model_options
    from role_ai.coding_jobs import TaskCreate

    _, _, state, *_ = coding
    state["spec"]["model"].pop("automatic_tests", None)
    assert not model_options(state).automatic_tests
    body = TaskCreate(repository_id="r", project_id="p", request="fix", allowed_files=("calc.py",))
    assert body.model.automatic_tests


def test_context_retains_plan_status_without_duplicating_plan_or_source(coding):  # noqa: F811
    from role_ai.coding_jobs import encode_files

    _, _, state, *_ = coding
    plan = ["inspect and fix"] * 4
    state.update(
        plan=plan,
        files=encode_files({"calc.py": b"answer = 0\n"}),
        revision="r",
        original_revision="r",
    )
    state["observations"] = [{"action": "plan", "result": {"plan": plan, "notice": "continue"}}]
    body = context(state, {"calc.py": b"answer = 0\n"})
    assert body["plan_recorded"] and body["plan"] == plan
    assert "plan" not in body["last_result"]["result"]
    assert list(body)[-1] == "next"


def test_suppressed_permission_failure_still_has_safe_structured_code():
    from role_ai.coding_agent import failure_code
    from role_ai.coding_tasks import CodingBoundaryError

    try:
        try:
            raise PermissionError("SECRET path")
        except PermissionError:
            raise CodingBoundaryError("Workspace preparation failed") from None
    except CodingBoundaryError as exc:
        assert failure_code(exc) == "file_permission"
    assert failure_code(RuntimeError("SECRET contents")) == "internal_exception"


def test_cached_test_evidence_is_invalidated_by_new_revision(coding):  # noqa: F811
    settings, _, state = _automatic(coding, enabled=False)

    class Counting(FixtureRunner):
        runs = 0

        def run(self, *args):
            self.runs += 1
            return super().run(*args)

    runner = Counting()
    bad = {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 41\n"}]}
    model = ScriptModel(
        [PLAN, bad, {"action": "test"}, FIX, {"action": "test"}, {"action": "finish"}]
    )
    result = CodingAgent(settings, model=model, runner=runner).run("owner", state["id"])
    assert result["status"] == "completed" and runner.runs == 2
    assert result["tested_revision"] == result["revision"]
