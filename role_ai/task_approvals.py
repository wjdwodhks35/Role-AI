"""Current authenticated approval evidence. No model-supplied bool authorizes a tool."""

import hashlib
import json
import math
import re
import time
from pathlib import Path
from sqlalchemy.exc import SQLAlchemyError
from role_ai.models import AuthSession, User, Project, ScopePolicy, TaskApproval
from role_ai.task_contracts import TaskContract, ResourceRef, contract_digest
from role_ai.agent_registry import AgentRegistry
from role_ai.scope_permissions import ScopeReadGate
from role_ai.coding_project import repository
from role_ai.coding_workspace import git
from role_ai.coding_tasks import safe_path
from role_ai.coding_runner import PINNED_IMAGE
from role_ai.permissions import PermissionDecision

# Server-owned catalog. Arbitrary shell/network/model/permission-management tools are absent.
TOOLS = {
    "documents.read": ("read", "document"),
    "memory.read": ("read", "memory"),
    "coding.read": ("read", "repository"),
    "coding.propose": ("propose", "repository"),
    "coding.sandbox_run": ("sandbox_run", "repository"),
}

REQUIRED_CAPABILITIES = {
    "documents.read": "knowledge.search",
    "memory.read": "knowledge.search",
    "coding.read": "code.propose",
    "coding.propose": "code.propose",
    "coding.sandbox_run": "sandbox.request",
}


class ApprovalDenied(ValueError):
    pass


def checked_contract(value):
    return TaskContract.model_validate_json(value.model_dump_json())


def actor(db, session_hash):
    if (
        db.new
        or db.dirty
        or db.deleted
        or not isinstance(session_hash, str)
        or not re.fullmatch("[0-9a-f]{64}", session_hash)
    ):
        raise ApprovalDenied("authentication_required")
    session = db.get(AuthSession, session_hash, populate_existing=True)
    if (
        session is None
        or not math.isfinite(session.expires_at)
        or session.expires_at <= time.time()
    ):
        raise ApprovalDenied("authentication_required")
    user = db.get(User, session.user_id, populate_existing=True)
    if user is None:
        raise ApprovalDenied("authentication_required")
    return user


def validate_scope(db, user, contract):
    if contract.user_id != user.id:
        raise ApprovalDenied("owner_mismatch")
    policy = db.get(ScopePolicy, user.id, populate_existing=True)
    if policy is None or policy.generation != contract.policy_version:
        raise ApprovalDenied("policy_changed")
    if contract.project_id is not None:
        project = db.get(Project, contract.project_id, populate_existing=True)
        if project is None or project.user_id != user.id or project.state != "active":
            raise ApprovalDenied("scope_denied")


def validate_resource(db, settings, session_hash, user, contract, reference):
    if reference.kind != "repository":
        if ScopeReadGate(db).evaluate(session_hash, contract, reference).state != "allowed":
            raise ApprovalDenied("resource_denied")
        return None
    if (
        not user.is_owner
        or settings.mode != "local"
        or not settings.coding_enabled
        or contract.scope != "project"
    ):
        raise ApprovalDenied("repository_disabled")
    source = repository(settings, reference.resource_id)
    if git(source, "rev-parse", "HEAD^{commit}").decode().strip() != reference.version:
        raise ApprovalDenied("repository_changed")
    for path in reference.paths:
        safe_path(path)
    return source


def environment(settings, contract):
    registry = AgentRegistry(settings)
    definition = next((a for a in registry.configuration.agents if a.id == contract.agent_id), None)
    if definition is None:
        raise ApprovalDenied("unknown_agent")
    if any(tool not in TOOLS for tool in contract.allowed_tools):
        raise ApprovalDenied("unknown_tool")
    if (
        any(tool.startswith("coding.") for tool in contract.allowed_tools)
        and contract.agent_id != "coding"
    ):
        raise ApprovalDenied("role_denied")
    if any(
        REQUIRED_CAPABILITIES[tool] not in definition.capabilities
        for tool in contract.allowed_tools
    ):
        raise ApprovalDenied("role_denied")
    repos = {
        ref.resource_id: hashlib.sha256(
            str(repository(settings, ref.resource_id)).encode()
        ).hexdigest()
        for ref in contract.allowed_resources
        if ref.kind == "repository"
    }
    value = {
        "catalog": TOOLS,
        "role_requirements": REQUIRED_CAPABILITIES,
        "registry": registry.config_hash,
        "mode": settings.mode,
        "coding_enabled": settings.coding_enabled,
        "image": settings.coding_image,
        "socket": hashlib.sha256(settings.code_execution_socket.encode()).hexdigest(),
        "repositories": repos,
        "implementation": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "task_approvals.py",
                "contract_execution.py",
                "task_approval_api.py",
                "permissions.py",
                "scope_permissions.py",
                "coding_tasks.py",
                "coding_runner.py",
            )
        },
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ApprovalService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def issue(self, session_hash, contract, expires_in=900):
        user = actor(self.db, session_hash)
        contract = checked_contract(contract)
        validate_scope(self.db, user, contract)
        if (
            not contract.approval_reference
            or type(expires_in) is not int
            or not 1 <= expires_in <= 3600
        ):
            raise ApprovalDenied("approval_reference_required")
        env = environment(self.settings, contract)
        for ref in contract.allowed_resources:
            validate_resource(self.db, self.settings, session_hash, user, contract, ref)
        digest = contract_digest(contract)
        row = self.db.get(TaskApproval, contract.approval_reference, populate_existing=True)
        if row:
            if (
                row.user_id != user.id
                or row.session_hash != session_hash
                or row.contract_digest != digest
                or row.environment_digest != env
                or row.revoked_at is not None
                or row.expires_at <= time.time()
            ):
                raise ApprovalDenied("approval_reference_unavailable")
            return row
        now = time.time()
        row = TaskApproval(
            id=contract.approval_reference,
            user_id=user.id,
            session_hash=session_hash,
            task_id=contract.task_id,
            contract_digest=digest,
            contract_json=contract.model_dump_json(),
            environment_digest=env,
            created_at=now,
            expires_at=now + expires_in,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def revoke(self, session_hash, reference):
        user = actor(self.db, session_hash)
        row = self.db.get(TaskApproval, reference, populate_existing=True)
        if row is None or row.user_id != user.id:
            raise ApprovalDenied("approval_unavailable")
        if row.revoked_at is None:
            row.revoked_at = max(time.time(), row.created_at)
            self.db.flush()
        return row


class TaskApprovalGate:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def require(self, session_hash, contract, tool, resource):
        try:
            user = actor(self.db, session_hash)
            contract = checked_contract(contract)
            reference = ResourceRef.model_validate_json(resource.model_dump_json())
            validate_scope(self.db, user, contract)
            rule = TOOLS.get(tool)
            if (
                not rule
                or tool not in contract.allowed_tools
                or rule[0] not in contract.permissions
                or reference.kind != rule[1]
            ):
                raise ApprovalDenied("tool_denied")
            if reference not in contract.allowed_resources:
                raise ApprovalDenied("resource_denied")
            if tool == "coding.sandbox_run" and not PINNED_IMAGE.fullmatch(
                self.settings.coding_image
            ):
                raise ApprovalDenied("runner_disabled")
            row = (
                self.db.get(TaskApproval, contract.approval_reference, populate_existing=True)
                if contract.approval_reference
                else None
            )
            now = time.time()
            if (
                row is None
                or row.user_id != user.id
                or row.session_hash != session_hash
                or row.task_id != contract.task_id
                or row.revoked_at is not None
                or not math.isfinite(row.expires_at)
                or not row.created_at <= now < row.expires_at
                or row.contract_digest != contract_digest(contract)
                or contract_digest(TaskContract.model_validate_json(row.contract_json))
                != row.contract_digest
                or row.environment_digest != environment(self.settings, contract)
            ):
                raise ApprovalDenied("approval_denied")
            validate_resource(self.db, self.settings, session_hash, user, contract, reference)
            return contract, reference
        except ApprovalDenied:
            raise
        except (SQLAlchemyError, ValueError, TypeError, AttributeError, OSError):
            raise ApprovalDenied("approval_unavailable") from None

    def evaluate(self, session_hash, contract, tool, resource):
        try:
            self.require(session_hash, contract, tool, resource)
            return PermissionDecision("allowed", "current_task_approval")
        except ApprovalDenied as error:
            return PermissionDecision("denied", str(error))
