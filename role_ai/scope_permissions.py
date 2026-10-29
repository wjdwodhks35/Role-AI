"""Authenticated data reads only; decisions are observations, never capabilities.

The application must pass the server-side authenticated session hash, not an
actor ID from model output. Every use rechecks the DB. This adapter performs no
external tool execution and does not replace future approval/execution checks.
"""

import math
import re
import time

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from role_ai.models import AuthSession, Project, ResourceVersion, ScopedResource, ScopePolicy, User
from role_ai.permissions import PermissionDecision
from role_ai.project_scopes import ScopeRepository
from role_ai.task_contracts import ResourceRef, TaskContract

READ_TOOLS = {"document": "documents.read", "memory": "memory.read"}


class ScopeReadGate:
    def __init__(self, db):
        self.db = db

    def evaluate(self, session_hash, contract, resource):
        """Narrow a current authenticated user's data rights by a checked contract.

        approval_reference, claimed user_id and tool names confer no authority.
        Repository references and all write/run actions are deliberately denied.
        No denial includes content, raw DB errors, credentials or supplied IDs.
        """
        try:
            if self.db.new or self.db.dirty or self.db.deleted:
                return PermissionDecision("denied", "pending_changes")
            if not isinstance(session_hash, str) or not re.fullmatch("[0-9a-f]{64}", session_hash):
                return PermissionDecision("denied", "authentication_required")
            checked = TaskContract.model_validate_json(contract.model_dump_json())
            reference = ResourceRef.model_validate_json(resource.model_dump_json())
            with self.db.no_autoflush:
                actor = self.db.get(AuthSession, session_hash, populate_existing=True)
                if (
                    not actor
                    or not math.isfinite(actor.expires_at)
                    or actor.expires_at <= time.time()
                    or self.db.get(User, actor.user_id, populate_existing=True) is None
                ):
                    return PermissionDecision("denied", "authentication_required")
                if checked.user_id != actor.user_id:
                    return PermissionDecision("denied", "owner_mismatch")
                policy = self.db.get(ScopePolicy, actor.user_id, populate_existing=True)
                if not policy or policy.generation != checked.policy_version:
                    return PermissionDecision("denied", "policy_changed")
                if checked.project_id is not None:
                    project = self.db.get(Project, checked.project_id, populate_existing=True)
                    if not project or project.user_id != actor.user_id or project.state != "active":
                        return PermissionDecision("denied", "scope_denied")
                tool = READ_TOOLS.get(reference.kind)
                if (
                    not tool
                    or tool not in checked.allowed_tools
                    or "read" not in checked.permissions
                ):
                    return PermissionDecision("denied", "tool_denied")
                if reference not in checked.allowed_resources:
                    return PermissionDecision("denied", "resource_denied")
                column = getattr(ScopedResource, reference.kind + "_id")
                version = self.db.scalar(
                    select(ResourceVersion)
                    .join(ScopedResource)
                    .where(
                        ScopedResource.user_id == actor.user_id,
                        ScopedResource.kind == reference.kind,
                        column == reference.resource_id,
                        ResourceVersion.version == reference.version,
                    )
                )
                if version is None or not ScopeRepository(self.db).can_read_version(
                    actor.user_id, checked.project_id, version.id
                ):
                    return PermissionDecision("denied", "resource_denied")
                return PermissionDecision("allowed", "current_scope_read")
        except (SQLAlchemyError, ValueError, TypeError, AttributeError):
            return PermissionDecision("denied", "scope_unavailable")
