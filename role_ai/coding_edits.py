"""Exact-replacement edits with narrow, verifiable normalization. No fuzzy patching."""

import difflib
import re

ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class EditMismatch(ValueError):
    """Recoverable: nothing was changed. `detail` is safe to show to the model."""

    def __init__(self, reason, detail):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def unescape(text):
    return re.sub(r"\\([ntr\"\\])", lambda m: ESCAPES[m.group(1)], text)


def _strip_newline(text):
    for ending in ("\r\n", "\n"):
        if text.endswith(ending):
            return text[:-len(ending)]
    return text


def _candidates(text, old, new):
    """Yield (label, old, new). Each loosening keeps old/new transformed consistently."""
    bases = [("exact", old, new)]
    # Model emitted JSON "\\n" (literal backslash-n) where real newlines were meant.
    if "\n" not in old and re.search(r"\\[nt]", old):
        bases.append(("unescaped", unescape(old), unescape(new)))
    for label, o, n in bases:
        yield label, o, n
        if "\r\n" in text and "\r\n" not in o and "\n" in o:
            yield label + "+crlf", o.replace("\n", "\r\n"), n.replace("\n", "\r\n")
        trimmed = _strip_newline(o)
        if trimmed != o and trimmed:
            # Missing final newline in the file: drop the trailing newline from both sides.
            yield label + "+eof", trimmed, _strip_newline(n) if n.endswith("\n") else n
            if "\r\n" in text and "\r\n" not in trimmed and "\n" in trimmed:
                yield (label + "+crlf+eof", trimmed.replace("\n", "\r\n"),
                       (_strip_newline(n) if n.endswith("\n") else n).replace("\n", "\r\n"))


def excerpt(text, needle, limit=900):
    """Closest region of the current file, so the model can copy exact text."""
    lines = text.splitlines()
    probe = next((line.strip() for line in unescape(needle).splitlines() if line.strip()), "")
    best, score = 0, 0.0
    for number, line in enumerate(lines):
        ratio = difflib.SequenceMatcher(None, probe, line.strip()).ratio() if probe else 0
        if ratio > score:
            best, score = number, ratio
    start = max(0, best - 3)
    window = "\n".join(lines[start:start + 10])
    return {"from_line": start + 1, "text": window[:limit]}


def replace_once(text, old, new):
    """Return (new_text, normalization). Applies only when exactly one location matches."""
    for label, o, n in _candidates(text, old, new):
        count = text.count(o)
        if count == 1:
            return text.replace(o, n, 1), label
        if count > 1:
            raise EditMismatch("ambiguous", {
                "reason": "ambiguous", "matches": count, "normalization": label,
                "hint": "old_text appears more than once. Include more surrounding lines so it is unique.",
            })
    raise EditMismatch("not_found", {
        "reason": "not_found",
        "hint": "old_text was not found. Copy it exactly from the current file (real newlines, same indentation).",
        "closest": excerpt(text, old),
    })


def apply_changes(files, changes, decode=lambda raw: raw.decode("utf-8")):
    """Apply all changes against a working copy; per-path results are merged in order.

    Raises EditMismatch before anything is returned, so a partial failure changes nothing.
    """
    working, normalized, notes = {}, {}, []
    for index, change in enumerate(changes):
        path = change.path
        if change.old_text is not None:
            if path not in working:
                raw = files.get(path)
                working[path] = decode(raw) if raw is not None else None
            if working[path] is None:
                raise EditMismatch("missing_file", {"reason": "missing_file", "change": index, "path": path,
                                                    "hint": "File does not exist; use content to create it."})
            try:
                working[path], label = replace_once(working[path], change.old_text, change.new_text)
            except EditMismatch as exc:
                exc.detail = {"change": index, "path": path, **exc.detail}
                raise
            if label != "exact":
                notes.append({"change": index, "normalization": label})
        else:
            working[path] = change.content
        normalized[path] = working[path]
    return [{"path": p, "content": c} for p, c in normalized.items()], notes
