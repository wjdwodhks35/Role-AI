"""Real disposable Git repositories; no model, Docker, network or submitted-code execution."""

import hashlib
import os
import subprocess
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from role_ai.coding_tasks import (
    CodingBoundaryError, CodingBudget, CodingTaskSpec, FileChange, change_digest, task_digest,
)
from role_ai.coding_workspace import CodingWorkspace, MAX_FILE_BYTES, git


def command(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={"PATH": os.environ["PATH"], "HOME": str(repo.parent),
             "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    ).stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    command(repo, "init", "-q")
    command(repo, "config", "user.email", "fixture@example.test")
    command(repo, "config", "user.name", "fixture")
    (repo / "answer.py").write_text("answer = 41\n")
    (repo / "README.md").write_text("fixture\n")
    command(repo, "add", ".")
    command(repo, "commit", "-qm", "fixture")
    return repo


def spec(repo, **updates):
    values = dict(task_id="task-1", user_id="owner-1", project_id="project-1",
                  repository_id="registered-fixture", base_commit=command(repo, "rev-parse", "HEAD"),
                  allowed_files=("answer.py", "new.py", "README.md", "empty.py"))
    values.update(updates)
    return CodingTaskSpec(**values)


def workspace(repo, **updates):
    return CodingWorkspace(repo, repo.parent / "private-workspaces", spec(repo, **updates))


def update(path="answer.py", before="answer = 41\n", after="answer = 42\n"):
    return FileChange(path=path, expected_sha256=hashlib.sha256(before.encode()).hexdigest(),
                      content=after)


def prepare(ws):
    return ws.prepare(task_digest(ws.spec))


def apply(ws, changes):
    return ws.apply(changes, change_digest(ws.spec, changes))


def test_real_worktree_patch_and_source_preservation(repository):
    # Dirty and untracked source work remains untouched and is never copied to the task.
    (repository / "answer.py").write_text("owner edit\n")
    (repository / "untracked.txt").write_text("owner work\n")
    original = command(repository, "status", "--porcelain")
    head = command(repository, "rev-parse", "HEAD")
    branches = command(repository, "show-ref", "--heads")
    ws = workspace(repository)
    event = prepare(ws)
    assert event["status"] == "ready" and not event["executed"]
    assert event["requires_approval"]  # Preparing a worktree does not approve a patch.
    assert event["tests_state"] == "not_run" and event["progress"] is None
    assert event["heartbeat"] is None and event["resource_usage"] is None
    assert ws.path.is_dir() and not (ws.path / "untracked.txt").exists()
    assert command(ws.path, "status", "--porcelain") == ""
    changes = (update(), FileChange(path="new.py", content="new = True\n"),
               update("README.md", "fixture\n", None), FileChange(path="empty.py", content=""))
    result = apply(ws, changes)
    assert result["status"] == "changed" and result["tests_state"] == "not_run"
    assert not result["requires_approval"]
    assert (ws.path / "answer.py").read_text() == "answer = 42\n"
    assert not (ws.path / "README.md").exists()
    assert (ws.path / "empty.py").read_bytes() == b""
    assert "new file mode 100644" in result["diff"]
    # A separate clean checkout can consume the produced artifact, including empty files.
    patch = repository.parent / "candidate.diff"
    patch.write_text(result["diff"])
    command(repository, "worktree", "add", "--detach", str(repository.parent / "verify"), head)
    command(repository.parent / "verify", "apply", "--check", str(patch))
    command(repository.parent / "verify", "apply", str(patch))
    assert (repository.parent / "verify" / "empty.py").read_bytes() == b""
    assert command(repository, "status", "--porcelain") == original
    assert command(repository, "rev-parse", "HEAD") == head
    assert command(repository, "show-ref", "--heads") == branches
    with pytest.raises(CodingBoundaryError):
        apply(ws, (update(),))
    ws.close()
    assert not ws.path.exists() and ws.event()["status"] == "closed"
    assert (repository / "untracked.txt").read_text() == "owner work\n"


@pytest.mark.parametrize("path", [
    "../escape.py", "/tmp/escape.py", "a//b.py", "a/./b.py", "a\\b.py", "a\nb.py",
    ".git/config", ".env", "nested/.env.local", "key.pem", "data/file.py",
    ".github/workflows/ci.yml", "compose.yaml", "config/agents/example.json",
    "Dockerfile", "AGENTS.md", "role_ai/security.py", "role_ai/assistant.py",
])
def test_forbidden_paths_cannot_be_approved(repository, path):
    with pytest.raises((ValidationError, CodingBoundaryError)):
        spec(repository, allowed_files=(path,))
    with pytest.raises((ValidationError, CodingBoundaryError)):
        FileChange(path=path, content="pass")


def test_contracts_reject_coercion_unknown_fields_and_mutation(repository):
    for values in ({"max_files": True}, {"max_retries": 4}, {"timeout_seconds": "10"},
                   {"command": "sh"}, {"max_patch_bytes": 999999}):
        with pytest.raises(ValidationError):
            CodingBudget(**values)
    with pytest.raises(ValidationError):
        spec(repository, allowed_files=("answer.py", "answer.py"))
    with pytest.raises(ValidationError):
        spec(repository, base_commit="HEAD")
    task = spec(repository)
    with pytest.raises(ValidationError):
        task.user_id = "different"


def test_approval_binds_owner_project_commit_scope_budget_and_content(repository):
    ws = workspace(repository)
    with pytest.raises(CodingBoundaryError):
        ws.prepare("0" * 64)
    assert not ws.path.exists()
    for field, value in [("user_id", "other"), ("project_id", "other"),
                         ("repository_id", "other"), ("task_id", "other"),
                         ("allowed_files", ("answer.py",)),
                         ("budget", CodingBudget(max_files=1))]:
        assert task_digest(spec(repository, **{field: value})) != task_digest(ws.spec)
    prepare(ws)
    changes = (update(),)
    with pytest.raises(CodingBoundaryError):
        ws.apply(changes, change_digest(ws.spec, (update(after="different"),)))
    assert (ws.path / "answer.py").read_text() == "answer = 41\n"
    apply(ws, changes)


@pytest.mark.parametrize("case", ["hash", "scope", "duplicate", "count", "bytes", "noop", "empty"])
def test_invalid_batches_fail_before_any_write(repository, case):
    ws = workspace(repository, budget=CodingBudget(max_files=2, max_patch_bytes=256))
    prepare(ws)
    changes = (update(),)
    if case == "hash":
        changes += (FileChange(path="README.md", expected_sha256="0" * 64, content="new"),)
    elif case == "scope":
        changes += (FileChange(path="outside.py", content="new"),)
    elif case == "duplicate":
        changes += changes
    elif case == "count":
        changes += (FileChange(path="new.py", content="new"), FileChange(path="empty.py", content=""))
    elif case == "bytes":
        changes = (update(after="x" * 257),)
    elif case == "noop":
        changes = (update(after="answer = 41\n"),)
    elif case == "empty":
        changes = ()
    with pytest.raises(CodingBoundaryError):
        apply(ws, changes)
    assert (ws.path / "answer.py").read_text() == "answer = 41\n"
    assert ws.status == "ready"


@pytest.mark.parametrize("damage", [
    "symlink", "parent_symlink", "hardlink", "fifo", "unexpected", "deleted", "git_marker",
])
def test_workspace_tampering_is_rejected(repository, damage):
    ws = workspace(repository)
    prepare(ws)
    outside = repository.parent / "outside"
    outside.write_text("protected")
    target = ws.path / "answer.py"
    if damage in {"symlink", "hardlink", "fifo", "deleted"}:
        target.unlink()
        if damage == "symlink":
            target.symlink_to(outside)
        elif damage == "hardlink":
            os.link(outside, target)
        elif damage == "fifo":
            os.mkfifo(target)
    elif damage == "parent_symlink":
        (ws.path / "folder").symlink_to(repository.parent, target_is_directory=True)
    elif damage == "git_marker":
        (ws.path / ".git").write_text("gitdir: /unregistered\n")
    else:
        (ws.path / "unknown.py").write_text("unexpected")
    with pytest.raises(CodingBoundaryError):
        apply(ws, (update(),))
    assert outside.read_text() == "protected"
    if damage == "git_marker":
        with pytest.raises(CodingBoundaryError):
            ws.close()
        assert ws.path.exists()


@pytest.mark.parametrize("kind", ["secret", "symlink", "binary", "large"])
def test_unsupported_repository_rejected_without_workspace(repository, kind):
    if kind == "secret":
        (repository / ".env").write_text("SECRET=fixture")
    elif kind == "symlink":
        (repository / "link").symlink_to("answer.py")
    else:
        (repository / "file").write_bytes(b"\0" if kind == "binary" else b"x" * (MAX_FILE_BYTES + 1))
    command(repository, "add", ".")
    command(repository, "commit", "-qm", "unsupported")
    ws = workspace(repository)
    with pytest.raises(CodingBoundaryError):
        prepare(ws)
    assert not ws.path.exists()


def test_checkout_hooks_filters_and_inherited_git_config_never_execute(repository, monkeypatch):
    marker = repository.parent / "must-not-run"
    script = repository.parent / "marker.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    # A malicious local filter and hook are installed AFTER creating the trusted fixture commit.
    (repository / ".gitattributes").write_text("answer.py filter=untrusted\n")
    command(repository, "add", ".gitattributes")
    command(repository, "commit", "-qm", "attributes")
    hook = repository / ".git/hooks/post-checkout"
    hook.write_text(script.read_text())
    hook.chmod(0o755)
    command(repository, "config", "filter.untrusted.smudge", str(script))
    command(repository, "config", "filter.untrusted.clean", str(script))
    command(repository, "config", "core.fsmonitor", str(script))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(repository / ".git/hooks"))
    ws = CodingWorkspace(repository, repository.parent / "private-workspaces", CodingTaskSpec(
        task_id="task-1", user_id="owner", project_id="project", repository_id="registered",
        base_commit=git(repository, "rev-parse", "HEAD").decode().strip(),
        allowed_files=("answer.py",),
    ))
    prepare(ws)
    apply(ws, (update(),))
    ws.close()
    assert not marker.exists()


def test_deadline_existing_paths_and_private_root(repository, monkeypatch):
    ws = workspace(repository)
    monkeypatch.setattr("role_ai.coding_workspace.time.monotonic", lambda: ws.started + 301)
    with pytest.raises(CodingBoundaryError, match="시간"):
        prepare(ws)
    assert not ws.path.exists()
    monkeypatch.undo()
    ws.path.mkdir()
    (ws.path / "owner.txt").write_text("keep")
    with pytest.raises(CodingBoundaryError):
        prepare(ws)
    assert (ws.path / "owner.txt").read_text() == "keep"
    ws.root.chmod(0o755)
    with pytest.raises(CodingBoundaryError):
        workspace(repository)
    with pytest.raises(CodingBoundaryError):
        CodingWorkspace(repository, repository / "nested", spec(repository))
    assert not (repository / "nested").exists()


def test_no_final_newline_artifact_is_applyable(repository):
    ws = workspace(repository)
    prepare(ws)
    result = apply(ws, (update(after="answer = 42"),))
    patch = repository.parent / "no-newline.diff"
    patch.write_text(result["diff"])
    command(repository, "apply", "--check", str(patch))
    assert "\\ No newline at end of file" in result["diff"]


def test_git_errors_do_not_expose_stderr_or_contents(repository):
    with pytest.raises(CodingBoundaryError) as error:
        git(repository, "cat-file", "blob", "sensitive-invalid-revision")
    assert "sensitive" not in str(error.value)


def test_partial_write_failure_is_blocked_and_source_untouched(repository, monkeypatch):
    ws = workspace(repository)
    prepare(ws)
    real_open = os.open

    def fail_second(path, flags, *args, **kwargs):
        if Path(path).name == "new.py" and flags & os.O_WRONLY:
            raise OSError("fixture")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("role_ai.coding_workspace.os.open", fail_second)
    with pytest.raises(CodingBoundaryError):
        apply(ws, (update(), FileChange(path="new.py", content="new")))
    assert ws.status == "failed"
    assert (repository / "answer.py").read_text() == "answer = 41\n"
    with pytest.raises(CodingBoundaryError):
        apply(ws, (update(),))


def test_concurrent_application_is_rejected(repository, monkeypatch):
    ws = workspace(repository)
    prepare(ws)
    entered, release = threading.Event(), threading.Event()
    original = ws.verify_tree

    def wait_before_write():
        entered.set()
        assert release.wait(5)
        original()

    monkeypatch.setattr(ws, "verify_tree", wait_before_write)
    errors = []

    def first():
        try:
            apply(ws, (update(),))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(CodingBoundaryError, match="진행 중"):
            apply(ws, (update(after="answer = 99\n"),))
        with pytest.raises(CodingBoundaryError):
            ws.close()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert (ws.path / "answer.py").read_text() == "answer = 42\n"
