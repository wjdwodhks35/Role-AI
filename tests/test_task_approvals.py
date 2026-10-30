"""Live approval evidence with synthetic sources, no model/container/network calls."""

from dataclasses import replace
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from role_ai.config import Settings
from role_ai.models import AuthSession, ScopePolicy, TaskApproval
from role_ai.task_approvals import ApprovalService, TaskApprovalGate, ApprovalDenied
from role_ai.contract_execution import TaskToolDispatcher
from role_ai.permissions import PermissionGate, PermissionRequest
from role_ai.assistant import AssistantContext, OpenClawGatewayAdapter
from tests.test_scope_permissions import contract, reference

pytest_plugins = ["tests.test_scope_permissions"]


@pytest.fixture
def approved(access):
    db, repo, rows, versions = access
    settings = Settings(_env_file=None)
    ref = reference(versions)
    body = contract(db, rows["doc"].project_id, ref, approval_reference="approval")
    service = ApprovalService(db, settings)
    return db, repo, settings, body, ref, service


def issue(fixture):
    db, _, _, body, _, service = fixture
    row = service.issue("a" * 64, body)
    db.commit()
    return row


def test_contract_reference_alone_is_not_authority(approved):
    db, _, settings, body, ref, _ = approved
    gate = TaskApprovalGate(db, settings)
    assert gate.evaluate("a" * 64, body, "documents.read", ref).state == "denied"
    issue(approved)
    assert gate.evaluate("a" * 64, body, "documents.read", ref).state == "allowed"
    assert (
        PermissionGate().evaluate_task(db, settings, "a" * 64, body, "documents.read", ref).state
        == "allowed"
    )
    assert (
        PermissionGate().evaluate(PermissionRequest("u1", "pc", "execute", "*")).state == "denied"
    )
    result = TaskToolDispatcher(db, settings).dispatch("a" * 64, body, "documents.read", ref)
    assert result["content"] == "synthetic"


@pytest.mark.parametrize(
    "change",
    [
        "goal",
        "budget",
        "tools",
        "resource",
        "session",
        "policy",
        "revoked",
        "expired",
        "environment",
    ],
)
def test_current_approval_is_bound_and_invalidated(approved, change, monkeypatch):
    db, _, settings, body, ref, service = approved
    row = issue(approved)
    token = "a" * 64
    if change == "goal":
        body = body.model_copy(update={"goal": "different"})
    elif change == "budget":
        body = body.model_copy(update={"budget": body.budget.model_copy(update={"max_files": 1})})
    elif change == "tools":
        body = body.model_copy(update={"allowed_tools": ("documents.read",)})
    elif change == "resource":
        ref = ref.model_copy(update={"version": "0" * 64})
    elif change == "session":
        token = "b" * 64
    elif change == "policy":
        db.get(ScopePolicy, "u1").generation += 1
        db.commit()
    elif change == "revoked":
        service.revoke(token, row.id)
        db.commit()
    elif change == "expired":
        monkeypatch.setattr("role_ai.task_approvals.time.time", lambda: row.expires_at + 1)
    elif change == "environment":
        settings.coding_enabled = not settings.coding_enabled
    assert (
        TaskApprovalGate(db, settings).evaluate(token, body, "documents.read", ref).state
        == "denied"
    )


def test_logout_preserves_approval_history_but_blocks_use(approved):
    db, _, settings, body, ref, _ = approved
    issue(approved)
    db.delete(db.get(AuthSession, "a" * 64))
    db.commit()
    assert db.get(TaskApproval, "approval") is not None
    assert (
        TaskApprovalGate(db, settings).evaluate("a" * 64, body, "documents.read", ref).state
        == "denied"
    )


@pytest.mark.parametrize("operation", ["delete", "rewrite", "unrevoke"])
def test_approval_evidence_is_immutable(approved, operation):
    db, _, _, _, _, service = approved
    issue(approved)
    if operation == "unrevoke":
        service.revoke("a" * 64, "approval")
        db.commit()
    sql = {
        "delete": "DELETE FROM task_approvals",
        "rewrite": "UPDATE task_approvals SET task_id='other'",
        "unrevoke": "UPDATE task_approvals SET revoked_at=NULL",
    }[operation]
    with pytest.raises(IntegrityError):
        db.execute(text(sql))
    db.rollback()


def test_revoked_reference_cannot_be_resurrected(approved):
    db, _, _, body, _, service = approved
    issue(approved)
    service.revoke("a" * 64, "approval")
    db.commit()
    with pytest.raises(ApprovalDenied):
        service.issue("a" * 64, body)


def test_unknown_tools_never_reach_executor(approved):
    db, _, settings, body, ref, _ = approved
    issue(approved)
    called = []
    dispatcher = TaskToolDispatcher(db, settings, sandbox_handler=lambda *args: called.append(args))
    with pytest.raises(ApprovalDenied):
        dispatcher.dispatch("a" * 64, body, "shell", ref, {"command": "anything"})
    assert called == []


def test_session_keys_separate_projects_and_policy_generations():
    context = AssistantContext("u", "c", True, project_id="p", policy_generation=1)
    variants = [
        context,
        replace(context, project_id="q"),
        replace(context, policy_generation=2),
        replace(context, user_id="v"),
        replace(context, conversation_id="d"),
    ]
    assert len({OpenClawGatewayAdapter.session_key(c, "coding") for c in variants}) == len(variants)


@pytest.fixture
def coding_access(approved, tmp_path):
    import subprocess
    from role_ai.task_contracts import ResourceRef

    db, repo, settings, body, _, _ = approved
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n")
    for args in [
        ("init",),
        ("add", "app.py"),
        (
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@test.invalid",
            "commit",
            "-m",
            "fixture",
        ),
    ]:
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    user = db.get(__import__("role_ai.models", fromlist=["User"]).User, "u1")
    user.is_owner = True
    db.commit()
    settings.coding_enabled = True
    settings.coding_root = str(tmp_path / "tasks")
    settings.coding_repositories = {"fixture": str(source)}
    settings.coding_image = "fixture@sha256:" + "1" * 64
    ref = ResourceRef(kind="repository", resource_id="fixture", version=sha, paths=("app.py",))
    body = body.model_copy(
        update={
            "agent_id": "coding",
            "allowed_resources": (ref,),
            "allowed_tools": ("coding.read", "coding.propose", "coding.sandbox_run"),
            "permissions": ("read", "propose", "sandbox_run"),
        }
    )
    ApprovalService(db, settings).issue("a" * 64, body)
    db.commit()
    return db, settings, body, ref, source


def test_coding_read_and_propose_preserve_original(coding_access):
    db, settings, body, ref, source = coding_access
    dispatcher = TaskToolDispatcher(db, settings)
    assert (
        dispatcher.dispatch("a" * 64, body, "coding.read", ref, {"path": "app.py"})["content"]
        == "value = 1\n"
    )
    result = dispatcher.dispatch(
        "a" * 64,
        body,
        "coding.propose",
        ref,
        {"changes": [{"path": "app.py", "content": "value = 2\n"}]},
    )
    assert result["applied"] is False
    assert (source / "app.py").read_text() == "value = 1\n"
    with pytest.raises(ApprovalDenied):
        dispatcher.dispatch("a" * 64, body, "coding.read", ref, {"path": "other.py"})
    with pytest.raises(ApprovalDenied):
        dispatcher.dispatch("a" * 64, body, "coding.sandbox_run", ref, {"command": "pytest"})


def test_revocation_during_registered_execution_discards_result(coding_access):
    db, settings, body, ref, _ = coding_access
    calls = []

    def handler(*args):
        calls.append(args)
        ApprovalService(db, settings).revoke("a" * 64, "approval")
        db.commit()
        return {"passed": True}

    dispatcher = TaskToolDispatcher(db, settings, sandbox_handler=handler)
    with pytest.raises(ApprovalDenied):
        dispatcher.dispatch("a" * 64, body, "coding.sandbox_run", ref, {"command": "sh"})
    assert calls == []
    with pytest.raises(ApprovalDenied):
        dispatcher.dispatch("a" * 64, body, "coding.sandbox_run", ref, {"command": "pytest"})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "path",
    [
        "role_ai/task_approvals.py",
        "role_ai/coding_tasks.py",
        "role_ai/contract_execution.py",
        "role_ai/scope_data.py",
        "role_ai/models.py",
        "tests/test_auth.py",
        "compose.yml",
    ],
)
def test_model_cannot_propose_security_or_test_edits(path):
    from role_ai.coding_jobs import TaskCreate

    with pytest.raises(ValueError):
        TaskCreate(repository_id="repo", project_id="p", request="change", allowed_files=(path,))


def test_http_approval_uses_auth_csrf_and_private_receipts(signed_in):
    import hashlib
    from role_ai.task_contracts import TaskContract, ResourceRef, ReviewPolicy

    c = signed_in
    doc = c.post(
        "/api/documents",
        json={"filename": "synthetic.txt", "content": "private synthetic", "project": "P"},
    ).json()
    state = c.get("/api/projects").json()
    pid = state["projects"][0]["id"]
    # The user is taken from the authenticated server session, not an approval bool.
    from role_ai.security import token_hash
    from role_ai.models import AuthSession

    with c.app.state.database_factory() as db:
        uid = db.get(AuthSession, token_hash(c.cookies.get("role_ai_session"))).user_id
    body = TaskContract(
        task_id="http-task",
        user_id=uid,
        scope="project",
        project_id=pid,
        agent_id="knowledge",
        goal="read",
        acceptance_criteria=("only scoped",),
        allowed_resources=(
            ResourceRef(
                kind="document",
                resource_id=doc["id"],
                version=hashlib.sha256(b"private synthetic").hexdigest(),
            ),
        ),
        allowed_tools=("documents.read",),
        permissions=("read",),
        risk_level="low",
        review_policy=ReviewPolicy(policy_id="p", version=1),
        policy_version=state["policy_generation"],
        approval_reference="http-approval",
    )
    response = c.post("/api/task-approvals", json={"contract": body.model_dump(mode="json")})
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"id", "task_id", "contract_digest", "expires_at", "revoked_at"}
    assert c.delete("/api/task-approvals/http-approval").status_code == 200
    assert (
        c.post("/api/task-approvals", json={"contract": body.model_dump(mode="json")}).status_code
        == 409
    )
    csrf = c.headers.pop("X-CSRF-Token", None)
    assert (
        c.post("/api/task-approvals", json={"contract": body.model_dump(mode="json")}).status_code
        == 403
    )
    if csrf:
        c.headers["X-CSRF-Token"] = csrf
