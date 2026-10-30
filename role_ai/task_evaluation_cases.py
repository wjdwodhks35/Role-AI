"""Ten synthetic coding tasks. Private oracle stays outside the model/candidate container."""

import copy
import hashlib
import inspect
import json
import random
from dataclasses import dataclass


def visible_documents(records, user_id, project_id, grants):
    result = []
    for row in records:
        if row.get("user_id") != user_id or row.get("archived") is True:
            continue
        if not isinstance(row.get("version"), str) or not row["version"]:
            continue
        native = row.get("project_id") == project_id
        shared = project_id is not None and any(
            g.get("user_id") == user_id
            and g.get("target_project") == project_id
            and g.get("resource_id") == row["id"]
            and g.get("version") == row["version"]
            and g.get("permission") == "read"
            and g.get("revoked") is False
            for g in grants
        )
        if native or shared:
            result.append(row["id"])
    return result


def append_revision(history, content, expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError()
    if [r["revision"] for r in history] != list(range(1, len(history) + 1)):
        raise ValueError()
    if expected_revision != len(history):
        raise ValueError()
    return [dict(row) for row in history] + [
        {"revision": expected_revision + 1, "content": content}
    ]


def can_reuse_conversation(
    bound_project, current_project, bound_generation, current_generation, legacy
):
    return (
        legacy is False
        and type(bound_project) in (str, type(None))
        and type(current_project) in (str, type(None))
        and bound_project == current_project
        and type(bound_generation) is int
        and type(current_generation) is int
        and bound_generation >= 1
        and bound_generation == current_generation
    )


def budget_admission(used, reserve, limit):
    return (
        all(type(n) is int and n >= 0 for n in (used, reserve, limit)) and used + reserve <= limit
    )


def fold_success_runs(lines):
    result = []
    index = 0
    while index < len(lines):
        if lines[index] != "PASS":
            result.append(lines[index])
            index += 1
            continue
        end = index + 1
        while end < len(lines) and lines[end] == "PASS":
            end += 1
        count = end - index
        result.extend(["PASS x" + str(count)] if count >= 3 else ["PASS"] * count)
        index = end
    return result


def deduplicate_ids(values):
    return list(dict.fromkeys(values))


def merge_intervals(intervals):
    if any(
        len(pair) != 2 or any(type(n) is not int for n in pair) or pair[0] > pair[1]
        for pair in intervals
    ):
        raise ValueError()
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def parse_duration(text):
    import re

    if not isinstance(text, str) or not text.strip():
        raise ValueError()
    factors = {"h": 3600, "m": 60, "s": 1}
    seen = set()
    total = 0
    for token in text.split():
        match = re.fullmatch(r"([0-9]+)([hms])", token)
        if not match or match[2] in seen:
            raise ValueError()
        seen.add(match[2])
        total += int(match[1]) * factors[match[2]]
    return total


def topological_order(nodes, edges):
    import heapq

    if len(set(nodes)) != len(nodes):
        raise ValueError()
    graph = {n: set() for n in nodes}
    degree = {n: 0 for n in nodes}
    for source, target in edges:
        if source not in graph or target not in graph:
            raise ValueError()
        if target not in graph[source]:
            graph[source].add(target)
            degree[target] += 1
    ready = [n for n in nodes if degree[n] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        current = heapq.heappop(ready)
        result.append(current)
        for target in sorted(graph[current]):
            degree[target] -= 1
            if degree[target] == 0:
                heapq.heappush(ready, target)
    if len(result) != len(nodes):
        raise ValueError()
    return result


def paginate(items, page, size):
    if type(page) is not int or type(size) is not int or page < 1 or size < 1:
        raise ValueError()
    return {
        "items": items[(page - 1) * size : page * size],
        "total": len(items),
        "pages": (len(items) + size - 1) // size,
    }


@dataclass(frozen=True)
class CodingBenchmarkTask:
    id: str
    category: str
    goal: str
    function: object


TASKS = (
    CodingBenchmarkTask(
        "scope-read",
        "role_ai",
        "Return visible record IDs in input order. Records contain id,user_id,project_id,version,archived. Exclude other users, archived=True and missing/empty version. Native records match project_id exactly (None is personal). Cross-project reads only when target project is not None and a grant matches user_id,target_project,resource_id,exact version,permission=read,revoked=False.",
        visible_documents,
    ),
    CodingBenchmarkTask(
        "memory-revision",
        "role_ai",
        "Append {revision:expected_revision+1,content:content} to copied history. Existing revisions must be exactly 1..len(history). expected_revision must be an exact nonnegative int equal to len(history); otherwise raise ValueError. Preserve all old rows.",
        append_revision,
    ),
    CodingBenchmarkTask(
        "conversation-bound",
        "role_ai",
        "Return bool: legacy must be exactly False; project values must each be str or None and equal; both generations must be exact ints (not bool), >=1 and equal. Otherwise deny False.",
        can_reuse_conversation,
    ),
    CodingBenchmarkTask(
        "budget-check",
        "role_ai",
        "Return bool, allow only exact nonnegative int used,reserve,limit (not bool/float/None) and used+reserve<=limit. Unknown/invalid inputs deny False.",
        budget_admission,
    ),
    CodingBenchmarkTask(
        "safe-log-fold",
        "role_ai",
        "Fold contiguous runs of exactly PASS: runs >=3 become a single PASS xN string. Shorter runs stay unchanged. Preserve every other line exactly, including duplicate errors/warnings/blank lines.",
        fold_success_runs,
    ),
    CodingBenchmarkTask(
        "stable-dedup",
        "small_project",
        "Return unique strings preserving first appearance. Inputs are a list of strings; equality is exact and case-sensitive.",
        deduplicate_ids,
    ),
    CodingBenchmarkTask(
        "merge-intervals",
        "small_project",
        "Return sorted merged [start,end] lists; touching intervals merge. Input pairs must have two exact ints (not bool) and start<=end or raise ValueError. Empty list returns [].",
        merge_intervals,
    ),
    CodingBenchmarkTask(
        "duration-parser",
        "small_project",
        "Parse whitespace-separated nonnegative ASCII integer tokens ending h/m/s, in any order. Sum seconds. Duplicate units, empty/nonstring input, negative/fractional values, unknown/uppercase units raise ValueError.",
        parse_duration,
    ),
    CodingBenchmarkTask(
        "stable-topology",
        "small_project",
        "Given unique string nodes and directed [source,target] edges, return topological order choosing lexicographically smallest available node at every step. Ignore duplicate edges. Duplicate nodes/unknown nodes/cycles raise ValueError.",
        topological_order,
    ),
    CodingBenchmarkTask(
        "pagination",
        "small_project",
        "Return {items:page slice,total:len(items),pages:ceil(len/size)}. page is 1-based; page/size must be exact positive ints (not bool) else ValueError. Beyond end returns empty items; empty input has pages=0.",
        paginate,
    ),
)


def task(task_id):
    for item in TASKS:
        if item.id == task_id:
            return item
    raise ValueError("Unknown trusted task ID")


def arguments(task_id, seed=0):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Invalid evaluation seed")
    rng = random.Random(seed ^ int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16))
    n = rng.randint(3, 8)

    def record(ident, owner="u", project="a", version="v1", archived=False):
        return dict(id=ident, user_id=owner, project_id=project, version=version, archived=archived)

    def grant(version="v1", revoked=False):
        return dict(
            user_id="u",
            target_project="b",
            resource_id="doc",
            version=version,
            permission="read",
            revoked=revoked,
        )

    data = {
        "scope-read": (
            [[[record("doc")], "u", "a", []], [[record("p", project=None)], "u", None, []]],
            [
                [[record("doc")], "u", "b", [grant()]],
                [[record("doc", version="v2")], "u", "b", [grant()]],
                [[record("doc")], "u", "b", [grant(revoked=True)]],
                [
                    [
                        record("x", owner="other"),
                        record("arch", archived=True),
                        record("private", project=None),
                    ],
                    "u",
                    "a",
                    [],
                ],
            ],
        ),
        "memory-revision": (
            [[[], "first", 0], [[{"revision": 1, "content": "old"}], "new", 1]],
            [
                [[], "x", True],
                [[{"revision": 1, "content": "old"}], "x", 0],
                [[{"revision": 2, "content": "gap"}], "x", 1],
                [[{"revision": i, "content": str(i)} for i in range(1, n + 1)], "next", n],
            ],
        ),
        "conversation-bound": (
            [["a", "a", 1, 1, False], ["a", "b", 1, 1, False]],
            [
                [None, None, n, n, False],
                ["a", "a", True, 1, False],
                ["a", "a", n, n + 1, False],
                ["a", "a", n, n, True],
            ],
        ),
        "budget-check": (
            [[2, 3, 5], [2, 4, 5]],
            [[None, 0, 5], [True, 0, 2], [-1, 1, 5], [n, 0, n]],
        ),
        "safe-log-fold": (
            [[["PASS"] * 3], [["FAIL", "FAIL"]]],
            [
                [["PASS"] * 2],
                [["PASS"] * n + ["WARNING", "WARNING"]],
                [["PASS", "ERROR", "PASS", "PASS"]],
                [["", "PASS", "PASS", "PASS", "Traceback"]],
            ],
        ),
        "stable-dedup": (
            [[["b", "a", "b"]], [[]]],
            [[["A", "a", "A"]], [["한글", "한글", "x"]], [["x"] * n], [["c", "b", "a", "c", "b"]]],
        ),
        "merge-intervals": (
            [[[[1, 3], [2, 5]]], [[]]],
            [[[[3, 4], [1, 3]]], [[[-n, 0], [0, n], [n + 2, n + 3]]], [[[2, 1]]], [[[True, 2]]]],
        ),
        "duration-parser": (
            [["1h 2m 3s"], ["0s"]],
            [["2m 1h"], ["1m 2m"], ["1.5h"], [str(n) + "h 7s"]],
        ),
        "stable-topology": (
            [[["a", "b"], [["a", "b"]]], [[], []]],
            [
                [["b", "a", "c"], [["a", "c"], ["a", "c"]]],
                [["a", "b"], [["a", "b"], ["b", "a"]]],
                [["a"], [["a", "missing"]]],
                [["d", "c", "b", "a"], [["b", "d"], ["a", "d"]]],
            ],
        ),
        "pagination": (
            [[[1, 2, 3], 1, 2], [[], 1, 3]],
            [[list(range(n)), 2, 2], [[1], 3, 2], [[1], True, 2], [[1], 1, 0]],
        ),
    }
    return copy.deepcopy(data[task_id])


def expected_observation(function, args):
    copied = copy.deepcopy(args)
    try:
        value = {"value": function(*copied)}
    except Exception as error:
        value = {"error": type(error).__name__}
    return {**value, "arguments_after": copied}


def private_cases(task_id, seed=0):
    definition = task(task_id)
    public, hidden = arguments(task_id, seed)
    return [(args, expected_observation(definition.function, args)) for args in public + hidden]


def reference_source(task_id):
    return inspect.getsource(task(task_id).function)


def task_digest(task_id, seed=0):
    definition = task(task_id)
    value = {
        "id": definition.id,
        "goal": definition.goal,
        "seed": seed,
        "cases": private_cases(task_id, seed),
        "oracle": reference_source(task_id),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def public_task(task_id, seed=0):
    definition = task(task_id)
    public, _ = arguments(task_id, seed)
    signature = str(inspect.signature(definition.function))
    stub = "def " + definition.function.__name__ + signature + ":\n    raise NotImplementedError\n"
    tests = (
        "import unittest\nfrom solution import "
        + definition.function.__name__
        + "\n\nclass PublicTests(unittest.TestCase):\n"
    )
    for index, args in enumerate(public):
        observed = expected_observation(definition.function, args)
        tests += "    def test_case_" + str(index) + "(self):\n"
        if "error" in observed:
            tests += (
                "        with self.assertRaises("
                + observed["error"]
                + "):\n            "
                + definition.function.__name__
                + "(*"
                + repr(args)
                + ")\n"
            )
        else:
            tests += (
                "        self.assertEqual("
                + definition.function.__name__
                + "(*"
                + repr(args)
                + "), "
                + repr(observed["value"])
                + ")\n"
            )
    return {
        "id": definition.id,
        "category": definition.category,
        "goal": definition.goal
        + " Do not mutate input arguments. Use Python standard library only.",
        "files": {"solution.py": stub, "test_public.py": tests},
        "task_digest": task_digest(task_id, seed),
        "public_cases": len(public),
    }
