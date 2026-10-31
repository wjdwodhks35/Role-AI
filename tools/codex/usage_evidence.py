"""Read bounded, local Codex CLI JSONL receipts; never invoke Codex or infer costs.

Phase coverage is relative to the operator's manifest, not authenticated proof of
all work, provider billing, model identity, task success, or execution authority.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_BYTES = 4 * 1024 * 1024
MAX_SESSIONS = 128
CATEGORIES = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
PHASES = {
    "codex_direct": {"definition", "execution", "review"},
    "local_delegate": {"definition", "review"},
}
EVENTS = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "error",
    "item.started",
    "item.updated",
    "item.completed",
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMPONENT = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z")
DEVICES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(10)}


class EvidenceError(ValueError):
    """Only fixed codes may cross the CLI boundary, never raw input or paths."""


def require(condition, code):
    if not condition:
        raise EvidenceError(code)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise EvidenceError("nonfinite_json")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise EvidenceError("invalid_json") from exc


def bounded_read(root, relative):
    require(type(relative) is str and 0 < len(relative) <= 256, "invalid_path")
    parts = PurePosixPath(relative).parts
    require(
        parts
        and not relative.startswith("/")
        and "\\" not in relative
        and ":" not in relative
        and "\0" not in relative
        and "/".join(parts) == relative
        and not any(p in {".", "..", ".git", ".codex", ".ssh", ".aws"} for p in parts),
        "invalid_path",
    )
    require(
        all(
            COMPONENT.fullmatch(p)
            and not p.endswith(".")
            and p.split(".", 1)[0].upper() not in DEVICES
            for p in parts
        ),
        "invalid_path",
    )
    path = root
    for part in (None, *parts):
        if part is not None:
            path = path / part
        require(
            not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(),
            "linked_path",
        )
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0),
    )
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), "not_regular_file")
        require(before.st_size <= MAX_BYTES, "oversized_file")
        data = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(data) <= MAX_BYTES, "oversized_file")
    require(
        (before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_ino, after.st_size, after.st_mtime_ns),
        "file_changed",
    )
    require(path.stat().st_ino == before.st_ino and not path.is_symlink(), "file_changed")
    return data


def parse_session(data, exit_code):
    require(type(exit_code) is int or exit_code is None, "invalid_exit_code")
    require(type(data) is bytes and len(data) <= MAX_BYTES, "oversized_file")
    lines = data.splitlines()
    require(0 < len(lines) <= 16384, "invalid_event_count")
    events = []
    for line in lines:
        require(0 < len(line) <= 262144, "invalid_event_line")
        item = strict_json(line)
        require(type(item) is dict and type(item.get("type")) is str, "invalid_event")
        events.append(item)
    threads = [e for e in events if e["type"] == "thread.started"]
    starts = [e for e in events if e["type"] == "turn.started"]
    completed = [e for e in events if e["type"] == "turn.completed"]
    require(len(threads) == len(starts) == len(completed) == 1, "ambiguous_or_missing_turn")
    thread = threads[0].get("thread_id")
    require(type(thread) is str and 0 < len(thread) <= 256, "invalid_thread")
    require(
        events.index(threads[0]) < events.index(starts[0]) < events.index(completed[0]),
        "invalid_event_order",
    )
    require(events[-1] is completed[0], "trailing_events")
    usage = completed[0].get("usage")
    require(type(usage) is dict and set(usage) <= set(CATEGORIES), "unknown_usage_schema")
    require({"input_tokens", "output_tokens"} <= set(usage), "missing_required_usage")
    for value in usage.values():
        require(type(value) is int and 0 <= value <= 10**12, "invalid_token_count")
    categories = {key: usage.get(key) for key in CATEGORIES}
    if categories["cached_input_tokens"] is not None:
        require(
            categories["cached_input_tokens"] <= categories["input_tokens"], "invalid_cache_count"
        )
    failed = any(e["type"] in {"turn.failed", "error"} for e in events)
    unknown = sum(e["type"] not in EVENTS for e in events)
    warnings = sum(
        e["type"] == "item.completed"
        and type(e.get("item")) is dict
        and e["item"].get("type") == "error"
        for e in events
    )
    return {
        "reported_categories": categories,
        "measurement_complete": exit_code == 0 and not failed and not unknown,
        "failed_event_present": failed,
        "unknown_events": unknown,
        "warning_items": warnings,
        "exit_code_known": exit_code is not None,
    }, thread


def summarize(root, manifest_name):
    root = Path(os.path.abspath(root))
    require(not root.anchor.startswith(("\\\\", "//")), "remote_root")
    for parent in (*reversed(root.parents), root):
        require(
            not parent.is_symlink() and not getattr(parent, "is_junction", lambda: False)(),
            "linked_path",
        )
    manifest_data = bounded_read(root, manifest_name)
    manifest = strict_json(manifest_data)
    require(
        type(manifest) is dict
        and set(manifest)
        == {
            "format",
            "client_version",
            "protocol_sha256",
            "approach",
            "expected_sessions",
            "sessions",
        },
        "invalid_manifest",
    )
    require(manifest["format"] == "role-ai-codex-usage-v1", "unknown_manifest_format")
    require(manifest["client_version"] == "codex-cli 0.164.0", "unreviewed_client_version")
    require(
        type(manifest["protocol_sha256"]) is str and HEX.fullmatch(manifest["protocol_sha256"]),
        "invalid_protocol_identity",
    )
    approach = manifest["approach"]
    require(type(approach) is str and approach in PHASES, "invalid_approach")
    expected = manifest["expected_sessions"]
    require(type(expected) is dict and set(expected) == PHASES[approach], "invalid_expected_phases")
    require(
        all(type(v) is int and 1 <= v <= MAX_SESSIONS for v in expected.values())
        and sum(expected.values()) <= MAX_SESSIONS,
        "invalid_expected_counts",
    )
    sessions = manifest["sessions"]
    require(type(sessions) is list and len(sessions) <= MAX_SESSIONS, "invalid_sessions")
    records, threads, hashes, paths = [], set(), set(), set()
    counts = {phase: 0 for phase in expected}
    for session in sessions:
        require(
            type(session) is dict and set(session) == {"phase", "path", "sha256", "exit_code"},
            "invalid_session",
        )
        phase = session["phase"]
        require(type(phase) is str and phase in expected, "invalid_phase")
        require(type(session["path"]) is str and session["path"].endswith(".jsonl"), "invalid_path")
        require(
            type(session["sha256"]) is str and HEX.fullmatch(session["sha256"]),
            "invalid_source_identity",
        )
        require(session["path"] not in paths, "duplicate_source")
        paths.add(session["path"])
        data = bounded_read(root, session["path"])
        digest = hashlib.sha256(data).hexdigest()
        require(digest == session["sha256"], "source_identity_mismatch")
        require(digest not in hashes, "duplicate_source")
        hashes.add(digest)
        record, thread = parse_session(data, session["exit_code"])
        require(thread not in threads, "duplicate_thread")
        threads.add(thread)
        counts[phase] += 1
        records.append({"phase": phase, "source_sha256": digest, **record})
    coverage = counts == expected
    complete = coverage and all(r["measurement_complete"] for r in records)
    subtotal = {
        key: sum(r["reported_categories"][key] for r in records)
        if records and all(r["reported_categories"][key] is not None for r in records)
        else None
        for key in CATEGORIES
    }
    return {
        "format": "role-ai-codex-usage-summary-v1",
        "manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        "protocol_sha256": manifest["protocol_sha256"],
        "approach": approach,
        "expected_sessions": expected,
        "observed_sessions": counts,
        "declared_phase_coverage_complete": coverage,
        "measurement_complete": complete,
        "reported_category_subtotals": subtotal,
        "declared_phase_category_totals": subtotal if complete else None,
        "model_identity_verified": False,
        "full_cost_scope_verified": False,
        "source_authenticated": False,
        "token_reduction": None,
        "records": records,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Explicit local evidence directory")
    parser.add_argument("--manifest", default="manifest.json", help="Relative manifest filename")
    args = parser.parse_args(argv)
    try:
        report = summarize(args.root, args.manifest)
    except EvidenceError as exc:
        print(json.dumps({"status": "invalid_evidence", "error_code": exc.args[0]}))
        return 2
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "invalid_evidence", "error_code": "unreadable_evidence"}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["measurement_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
