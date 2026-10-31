import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "usage_evidence.py"
spec = importlib.util.spec_from_file_location("usage_evidence", SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


def events(thread="t1", counts=None):
    return [
        {"type": "thread.started", "thread_id": thread},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "PRIVATE_BODY"}},
        {
            "type": "turn.completed",
            "usage": counts
            if counts is not None
            else {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 10,
                "reasoning_output_tokens": 3,
            },
        },
    ]


class UsageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = {
            "format": "role-ai-codex-usage-v1",
            "client_version": "codex-cli 0.164.0",
            "protocol_sha256": "a" * 64,
            "approach": "codex_direct",
            "expected_sessions": {"definition": 1, "execution": 1, "review": 1},
            "sessions": [],
        }

    def add(self, phase, rows=None, exit_code=0, name=None):
        name = name or f"event-{len(self.manifest['sessions'])}.jsonl"
        data = (
            b"\n".join(
                json.dumps(row).encode() for row in (rows if rows is not None else events(name))
            )
            + b"\n"
        )
        (self.root / name).write_bytes(data)
        self.manifest["sessions"].append(
            {
                "phase": phase,
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "exit_code": exit_code,
            }
        )
        return data

    def summarize(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        return usage.summarize(self.root, "manifest.json")

    def test_complete_declared_phases_preserve_overlapping_categories(self):
        for phase in self.manifest["expected_sessions"]:
            self.add(phase)
        result = self.summarize()
        self.assertTrue(result["measurement_complete"])
        totals = result["declared_phase_category_totals"]
        self.assertEqual(totals["input_tokens"], 300)
        self.assertEqual(totals["cached_input_tokens"], 120)
        self.assertEqual(totals["output_tokens"], 30)
        self.assertEqual(totals["reasoning_output_tokens"], 9)
        self.assertIsNone(totals["cache_write_input_tokens"])
        self.assertNotIn("total_tokens", totals)
        self.assertFalse(result["source_authenticated"])
        self.assertFalse(result["full_cost_scope_verified"])
        self.assertFalse(result["model_identity_verified"])
        self.assertIsNone(result["token_reduction"])
        self.assertNotIn("PRIVATE_BODY", json.dumps(result))

    def test_local_delegate_requires_definition_and_review(self):
        self.manifest.update(
            approach="local_delegate", expected_sessions={"definition": 1, "review": 1}
        )
        self.add("definition")
        self.assertFalse(self.summarize()["measurement_complete"])
        self.add("review")
        self.assertTrue(self.summarize()["measurement_complete"])

    def test_partial_scope_preserves_subtotal_but_no_complete_total(self):
        self.add("execution")
        result = self.summarize()
        self.assertFalse(result["declared_phase_coverage_complete"])
        self.assertIsNone(result["declared_phase_category_totals"])
        self.assertEqual(result["reported_category_subtotals"]["input_tokens"], 100)

    def test_no_sessions_never_means_zero_tokens(self):
        result = self.summarize()
        self.assertTrue(all(v is None for v in result["reported_category_subtotals"].values()))
        self.assertFalse(result["measurement_complete"])

    def test_multiple_expected_fresh_sessions_are_counted_exactly(self):
        self.manifest["expected_sessions"]["execution"] = 2
        for phase in ["definition", "execution", "execution", "review"]:
            self.add(phase)
        self.assertTrue(self.summarize()["measurement_complete"])
        self.add("review")
        self.assertFalse(self.summarize()["measurement_complete"])

    def test_required_and_optional_unknown_counts(self):
        counts = {"input_tokens": 0, "output_tokens": 0}
        report, _ = usage.parse_session(
            json.dumps(events(counts=counts)[0]).encode()
            + b"\n"
            + b"\n".join(json.dumps(e).encode() for e in events(counts=counts)[1:]),
            0,
        )
        self.assertEqual(report["reported_categories"]["input_tokens"], 0)
        self.assertIsNone(report["reported_categories"]["cached_input_tokens"])
        for value in [-1, True, 1.5, "SECRET_VALUE", None, 10**12 + 1]:
            with self.subTest(value_type=type(value).__name__):
                rows = events(counts={"input_tokens": value, "output_tokens": 1})
                with self.assertRaises(usage.EvidenceError):
                    usage.parse_session(b"\n".join(json.dumps(e).encode() for e in rows), 0)
        for bad in [
            {"output_tokens": 3},
            {"input_tokens": 3},
            {"input_tokens": 3, "output_tokens": 1, "total": 4},
            {"input_tokens": 3, "output_tokens": 1, "cached_input_tokens": 4},
        ]:
            with self.subTest(keys=list(bad)):
                with self.assertRaises(usage.EvidenceError):
                    usage.parse_session(
                        b"\n".join(json.dumps(e).encode() for e in events(counts=bad)), 0
                    )

    def test_duplicate_truncated_nested_and_nonfinite_json_rejected(self):
        for raw in [
            b'{"type":"turn.completed","type":"error"}',
            b'{"type":',
            b"NaN",
            b'{"type":"turn.completed","usage":{"input_tokens":1,"input_tokens":2}}',
        ]:
            with self.subTest(raw_length=len(raw)):
                with self.assertRaises(usage.EvidenceError):
                    usage.parse_session(raw, 0)

    def test_missing_duplicate_reordered_and_trailing_turns_rejected(self):
        base = events()
        variants = [
            base[:-1],
            base + [base[-1]],
            [base[1], base[0], *base[2:]],
            base + [{"type": "item.completed"}],
            [{"type": "turn.started"}, *base],
        ]
        for rows in variants:
            with self.subTest(types=[r["type"] for r in rows]):
                with self.assertRaises(usage.EvidenceError):
                    usage.parse_session(b"\n".join(json.dumps(e).encode() for e in rows), 0)

    def test_nonzero_unknown_exit_failure_or_unknown_event_prevents_complete(self):
        for code, extra in [
            (7, None),
            (None, None),
            (0, "error"),
            (0, "turn.failed"),
            (0, "future.event"),
        ]:
            with self.subTest(code=code, event=extra):
                rows = events()
                if extra:
                    rows.insert(-1, {"type": extra, "message": "PRIVATE_ERROR"})
                report, _ = usage.parse_session(
                    b"\n".join(json.dumps(e).encode() for e in rows), code
                )
                self.assertFalse(report["measurement_complete"])
                self.assertEqual(report["reported_categories"]["input_tokens"], 100)
                self.assertNotIn("PRIVATE_ERROR", json.dumps(report))

    def test_warning_item_is_reported_without_copying_message(self):
        rows = events()
        rows.insert(
            1, {"type": "item.completed", "item": {"type": "error", "message": "PRIVATE_WARNING"}}
        )
        report, _ = usage.parse_session(b"\n".join(json.dumps(e).encode() for e in rows), 0)
        self.assertEqual(report["warning_items"], 1)
        self.assertTrue(report["measurement_complete"])
        self.assertNotIn("PRIVATE_WARNING", json.dumps(report))

    def test_agent_body_cannot_inject_usage(self):
        rows = events()
        rows[2]["item"]["text"] = json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 999999}}
        )
        self.add("execution", rows)
        self.assertEqual(self.summarize()["reported_category_subtotals"]["input_tokens"], 100)

    def test_source_or_thread_cannot_be_counted_twice(self):
        self.add("definition")
        original = copy.deepcopy(self.manifest)
        self.manifest["sessions"].append(copy.deepcopy(self.manifest["sessions"][0]))
        with self.assertRaises(usage.EvidenceError):
            self.summarize()
        self.manifest = original
        first = self.manifest["sessions"][0]
        self.add("execution", events(first["path"]))
        with self.assertRaises(usage.EvidenceError):
            self.summarize()
        self.manifest["sessions"].pop()
        self.add(
            "execution",
            events(first["path"], {"input_tokens": 200, "output_tokens": 2}),
            name="different.jsonl",
        )
        with self.assertRaises(usage.EvidenceError):
            self.summarize()

    def test_source_hash_mismatch_rejected(self):
        self.add("execution")
        self.manifest["sessions"][0]["sha256"] = "f" * 64
        with self.assertRaises(usage.EvidenceError):
            self.summarize()

    def test_invalid_manifest_shapes_and_counts_rejected(self):
        original = copy.deepcopy(self.manifest)
        variants = [
            dict(approach=[]),
            dict(client_version="codex-cli 99.0.0"),
            dict(expected_sessions={"execution": 1}),
            dict(expected_sessions={"definition": True, "execution": 1, "review": 1}),
            dict(expected_sessions={"definition": 128, "execution": 128, "review": 128}),
            dict(protocol_sha256="SECRET_PROTOCOL"),
            dict(sessions={}),
            dict(format="unknown"),
        ]
        for patch in variants:
            with self.subTest(keys=list(patch)):
                self.manifest = {**original, **patch}
                with self.assertRaises(usage.EvidenceError):
                    self.summarize()

    def test_escaping_paths_are_rejected(self):
        self.add("execution")
        for path in [
            "../outside.jsonl",
            "/absolute.jsonl",
            "C:/file.jsonl",
            "logs\\file.jsonl",
            "a//b.jsonl",
            "./event.jsonl",
            ".codex/events.jsonl",
            "a/../b.jsonl",
            "bad\0.jsonl",
            "CON.jsonl",
            "logs/aux.jsonl",
            "COM1.jsonl",
            "a .jsonl",
        ]:
            with self.subTest(path_length=len(path)):
                self.manifest["sessions"][0]["path"] = path
                with self.assertRaises(usage.EvidenceError):
                    self.summarize()

    def test_symlink_file_parent_and_root_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "event.jsonl").write_text("PRIVATE")
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable for this account")
        for base, relative in [(self.root, "link/event.jsonl"), (link, "event.jsonl")]:
            with self.subTest(relative=relative):
                with self.assertRaises(usage.EvidenceError):
                    usage.bounded_read(base, relative)
        file_link = self.root / "linked.jsonl"
        file_link.symlink_to(outside / "event.jsonl")
        with self.assertRaises(usage.EvidenceError):
            usage.bounded_read(self.root, "linked.jsonl")

    def test_size_limits_and_directory_not_accepted(self):
        with self.assertRaises(usage.EvidenceError):
            usage.parse_session(b"x" * (usage.MAX_BYTES + 1), 0)
        with self.assertRaises(usage.EvidenceError):
            usage.parse_session(b"x" * 262145, 0)
        path = self.root / "large.jsonl"
        with path.open("wb") as stream:
            stream.truncate(usage.MAX_BYTES + 1)
        with self.assertRaises(usage.EvidenceError):
            usage.bounded_read(self.root, path.name)
        with self.assertRaises((usage.EvidenceError, OSError)):
            usage.bounded_read(self.root, "missing.jsonl")

    def test_crlf_utf8_raw_hash_and_private_text(self):
        self.add("execution")
        session = self.manifest["sessions"][0]
        rows = events("crlf-thread")
        rows[2]["item"]["text"] = "비공개 원문"
        raw = (
            b"\r\n".join(json.dumps(row, ensure_ascii=False).encode("utf-8") for row in rows)
            + b"\r\n"
        )
        (self.root / session["path"]).write_bytes(raw)
        session["sha256"] = hashlib.sha256(raw).hexdigest()
        report = self.summarize()
        self.assertEqual(report["records"][0]["source_sha256"], session["sha256"])
        self.assertNotIn("비공개", json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["reported_category_subtotals"]["input_tokens"], 100)

    def test_nonregular_sources_fail_without_reading_or_blocking(self):
        directory = self.root / "directory.jsonl"
        directory.mkdir()
        with self.assertRaises((usage.EvidenceError, OSError)):
            usage.bounded_read(self.root, directory.name)
        if hasattr(os, "mkfifo"):
            self.add("execution")
            session = self.manifest["sessions"][0]
            pipe = self.root / "pipe.jsonl"
            os.mkfifo(pipe)
            session["path"] = pipe.name
            (self.root / "manifest.json").write_text(json.dumps(self.manifest))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(self.root)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error_code"], "not_regular_file")

    def test_cli_preserves_unknown_and_never_prints_raw_errors(self):
        self.add("execution")
        self.summarize()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertIsNone(report["declared_phase_category_totals"])
        (self.root / "manifest.json").write_text("PRIVATE_SECRET_TEXT")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = usage.main(["--root", str(self.root)])
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["error_code"], "invalid_json")


if __name__ == "__main__":
    unittest.main()
