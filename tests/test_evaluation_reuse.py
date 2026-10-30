"""Selective PR31 storage diagnostics, no benchmark/model or host executor imports."""

import pytest
from role_ai.coding_jobs import CheckpointWriteError, CodingJobError, ModelOptions
from role_ai.task_errors import failure_code
from role_ai.task_resources import failure_kind
from role_ai.coding_tasks import CodingBoundaryError

pytest_plugins = ("test_coding_runtime",)


def test_checkpoint_write_error_survives_file_lock_and_keeps_previous_revision(coding, monkeypatch):
    _, store, initial, *_ = coding
    directory, state = store.load("owner", initial["id"])
    before = (directory / "checkpoint.json").read_bytes()
    state["steps"] = 99

    def fail(*args):
        raise OSError("private-file-and-secret")

    monkeypatch.setattr("role_ai.coding_jobs.os.replace", fail)
    with pytest.raises(CheckpointWriteError) as observed:
        with store.lock(directory):
            store.save(directory, state)
    assert failure_code(observed.value) == "checkpoint_io"
    assert failure_kind(observed.value) == ("checkpoint_io", False)
    assert "private-file" not in str(observed.value)
    assert (directory / "checkpoint.json").read_bytes() == before
    assert not list(directory.glob(".checkpoint-*"))


def test_suppressed_permission_cause_is_bounded_and_message_free():
    try:
        try:
            raise PermissionError("private credential path")
        except PermissionError:
            raise CodingJobError("safe wrapper") from None
    except CodingJobError as error:
        assert failure_code(error) == "file_permission"
        assert failure_kind(error) == ("file_permission", False)
    cycle = RuntimeError("private")
    cycle.__context__ = cycle
    assert failure_code(cycle) == "internal_exception"


@pytest.mark.parametrize(
    "error,expected",
    [
        (CodingBoundaryError("private"), "boundary_violation"),
        (CodingJobError("private"), "policy_violation"),
        (ValueError("private"), "internal_exception"),
    ],
)
def test_selected_diagnostics_do_not_become_execution_authority(error, expected):
    assert failure_code(error) == expected
    assert type(failure_code(error)) is str


def test_automatic_tests_and_context_overrides_are_not_imported():
    assert "automatic_tests" not in ModelOptions.model_fields
    from role_ai.coding_agent import CodingAgent

    assert not hasattr(CodingAgent, "run_automatic_test")


def test_runtime_storage_failure_does_not_replay_or_start_callback(tmp_path, monkeypatch):
    # The common runtime storage adapter has the same typed fail-closed error.
    from role_ai.task_resources import TaskRuntime
    from types import SimpleNamespace

    class Files:
        def atomic(self, *_):
            raise OSError("secret")

    runtime = object.__new__(TaskRuntime)
    runtime.lifecycle = SimpleNamespace(store=SimpleNamespace(files=Files()))
    monkeypatch.setattr(runtime, "path", lambda *_: tmp_path / "runtime.json")
    with pytest.raises(CheckpointWriteError):
        runtime.save(SimpleNamespace(user_id="u", task_id="t"), {})
    assert not (tmp_path / "runtime.json").exists()


def test_coding_control_reports_storage_failure_without_success_or_private_text(
    coding, monkeypatch
):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from role_ai.main import create_app
    from role_ai.coding_jobs import CodingJobStore

    settings, _, initial, *_ = coding
    settings.bootstrap_email = "owner@example.test"
    settings.bootstrap_password = SecretStr("fixture-password")
    settings.allowed_origins = ["http://testserver"]

    def fail(*args, **kwargs):
        raise CheckpointWriteError("private-path-and-token")

    monkeypatch.setattr(CodingJobStore, "resume", fail)
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": "fixture-password"}
        ).json()
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        result = client.post(
            "/api/coding/tasks/" + initial["id"] + "/resume", json={"approved": True}
        )
        assert result.status_code == 503
        assert "private-path" not in result.text
        assert "status" not in result.json()
