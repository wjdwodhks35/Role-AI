"""P1-04 read bridge; no registration, mutation, migration or tool execution.

HTTP adapters must pass the session hash from the existing authenticated
server dependency. Project IDs are explicit; None is the personal inbox.
"""

import time
import math
import re

from sqlalchemy import select

from role_ai.models import AuthSession, Document, Memory, ScopePolicy
from role_ai.project_scopes import source_fingerprint
from role_ai.scope_permissions import ScopeReadGate
from role_ai.task_contracts import ResourceRef, ReviewPolicy, TaskContract


class ScopedReadDenied(ValueError):
    pass


class ScopedReads:
    def __init__(self, db, session_hash, project_id=None):
        self.db, self.session_hash, self.project_id = db, session_hash, project_id

    def _actor(self):
        if (
            self.db.new
            or self.db.dirty
            or self.db.deleted
            or not isinstance(self.session_hash, str)
            or not re.fullmatch("[0-9a-f]{64}", self.session_hash)
        ):
            raise ScopedReadDenied("Source unavailable")
        actor = self.db.get(AuthSession, self.session_hash, populate_existing=True)
        if actor is None or not math.isfinite(actor.expires_at) or actor.expires_at <= time.time():
            raise ScopedReadDenied("Source unavailable")
        return actor

    def require(self, kind, source_id):
        model = {"document": Document, "memory": Memory}.get(kind)
        if model is None or self.db.new or self.db.dirty or self.db.deleted:
            raise ScopedReadDenied("Source unavailable")
        actor = self._actor()
        row = self.db.get(model, source_id, populate_existing=True)
        if row is None or row.user_id != actor.user_id:
            raise ScopedReadDenied("Source unavailable")
        fingerprint = source_fingerprint(row)
        policy = self.db.get(ScopePolicy, actor.user_id, populate_existing=True)
        if fingerprint is None or policy is None:
            raise ScopedReadDenied("Source unavailable")
        reference = ResourceRef(kind=kind, resource_id=row.id, version=fingerprint[0])
        contract = TaskContract(
            task_id="authenticated-data-read",
            user_id=actor.user_id,
            scope="personal" if self.project_id is None else "project",
            project_id=self.project_id,
            agent_id="knowledge",
            goal="Read a current permitted source",
            acceptance_criteria=("Current exact version and scope are allowed",),
            allowed_resources=(reference,),
            allowed_tools=(f"{'documents' if kind == 'document' else 'memory'}.read",),
            permissions=("read",),
            risk_level="low",
            review_policy=ReviewPolicy(policy_id="source-read", version=1),
            policy_version=policy.generation,
        )
        if (
            ScopeReadGate(self.db).evaluate(self.session_hash, contract, reference).state
            != "allowed"
        ):
            raise ScopedReadDenied("Source unavailable")
        return row

    def visible(self, kind):
        model = {"document": Document, "memory": Memory}.get(kind)
        if model is None:
            raise ScopedReadDenied("Source unavailable")
        actor = self._actor()
        # Per-source Gate before anything is sent to an embedding/model/export path.
        result = []
        for source_id in self.db.scalars(select(model.id).where(model.user_id == actor.user_id)):
            try:
                result.append(self.require(kind, source_id))
            except ScopedReadDenied:
                continue
        return result
