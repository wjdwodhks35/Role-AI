"""Deterministic context budgets. Character counts are never called measured tokens.

For byte-level tokenizers UTF-8 bytes conservatively bound text tokens. Framing,
chat control tokens and the action contract get a separate reserve. Actual counts
come only from the inference response. No tokenizer/model download is required.
"""

import json


class ContextBudgetError(ValueError):
    code = "context_budget_exhausted"


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def input_bound(messages):
    return sum(len(m["content"].encode("utf-8")) for m in messages) + 256 + 32 * len(messages)


def user_bytes(system, num_ctx, output_tokens):
    available = (
        num_ctx
        - output_tokens
        - input_bound([{"role": "system", "content": system}, {"role": "user", "content": ""}])
    )
    if available <= 0:
        raise ContextBudgetError(
            "System contract and output reserve exceed context; approve a larger num_ctx."
        )
    return available


def select(body, views, *, max_chars=16000, max_bytes=None):
    """Keep the goal and current state; optional fields and exact source windows are selected.

    Omitted data stays in the checkpoint. No source is paraphrased, normalized or
    spliced. Every source window states its offset and next offset.
    """

    def fits(value):
        raw = encode(value)
        return len(raw) <= max_chars and (max_bytes is None or len(raw.encode()) <= max_bytes)

    optional = {
        k: body.pop(k)
        for k in ("plan", "progress", "failed_attempts", "last_patch", "last_result", "read_only")
    }
    last = body.get("last_test")
    failures = last.pop("failures", None) if last else None
    body["context_policy"] = {
        "omitted": [],
        "recovery": "Read omitted source at explicit offsets; never guess patch text.",
    }
    body["file_views"] = {}
    if not fits(body):
        raise ContextBudgetError(
            "Goal and current state cannot fit; approve a larger num_ctx or a smaller task."
        )
    for key in ("last_patch", "last_result", "plan", "failed_attempts", "progress", "read_only"):
        value = optional[key]
        if key in {"progress", "failed_attempts"}:
            value = list(dict.fromkeys(value))
        body[key] = value
        if not fits(body):
            body[key] = [] if isinstance(value, list) else None
            body["context_policy"]["omitted"].append(key)
    if last and failures:
        last["failures"] = failures
        if not fits(body):
            last.pop("failures")
            body["context_policy"]["omitted"].append("test_log")
    # Reserve room for explicit omission notices before adding verbatim file windows.
    for name, view in views.items():
        candidate = dict(view)
        body["file_views"][name] = candidate
        if fits(body):
            continue
        body["file_views"].pop(name)
        text = view.get("text")
        if text:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                candidate = {
                    **view,
                    "text": text[:mid],
                    "next_offset": view.get("offset", 0) + mid,
                    "truncated": True,
                }
                body["file_views"][name] = candidate
                if fits(body):
                    lo = mid
                else:
                    hi = mid - 1
            body["file_views"].pop(name, None)
            if lo:
                body["file_views"][name] = {
                    **view,
                    "text": text[:lo],
                    "next_offset": view.get("offset", 0) + lo,
                    "truncated": True,
                }
    # Notices themselves are budgeted; fail explicitly instead of returning oversized JSON.
    if not fits(body):
        raise ContextBudgetError("Context recovery metadata cannot fit; approve a larger num_ctx.")
    return body
