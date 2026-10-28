"""Revisioned private worktrees; submitted commands never run in these directories."""

import difflib
import hashlib
from pathlib import Path
from uuid import uuid4

from role_ai.coding_jobs import CodingJobError, checksum, decode_files, encode_files
from role_ai.coding_tasks import CodingBudget, CodingTaskSpec, FileChange, change_digest, task_digest
from role_ai.coding_workspace import CodingWorkspace, private_directory


def repository(settings, identifier):
    try:
        source = Path(settings.coding_repositories[identifier]).absolute()
    except KeyError:
        raise CodingJobError("Repository is not registered") from None
    root = Path(settings.coding_root).absolute()
    if (source.resolve() != source or source == root or source.is_relative_to(root)
            or root.is_relative_to(source)):
        raise CodingJobError("Repository and task storage must be separate canonical paths")
    return source


def workspace_spec(state):
    return CodingTaskSpec(
        task_id=state["id"], user_id=state["user_id"], project_id=state["spec"]["project_id"],
        repository_id=state["spec"]["repository_id"], base_commit=state["repo_revision"],
        allowed_files=tuple(state["spec"]["allowed_files"]),
        budget=CodingBudget(max_files=state["spec"]["budget"]["max_files"],
                            max_patch_bytes=state["spec"]["budget"]["patch_bytes"], timeout_seconds=300),
    )


def prepare_files(settings, directory, state, changes=None):
    if sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) > 32 * 1024 * 1024:
        raise CodingJobError("Task storage quota exceeded; inspect abandoned worktrees")
    source = repository(settings, state["spec"]["repository_id"])
    if checksum(str(source)) != state["source_hash"]:
        raise CodingJobError("Repository registration changed; new approval required")
    spec = workspace_spec(state)
    # One worktree per patch: previous revisions remain in the atomic checkpoint.
    root = private_directory(directory / ("work-" + uuid4().hex))
    ws = CodingWorkspace(source, root, spec, binary_passthrough=True)
    ws.prepare(task_digest(spec))
    try:
        if state["files"] is None:
            state["modes"] = dict(ws.modes)
        if state["files"] is not None:
            current = decode_files(state["files"])
            for name in set(ws.baseline) - set(current):
                ws.target(name).unlink()
            for name, raw in current.items():
                path = ws.target(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            ws.baseline = current
            ws.modes = {name: ws.modes.get(name, "100644") for name in current}
        if changes is not None:
            typed = []
            for change in changes:
                name = change["path"]
                before = ws.baseline.get(name)
                typed.append(FileChange(path=name, content=change["content"],
                                        expected_sha256=hashlib.sha256(before).hexdigest() if before is not None else None))
            typed = tuple(typed)
            ws.apply(typed, change_digest(spec, typed))
            result = dict(ws.baseline)
            for change in typed:
                if change.content is None:
                    result.pop(change.path)
                else:
                    result[change.path] = change.content.encode()
        else:
            result = dict(ws.baseline)
        encoded = encode_files(result)
        # Validate total snapshot limits before storing or testing it.
        decode_files(encoded)
        return encoded
    finally:
        ws.close()
        root.rmdir()


def cumulative_diff(state):
    before, after = decode_files(state["original"]), decode_files(state["files"])
    patches, names, size = [], [], 0
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        names.append(name)
        size += len(old or b"") + len(new or b"")
        patches.append(f"diff --git a/{name} b/{name}\n")
        if old is None:
            patches.append("new file mode 100644\n")
        if new is None:
            patches.append(f"deleted file mode {state.get('modes', {}).get(name, '100644')}\n")
        for line in difflib.unified_diff(
            (old or b"").decode().splitlines(keepends=True), (new or b"").decode().splitlines(keepends=True),
            fromfile="a/" + name if old is not None else "/dev/null",
            tofile="b/" + name if new is not None else "/dev/null",
        ):
            patches.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    patch = "".join(patches)
    budget = state["spec"]["budget"]
    if len(names) > budget["max_files"] or max(size, len(patch.encode())) > budget["patch_bytes"]:
        raise CodingJobError("Cumulative patch budget exceeded")
    return {"files": names, "diff": patch, "sha256": hashlib.sha256(patch.encode()).hexdigest()}
