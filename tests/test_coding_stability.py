"""Regression tests: thinking control, response parsing, working memory, patch recovery, usage settlement.

Scripted model/runner only; this is not evidence of real LLM quality.
"""

import asyncio
import json

import httpx
import pytest

from role_ai.coding_agent import CodingAction, CodingAgent, LocalCodingModel, parse_reply
from role_ai.coding_edits import EditMismatch, apply_changes, replace_once
from role_ai.coding_jobs import TaskBudget, TaskCreate, checksum, decode_files
from role_ai.coding_memory import CONTEXT_LIMIT, context
from test_coding_runtime import FixtureRunner, ScriptModel, coding  # noqa: F401


def run(coding, actions, **options):  # noqa: F811
    settings, _, state, *_ = coding
    model = ScriptModel(actions)
    result = CodingAgent(settings, model=model, runner=FixtureRunner()).run("owner", state["id"], **options)
    return result, model


def last_context(model):
    return json.loads(model.prompts[-1][1]["content"])


PLAN = {"action": "plan", "plan": ["Read, patch, test"]}
TEST = {"action": "test"}
FINISH = {"action": "finish", "summary": "done"}


# --- 4.3 patch matching -------------------------------------------------------------------

def test_json_escapes_decode_to_real_newlines_and_double_escapes_are_repaired():
    change = CodingAction.model_validate_json(
        '{"action":"patch","changes":[{"path":"a.py","old_text":"x = 1\\ny = 2","new_text":"x = 1\\ny = 3"}]}'
    ).changes[0]
    assert change.old_text == "x = 1\ny = 2"
    # A model that double-escapes sends literal backslash-n; only a unique match is repaired.
    text, label = replace_once("def add(a, b):\n    return a - b\n",
                               "def add(a, b):\\n    return a - b\\n", "def add(a, b):\\n    return a + b\\n")
    assert text == "def add(a, b):\n    return a + b\n" and label == "unescaped"


def test_crlf_and_missing_final_newline_are_matched_consistently():
    text, label = replace_once("a = 1\r\nb = 2\r\n", "a = 1\nb = 2", "a = 1\nb = 3")
    assert text == "a = 1\r\nb = 3\r\n" and "crlf" in label
    text, label = replace_once("x = 0", "x = 0\n", "x = 1\n")
    assert text == "x = 1" and label.endswith("eof")


def test_ambiguous_or_missing_text_is_never_patched():
    with pytest.raises(EditMismatch) as ambiguous:
        replace_once("x = 1\nx = 1\n", "x = 1", "x = 2")
    assert ambiguous.value.reason == "ambiguous"
    with pytest.raises(EditMismatch) as missing:
        replace_once("def f():\n    return 1\n", "def f():\n  return 2", "z")
    assert missing.value.reason == "not_found"
    assert "def f():" in missing.value.detail["closest"]["text"]


def test_several_edits_to_one_file_merge_in_order_and_partial_failure_changes_nothing():
    action = CodingAction.model_validate_json(json.dumps({"action": "patch", "changes": [
        {"path": "s.py", "old_text": "a = 1", "new_text": "a = 10"},
        {"path": "s.py", "old_text": "b = 2", "new_text": "b = 20"}]}))
    merged, _ = apply_changes({"s.py": b"a = 1\nb = 2\n"}, action.changes)
    assert merged == [{"path": "s.py", "content": "a = 10\nb = 20\n"}]
    broken = CodingAction.model_validate_json(json.dumps({"action": "patch", "changes": [
        {"path": "s.py", "old_text": "a = 1", "new_text": "a = 10"},
        {"path": "s.py", "old_text": "missing", "new_text": "x"}]}))
    files = {"s.py": b"a = 1\nb = 2\n"}
    with pytest.raises(EditMismatch):
        apply_changes(files, broken.changes)
    assert files == {"s.py": b"a = 1\nb = 2\n"}


def test_two_changes_to_same_file_complete_end_to_end(coding):  # noqa: F811
    result, _ = run(coding, [PLAN, {"action": "patch", "changes": [
        {"path": "calc.py", "old_text": "answer = 0", "new_text": "answer = 4"},
        {"path": "calc.py", "old_text": "answer = 4", "new_text": "answer = 42"}]}, TEST, FINISH])
    assert result["status"] == "completed", result.get("error")
    assert decode_files(result["files"])["calc.py"] == b"answer = 42\n"


def test_patch_mismatch_is_recoverable_and_checkpoint_stays_consistent(coding):  # noqa: F811
    bad = {"action": "patch", "changes": [{"path": "calc.py", "old_text": "answer = 7", "new_text": "answer = 42"}]}
    good = {"action": "patch", "changes": [{"path": "calc.py", "old_text": "answer = 0", "new_text": "answer = 42"}]}
    result, model = run(coding, [PLAN, {"action": "read", "path": "calc.py"}, bad, good, TEST, FINISH])
    assert result["status"] == "completed", result.get("error")
    after_failure = json.loads(model.prompts[3][1]["content"])
    assert after_failure["last_patch"]["status"] == "rejected"
    assert after_failure["last_patch"]["closest"]["text"].startswith("answer = 0")
    assert after_failure["file_views"]["calc.py"]["text"] == "answer = 0\n"
    assert result["tool_errors"] == 1


def test_identical_rejected_patch_stops_instead_of_looping(coding):  # noqa: F811
    bad = {"action": "patch", "changes": [{"path": "calc.py", "old_text": "nope", "new_text": "answer = 42"}]}
    result, model = run(coding, [PLAN, bad, bad, bad, bad])
    assert result["status"] == "budget_exhausted" and len(model.prompts) == 3
    assert decode_files(result["files"])["calc.py"] == b"answer = 0\n"


def test_scope_violation_stays_fatal_even_with_recovery(coding):  # noqa: F811
    result, _ = run(coding, [PLAN, {"action": "patch", "changes": [
        {"path": "calc.py", "old_text": "answer = 0", "new_text": "answer = 42"},
        {"path": "test_calc.py", "content": "pass"}]}])
    assert result["status"] == "failed"
    assert decode_files(result["files"])["calc.py"] == b"answer = 0\n"


def test_finish_without_passing_test_is_rejected_and_never_completes(coding):  # noqa: F811
    patch = {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 1\n"}]}
    result, _ = run(coding, [PLAN, patch, FINISH, TEST, FINISH, FINISH])
    assert result["status"] in {"budget_exhausted", "failed"} and "report" not in result
    assert result["tested_revision"] is None


# --- 4.1 response parsing / thinking -------------------------------------------------------

@pytest.mark.parametrize("reply,kind", [
    ({"content": "", "done_reason": "stop"}, "empty_response"),
    ({"content": "", "done_reason": "length"}, "output_limit"),
    ({"content": '{"action": "pla', "done_reason": "length"}, "output_limit"),
    ({"content": "not json", "done_reason": "stop"}, "invalid_json"),
    ({"content": '{"action":"shell"}', "done_reason": "stop"}, "schema_invalid"),
    ({"content": "[1]", "done_reason": "stop"}, "schema_invalid"),
])
def test_response_errors_are_classified(reply, kind):
    assert parse_reply(reply) == (None, kind)


@pytest.mark.parametrize("content", [
    '```json\n{"action":"test"}\n```', '<think>hmm</think>{"action":"test"}', 'Sure: {"action":"test"} done',
])
def test_action_json_is_extracted_without_relaxing_the_schema(content):
    assert parse_reply(content)[0].action == "test"


def test_empty_replies_are_recorded_and_bounded(coding):  # noqa: F811
    settings, _, state, *_ = coding
    class Empty:
        def complete(self, state, messages):
            return {"content": "", "done_reason": "length", "prompt_tokens": 100, "output_tokens": 1024}
    result = CodingAgent(settings, model=Empty(), runner=FixtureRunner()).run("owner", state["id"])
    assert result["status"] == "budget_exhausted"
    assert result["usage"]["response_errors"] == {"output_limit": 2}


def _capture(monkeypatch, capabilities):
    from role_ai.providers import OllamaProvider
    seen = []
    def handle(request):
        data = json.loads(request.content)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": capabilities})
        seen.append(data)
        return httpx.Response(200, json={"message": {"content": '{"action":"test"}', "thinking": "x" * 7},
                                         "done_reason": "stop", "prompt_eval_count": 180, "eval_count": 12,
                                         "load_duration": 4_000_000_000, "total_duration": 5_000_000_000})
    monkeypatch.setattr(OllamaProvider, "client", lambda self: httpx.AsyncClient(
        base_url="http://localhost/", transport=httpx.MockTransport(handle), trust_env=False))
    return seen


@pytest.mark.parametrize("think,sent", [("off", False), ("on", True), ("model_default", None)])
def test_thinking_flag_is_explicit_per_task_and_usage_is_reported(coding, monkeypatch, think, sent):  # noqa: F811
    settings, _, state, *_ = coding
    state["spec"]["model"] = {"think": think}
    seen = _capture(monkeypatch, ["completion", "thinking"])
    reply = asyncio.run(LocalCodingModel(settings)._call(state, []))
    assert seen[0].get("think") == sent
    assert seen[0]["keep_alive"] == "300s" and seen[0]["options"]["num_ctx"] == 4096
    assert reply["prompt_tokens"] == 180 and reply["output_tokens"] == 12
    assert reply["load_seconds"] == 4.0 and reply["thinking_chars"] == 7


def test_keep_alive_is_zero_under_gpu_admission_policy(coding, monkeypatch):  # noqa: F811
    from role_ai.gpu_resources import GpuPolicy, write_policy
    settings, _, state, *_ = coding
    write_policy(settings, GpuPolicy(enabled=True))
    seen = _capture(monkeypatch, ["completion"])
    asyncio.run(LocalCodingModel(settings)._call(state, []))
    assert seen[0]["keep_alive"] == 0


def test_release_only_touches_a_resident_model(coding, monkeypatch):  # noqa: F811
    settings, _, state, *_ = coding
    posts = []
    def handle(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": resident})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={})
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(**{**kw, "transport": httpx.MockTransport(handle)}))
    resident = []
    assert LocalCodingModel(settings).release(state) is False and posts == []
    resident = [{"name": settings.ollama_model}]
    assert LocalCodingModel(settings).release(state) is True
    assert posts == [{"model": settings.ollama_model, "keep_alive": 0}]


# --- 4.5 usage settlement ------------------------------------------------------------------

class Measured(ScriptModel):
    def complete(self, state, messages):
        return {"content": super().complete(state, messages), "prompt_tokens": 200, "output_tokens": 20,
                "load_seconds": 0.5, "total_seconds": 1.0, "done_reason": "stop"}


def test_measured_usage_replaces_reservation_and_unmeasured_keeps_it(coding):  # noqa: F811
    settings, store, state, *_ = coding
    actions = [PLAN, {"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 42\n"}]}, TEST, FINISH]
    result = CodingAgent(settings, model=Measured(actions), runner=FixtureRunner()).run("owner", state["id"])
    assert result["status"] == "completed"
    usage = result["usage"]
    assert result["tokens"] == usage["total_tokens"] == 4 * 220
    assert usage["prompt_tokens"] == 800 and usage["output_tokens"] == 80
    assert usage["reserved_tokens"] == 4 * (4096 + 1024) and usage["unmeasured_calls"] == 0
    assert usage["load_seconds"] == 2.0 and len(usage["calls"]) == 4


def test_unmeasured_reply_is_charged_the_full_reservation(coding):  # noqa: F811
    result, _ = run(coding, [PLAN], max_actions=1)
    assert result["tokens"] == 4096 + 1024
    assert result["usage"]["unmeasured_calls"] == 1 and result["usage"]["total_tokens"] == 0


# --- 4.2 working memory ----------------------------------------------------------------------

def test_memory_prevents_reread_and_stops_identical_loops(coding):  # noqa: F811
    read = {"action": "read", "path": "calc.py"}
    result, model = run(coding, [PLAN, read, read, read, read])
    assert result["status"] == "budget_exhausted" and len(model.prompts) == 4
    ctx = last_context(model)
    assert ctx["last_result"]["result"]["notice"].endswith(ctx["next"])
    assert ctx["file_views"]["calc.py"]["text"] == "answer = 0\n"
    assert "read calc.py" in ctx["progress"] and ctx["warning"]
    assert ctx["next"].startswith(("read test_calc.py", "patch"))


def test_no_progress_alternation_is_bounded(coding):  # noqa: F811
    actions = []
    for i in range(12):
        actions += [{"action": "plan", "plan": [f"Plan {i}"]}, {"action": "search", "query": f"q{i}"}]
    result, model = run(coding, actions)
    assert result["status"] == "budget_exhausted" and len(model.prompts) <= 9


def test_memory_survives_pause_resume_and_old_checkpoints_load(coding):  # noqa: F811
    settings, store, initial, *_ = coding
    first, _ = run(coding, [PLAN, {"action": "read", "path": "calc.py"}], max_actions=2)
    assert first["status"] == "paused" and first["memory"]["seen"]["calc.py"]
    store.resume("owner", initial["id"])
    directory, state = store.load("owner", initial["id"])
    # Checkpoints written before this change have no memory/usage/model keys.
    for key in ("memory", "usage"):
        state.pop(key)
    state["spec"].pop("model", None)
    store.save(directory, state)
    second, model = run(coding, [{"action": "patch", "changes": [{"path": "calc.py", "content": "answer = 42\n"}]},
                                 TEST, FINISH])
    assert second["status"] == "completed" and second["usage"]["unmeasured_calls"] == 3


def test_model_context_is_bounded_and_test_log_is_digested(coding):  # noqa: F811
    settings, store, state, *_ = coding
    directory, state = store.load("owner", state["id"])
    from role_ai.coding_jobs import encode_files
    state["files"] = encode_files({"calc.py": b"x" * 50000, "test_calc.py": b"y" * 50000})
    state["revision"] = checksum(state["files"])
    state["memory"] = {"seen": {"calc.py": "r", "test_calc.py": "r"}}
    noisy = "\n".join(["noise line"] * 4000 + ["FAIL: test_answer", "AssertionError: 0 != 42"] * 3)
    state["last_test"] = {"status": "failed", "output": noisy, "revision": "old", "count": 1}
    body = context(state, decode_files(state["files"]))
    assert len(json.dumps(body)) <= CONTEXT_LIMIT
    assert "noise line" not in body["last_test"]["failures"]
    assert body["last_test"]["failures"].count("FAIL: test_answer") == 1
    assert body["last_test"]["for_current_code"] is False


def test_new_task_options_validate_and_api_defaults_stay_compatible():
    body = TaskCreate(repository_id="r", project_id="p", request="x", allowed_files=("a.py",))
    assert body.model.think == "off" and body.model.keep_alive_seconds == 300
    assert body.budget.tool_error_limit == 4
    with pytest.raises(ValueError):
        TaskCreate.model_validate_json(json.dumps({"repository_id": "r", "project_id": "p", "request": "x",
                                                   "allowed_files": ["a.py"], "model": {"think": "maybe"}}))
    with pytest.raises(ValueError):
        TaskBudget(tool_error_limit=99)
