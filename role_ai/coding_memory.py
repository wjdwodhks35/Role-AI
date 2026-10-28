"""Structured working memory for the Coding Agent.

The checkpoint keeps raw observations/test output. The model only receives this compact,
bounded summary, so it does not forget completed steps or re-read unchanged files.
"""

import re

from role_ai.coding_jobs import checksum
from role_ai.coding_context import select

FILE_VIEW_LIMIT = 4000
FILE_VIEWS_TOTAL = 9000
CONTEXT_LIMIT = 16000
WARN_NO_PROGRESS = 3
STOP_NO_PROGRESS = 8
STOP_IDENTICAL = 3
FAILURE_LINE = re.compile(
    r"^(FAIL|ERROR|FAILED)\b|Error\b|Exception\b|^\s*assert|^E\s|^\s+File \"|^Ran \d+|^OK\b|^\w+Error:")


def new_memory():
    return {"done": [], "seen": {}, "targets": [], "last_patch": None, "failed": [],
            "windows": {}, "signatures": [], "no_progress": 0, "repeats": 0, "warning": None}


def memory(state):
    current = state.get("memory")
    if not isinstance(current, dict):
        current = state["memory"] = new_memory()
    for key, value in new_memory().items():
        current.setdefault(key, value)
    return current


def remember(items, value, limit):
    items.append(value)
    del items[:-limit]


def test_digest(output, limit=1500):
    """Only failure-relevant lines, de-duplicated; raw output stays in the checkpoint."""
    kept, seen = [], set()
    for line in output.splitlines():
        if FAILURE_LINE.search(line) and line not in seen and "_roleai_runner" not in line:
            kept.append(line[:240])
            seen.add(line)
    text = "\n".join(kept) or output[-limit:]
    return text[-limit:]


def signature(action, state):
    body = action.model_dump(mode="json", exclude={"summary"})
    body["revision"] = state.get("revision")
    return checksum(body)[:16]


def note_action(state, action):
    """Track repeats before executing. Returns a loop reason or None; raises nothing."""
    mem = memory(state)
    sig = signature(action, state)
    identical = mem["signatures"].count(sig)
    remember(mem["signatures"], sig, 12)
    if identical:
        mem["repeats"] += 1
        return identical + 1
    return 0


def progress(state, made):
    mem = memory(state)
    mem["no_progress"] = 0 if made else mem["no_progress"] + 1
    if mem["no_progress"] >= WARN_NO_PROGRESS:
        mem["warning"] = (f"{mem['no_progress']} steps without changing code or running tests. "
                          f"Do not repeat earlier actions. Next: {next_step(state)}")
    elif made:
        mem["warning"] = None


def next_step(state):
    mem = memory(state)
    if not state.get("plan"):
        return "record a short plan with {\"action\":\"plan\",...}"
    last = state.get("last_test") or {}
    if state.get("revision") != state.get("original_revision"):
        if last.get("revision") != state.get("revision"):
            return 'run {"action":"test"}; a changed revision needs tests before more planning'
        if last.get("status") == "passed":
            return 'finish with {"action":"finish","summary":"..."}; current code already passed'
    editable = state["spec"]["allowed_files"]
    files = state.get("files") or {}
    unread = [p for p in editable if p in files and p not in mem["seen"]]
    if unread:
        return f"read {unread[0]}"
    tests = [p for p in files if p.rsplit("/", 1)[-1].startswith("test") and p.endswith(".py")]
    unread_tests = [p for p in tests if p not in mem["seen"]]
    if unread_tests and len(tests) <= 3 and not state.get("last_test"):
        return f"read {unread_tests[0]} to learn the expected behaviour"
    last = state.get("last_test")
    if state.get("revision") == state.get("original_revision") and not last:
        return "patch an editable file to fix the problem"
    if not last or last.get("revision") != state.get("revision"):
        return "run {\"action\":\"test\"}"
    if last["status"] == "passed":
        return "finish with a summary"
    return "patch the code to fix the failing test shown in last_test (use a different fix than before)"


def file_views(state, decoded):
    """Current content of files the agent has read; editable files first."""
    mem = memory(state)
    editable = state["spec"]["allowed_files"]
    order = [p for p in editable if p in mem["seen"]] + [p for p in mem["seen"] if p not in editable]
    views, total = {}, 0
    for path in order:
        raw = decoded.get(path)
        if raw is None:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            continue
        offset = mem["windows"].get(path, 0)
        shown = text[offset:offset + FILE_VIEW_LIMIT]
        if total + len(shown) > FILE_VIEWS_TOTAL:
            views[path] = {"omitted": "context limit; use read with offset"}
            continue
        total += len(shown)
        views[path] = {"text": shown, "length": len(text), "editable": path in editable,
                       "offset": offset, "next_offset": offset + len(shown),
                       **({"truncated": True} if len(text) > len(shown) else {})}
    return views


def compact_result(item):
    """Last observation without duplicating file text or full test logs."""
    if not item:
        return None
    result = item.get("result")
    if item.get("action") == "read" and isinstance(result, dict):
        result = {k: v for k, v in result.items() if k != "text"}
    if item.get("action") == "plan" and isinstance(result, dict):
        result = {k: v for k, v in result.items() if k != "plan"}
    if item.get("action") == "patch" and isinstance(result, dict):
        result = {k: v for k, v in result.items() if k in {"error", "code", "reason", "hint"}}
    if item.get("action") == "test" and isinstance(result, dict):
        result = {k: result.get(k) for k in ("status", "reason", "count", "exit_code")}
    return {k: v for k, v in {**item, "result": result}.items() if v is not None}


def context(state, decoded, *, max_chars=CONTEXT_LIMIT, max_bytes=None):
    mem = memory(state)
    last = state.get("last_test")
    editable = state["spec"]["allowed_files"]
    body = {
        "request": state["spec"]["request"],
        "plan_recorded": bool(state["plan"]),
        "editable": editable,
        "read_only": [p for p in decoded if p not in editable][:32],
        "file_count": len(decoded),
        "plan": state["plan"],
        "progress": mem["done"],
        "failed_attempts": mem["failed"],
        "last_patch": mem["last_patch"],
        "last_result": compact_result(state["observations"][-1] if state["observations"] else None),
        "last_test": ({"status": last["status"], "count": last.get("count"), "reason": last.get("reason"),
                       "for_current_code": last.get("revision") == state.get("revision"),
                       "failures": test_digest(last.get("output", ""))} if last else None),
        "file_views": {},
        "warning": mem["warning"],
        "next": next_step(state),
        "remaining_steps": state["spec"]["budget"]["max_steps"] - state["steps"],
    }
    views = file_views(state, decoded)
    # The last explicitly read source window takes priority over older files.
    recent = state["observations"][-1] if state["observations"] else {}
    result = recent.get("result") or {}
    name = result.get("path") if isinstance(result, dict) else None
    if name in views:
        views = {name: views.pop(name), **views}
    selected = select(body, views, max_chars=max_chars, max_bytes=max_bytes)
    # Keep the actionable next step last, rather than repeating a stale plan there.
    next_action = selected.pop("next")
    return {**selected, "next": next_action}
