"""Pure contracts and original Coding boundaries; no live model or Docker execution."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from role_ai.coding_jobs import TaskCreate
from role_ai.coding_tasks import (
    CodingBudget, CodingTaskSpec, FileChange, change_digest, coding_spec_from_task_contract,
    task_contract_from_coding, task_digest,
)
from role_ai.permissions import PermissionGate, PermissionRequest
from role_ai.task_contracts import (
    REQUIRED_STOPS, ResourceBudget, ResourceRef, ReviewPolicy, TaskContract, TaskExecutionState,
    contract_digest,
)


def coding_spec(**updates):
    return CodingTaskSpec(**{
        "task_id": "task-1", "user_id": "owner-1", "project_id": "project-1",
        "repository_id": "repo-1", "base_commit": "a" * 40,
        "allowed_files": ("src/answer.py",), **updates,
    })


def common_contract(**updates):
    original = task_contract_from_coding(
        coding_spec(), goal="Fix answer", acceptance_criteria=("Independent tests pass",),
        policy_version=1, review_policy=ReviewPolicy(policy_id="coding-review", version=1),
    )
    return TaskContract.model_validate({**original.model_dump(), **updates})


@pytest.mark.parametrize("updates", [
    {"schema_version": True}, {"schema_version": 2}, {"policy_version": "1"},
    {"policy_version": 0}, {"approved": True}, {"status": "approved"},
    {"user_id": ""}, {"task_id": "../task"}, {"project_id": None},
    {"scope": "personal"}, {"goal": " \n"}, {"goal": "bad\0text"},
    {"goal": "bad\ud800text"}, {"acceptance_criteria": ()},
    {"acceptance_criteria": ("",)}, {"acceptance_criteria": ("same", "same")},
    {"acceptance_criteria": ("x",) * 17}, {"allowed_tools": ("shell; rm",)},
    {"allowed_tools": ("coding.read", "coding.read")},
    {"permissions": ("admin",)}, {"permissions": ("read", "read")},
    {"stop_conditions": ("cancel_requested",)},
    {"stop_conditions": (*REQUIRED_STOPS, "cancel_requested")},
    {"approval_reference": "approve\nnow"},
])
def test_invalid_contracts_fail_closed(updates):
    with pytest.raises(ValidationError):
        common_contract(**updates)


def test_personal_scope_is_explicit_and_never_a_wildcard():
    personal = common_contract(scope="personal", project_id=None, allowed_resources=())
    assert personal.project_id is None and not personal.allowed_tools and not personal.permissions
    with pytest.raises(ValidationError):
        common_contract(project_id="")
    with pytest.raises(ValueError):
        coding_spec_from_task_contract(personal)


@pytest.mark.parametrize("kind,version,paths", [
    ("repository", "HEAD", ("src/a.py",)), ("document", "latest", ()),
    ("memory", "0", ()), ("memory", "01", ()), ("memory", "1", ("a.py",)),
    ("document", "b" * 64, ("a.py",)), ("repository", "a" * 40, ()),
    ("repository", "a" * 40, ("a.py", "a.py")),
])
def test_resources_require_exact_versions_and_scopes(kind, version, paths):
    with pytest.raises(ValidationError):
        ResourceRef(kind=kind, resource_id="source-1", version=version, paths=paths)


@pytest.mark.parametrize("path", [
    "../escape.py", "/tmp/escape.py", "a//b.py", "a/./b.py", "a\\b.py", ".", "a\nb.py",
    ".env", "nested/.env.local", ".git/config", "data/file.py", "key.pem",
    "compose.yaml", "Dockerfile", "AGENTS.md", "role_ai/security.py", "role_ai/main.py",
])
def test_common_conversion_cannot_bypass_original_coding_paths(path):
    data = common_contract().model_dump(mode="json")
    data["allowed_resources"][0]["paths"] = [path]
    with pytest.raises(ValueError):
        # Generic read references may describe protected files; Coding writes still cannot.
        coding_spec_from_task_contract(TaskContract.model_validate_json(json.dumps(data)))


def test_resource_identity_cannot_rebind_to_multiple_versions():
    first = ResourceRef(kind="memory", resource_id="memory-1", version="1")
    second = ResourceRef(kind="memory", resource_id="memory-1", version="2")
    with pytest.raises(ValidationError):
        common_contract(allowed_resources=(first, second))
    shared = ResourceRef(kind="document", resource_id="document-1", version="b" * 64)
    changed = ResourceRef(kind="document", resource_id="document-1", version="c" * 64)
    assert contract_digest(common_contract(allowed_resources=(shared,))) != contract_digest(
        common_contract(allowed_resources=(changed,)))


@pytest.mark.parametrize("field,value", [
    ("max_files", True), ("max_files", 17), ("max_files", 0),
    ("max_patch_bytes", 262145), ("max_steps", 9), ("max_retries", 4),
    ("timeout_seconds", "120"), ("timeout_seconds", 301),
])
def test_common_budget_preserves_coding_limits(field, value):
    for budget_type in (ResourceBudget, CodingBudget):
        with pytest.raises(ValidationError):
            budget_type(**{field: value})


def test_high_risk_requires_human_review_and_models_are_deeply_immutable():
    with pytest.raises(ValidationError):
        common_contract(review_policy=ReviewPolicy(
            policy_id="review", version=1, requires_human=False))
    contract = common_contract()
    for instance, field, value in (
        (contract, "user_id", "other"), (contract.budget, "max_files", 16),
        (contract.allowed_resources[0], "version", "b" * 40),
        (contract.review_policy, "requires_human", False),
    ):
        with pytest.raises(ValidationError):
            setattr(instance, field, value)


@pytest.mark.parametrize("field,value", [
    ("task_id", "task-2"), ("user_id", "owner-2"), ("project_id", "project-2"),
    ("agent_id", "research"), ("goal", "Different goal"),
    ("acceptance_criteria", ("Different acceptance",)),
    ("allowed_tools", ("coding.read",)), ("permissions", ("read",)),
    ("budget", ResourceBudget(max_files=1)), ("risk_level", "critical"),
    ("review_policy", ReviewPolicy(policy_id="coding-review", version=2)),
    ("policy_version", 2), ("approval_reference", "approval-2"),
    ("stop_conditions", (*REQUIRED_STOPS, "approval_revoked")),
    ("allowed_resources", (ResourceRef(kind="repository", resource_id="repo-2",
                                      version="a" * 40, paths=("src/answer.py",)),)),
    ("allowed_resources", (ResourceRef(kind="repository", resource_id="repo-1",
                                      version="b" * 40, paths=("src/answer.py",)),)),
    ("allowed_resources", (ResourceRef(kind="repository", resource_id="repo-1",
                                      version="a" * 40, paths=("src/other.py",)),)),
])
def test_digest_binds_every_immutable_decision(field, value):
    assert contract_digest(common_contract(**{field: value})) != contract_digest(common_contract())


def test_digest_is_stable_across_json_order_and_state_updates():
    contract = common_contract()
    raw = contract.model_dump(mode="json")
    reordered = TaskContract.model_validate_json(json.dumps(dict(reversed(list(raw.items())))))
    assert contract_digest(contract) == contract_digest(reordered)
    state = TaskExecutionState(task_id=contract.task_id, contract_digest=contract_digest(contract),
                               created_at=100, updated_at=100)
    advanced = TaskExecutionState(**{**state.model_dump(), "status": "running",
                                     "updated_at": 200, "revision": 1})
    assert advanced.contract_digest == state.contract_digest == contract_digest(contract)
    assert advanced != state


@pytest.mark.parametrize("updates", [
    {"created_at": True}, {"updated_at": 99}, {"revision": -1},
    {"status": "made_up"}, {"contract_digest": "approve"}, {"approved": True},
])
def test_execution_observations_validate_without_changing_spec(updates):
    with pytest.raises(ValidationError):
        TaskExecutionState(**{"task_id": "task-1", "contract_digest": "a" * 64,
                              "created_at": 100, "updated_at": 100, **updates})


def test_digest_and_adapter_revalidate_unchecked_model_copies():
    contract = common_contract()
    poisoned = contract.model_copy(update={"budget": contract.budget.model_copy(
        update={"max_files": 1000})})
    with pytest.raises(ValidationError):
        contract_digest(poisoned)
    with pytest.raises(ValidationError):
        coding_spec_from_task_contract(poisoned)
    with pytest.raises(ValidationError):
        task_contract_from_coding(coding_spec().model_copy(update={"allowed_files": (".env",)}),
            goal="Fix", acceptance_criteria=("Pass",), policy_version=1,
            review_policy=ReviewPolicy(policy_id="review", version=1))


@pytest.mark.parametrize("budget", [CodingBudget(), CodingBudget(
    max_files=16, max_patch_bytes=262144, max_steps=8, max_retries=3, timeout_seconds=300)])
def test_coding_roundtrip_and_legacy_digest_compatibility(budget):
    spec = coding_spec(budget=budget)
    contract = task_contract_from_coding(spec, goal="Fix", acceptance_criteria=("Pass",),
        policy_version=1, review_policy=ReviewPolicy(policy_id="review", version=1))
    restored = coding_spec_from_task_contract(contract)
    assert restored == spec and task_digest(restored) == task_digest(spec)
    assert contract.budget.model_dump() == spec.budget.model_dump()
    assert not contract.allowed_tools and not contract.permissions
    if budget == CodingBudget():
        assert task_digest(spec) == "407d38775debc9bc64c47600b46f039ac6738c970e1612699b5ab84ddcb1b2ab"
        assert change_digest(spec, (FileChange(path="src/answer.py", content="answer = 42\n"),)) == (
            "86069e4d540830287e2a0823b0460eac25edbf9d137e80efd5d8e9b1d82f4464")


def test_contract_reference_and_success_observation_never_grant_permission():
    contract = common_contract(approval_reference="unverified-approval", permissions=("sandbox_run",),
                               allowed_tools=("coding.test",))
    state = TaskExecutionState(task_id=contract.task_id, contract_digest=contract_digest(contract),
                               status="succeeded", created_at=100, updated_at=100)
    assert state.status == "succeeded"  # Only a description, never a policy decision.
    decision = PermissionGate().evaluate(PermissionRequest(
        user_id=contract.user_id, tool="coding.test", action="run", resource="repo-1"))
    assert decision.state == "denied"
    request = TaskCreate(repository_id="repo-1", project_id="project-1", request="Fix",
                         allowed_files=("src/answer.py",))
    assert request.approved is False and "automatic_tests" not in type(request.model).model_fields


@pytest.mark.parametrize("path", ["tests/test_answer.py", "test_answer.py", "conftest.py"])
def test_runtime_test_files_remain_read_only(path):
    with pytest.raises(ValidationError):
        TaskCreate(repository_id="repo-1", project_id="project-1", request="Fix",
                   allowed_files=(path,))


def test_contract_operations_have_no_io_or_environment_side_effects(monkeypatch, tmp_path):
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("Contract descriptions must not access files, processes or network")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    contract = common_contract()
    assert coding_spec_from_task_contract(contract) == coding_spec()
    assert len(contract_digest(contract)) == 64
    assert list(tmp_path.iterdir()) == []


def test_legacy_digest_is_independent_of_new_contract_metadata():
    spec = coding_spec()
    before = task_digest(spec)
    common_contract(approval_reference="reference-1")
    assert task_digest(spec) == before
    assert before == hashlib.sha256(json.dumps(spec.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
