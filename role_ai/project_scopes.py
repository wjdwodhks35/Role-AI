"""Internal scope foundation, not an API or authorization credential.

Callers own transactions and must authenticate the actor before future API use.
No existing search/chat route calls this module in P1-02. Missing/stale metadata
denies eligibility; revocation retains grants, versions, and provenance records.
"""

import hashlib
import json
import time
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from role_ai.models import (
    Conversation, Document, Memory, MemoryProvenance, Project, ProjectReadGrant,
    ResourceVersion, ScopedResource, ScopePolicy, User,
)
from role_ai.task_contracts import ResourceRef
from role_ai.scope_models import SourceArchive


class ScopeError(ValueError):
    """Fixed diagnostics only; do not include content, aliases or raw DB errors."""


SOURCE_MODELS = {"document": Document, "memory": Memory, "conversation": Conversation}


def stable_id(*parts):
    return str(uuid5(NAMESPACE_URL, json.dumps(["role-ai-scope-v1", *parts], ensure_ascii=False)))


def source_fingerprint(source):
    if isinstance(source, Document):
        digest = hashlib.sha256(source.content.encode()).hexdigest()
        if source.digest != digest:
            return None
        return digest, digest
    if isinstance(source, Memory) and type(source.revision) is int and source.revision > 0:
        raw = json.dumps({k: getattr(source, k) for k in (
            "content", "kind", "project", "source_message_id")}, sort_keys=True, ensure_ascii=False)
        return str(source.revision), hashlib.sha256(raw.encode()).hexdigest()
    return None


class ScopeRepository:
    def __init__(self, db: Session):
        self.db = db

    def _user(self, user_id):
        if not self.db.get(User, user_id):
            raise ScopeError("Scope owner does not exist")
        if self.db.get(ScopePolicy, user_id) is None:
            self.db.add(ScopePolicy(user_id=user_id, generation=1))
            self.db.flush()

    def create_project(self, user_id, legacy_name=None):
        self._user(user_id)
        if legacy_name is not None:
            if not isinstance(legacy_name, str) or not 1 <= len(legacy_name) <= 80:
                raise ScopeError("Invalid project alias")
            existing = self.db.scalar(select(Project).where(
                Project.user_id == user_id, Project.legacy_name == legacy_name))
            if existing:
                return existing
        project = Project(user_id=user_id, legacy_name=legacy_name)
        self.db.add(project)
        self.db.flush()
        return project

    def register_source(self, user_id, kind, source_id):
        self._user(user_id)
        model = SOURCE_MODELS.get(kind)
        source = self.db.get(model, source_id) if model else None
        if source is None or source.user_id != user_id:
            raise ScopeError("Source is missing or not owned")
        column = getattr(ScopedResource, kind + "_id")
        existing = self.db.scalar(select(ScopedResource).where(column == source_id))
        if existing:
            if existing.user_id != user_id:
                raise ScopeError("Source scope owner mismatch")
            return existing  # Never reclassify or replace grants on a replay.
        alias = getattr(source, "project", "")
        project = self.create_project(user_id, alias) if alias else None
        scope = "project" if project else "personal"
        classification, reason = "ready", "legacy_private"
        if kind == "conversation":
            scope, classification, reason = "unresolved", "review_required", "legacy_conversation"
        elif kind == "memory" and source.source_message_id:
            classification, reason = "review_required", "legacy_message_provenance"
        elif source_fingerprint(source) is None:
            classification, reason = "review_required", "invalid_source_version"
        resource = ScopedResource(
            id=stable_id(kind, source_id), user_id=user_id, kind=kind,
            **{kind + "_id": source_id}, project_id=project.id if project else None,
            scope=scope, legacy_project=alias, classification=classification, reason=reason,
        )
        self.db.add(resource)
        self.db.flush()
        return resource

    def record_current_version(self, user_id, resource_id):
        resource = self.db.get(ScopedResource, resource_id)
        if not resource or resource.user_id != user_id or resource.classification != "ready":
            raise ScopeError("Source classification requires review")
        source = self._source(resource)
        fingerprint = source_fingerprint(source) if source is not None else None
        if fingerprint is None or getattr(source, "project", "") != resource.legacy_project:
            raise ScopeError("Source version or project changed")
        version, digest = fingerprint
        # Reuse P1-01's exact-version format, not a parallel contract or digest.
        ResourceRef(kind=resource.kind, resource_id=source.id, version=version)
        existing = self.db.scalar(select(ResourceVersion).where(
            ResourceVersion.resource_id == resource.id, ResourceVersion.version == version))
        if existing:
            if existing.content_digest != digest:
                raise ScopeError("A version was reused with different content")
            return existing
        inherited = {}
        if resource.kind == "memory":
            previous = list(self.db.scalars(select(ResourceVersion.version).where(
                ResourceVersion.resource_id == resource.id)))
            if previous and int(version) <= max(map(int, previous)):
                raise ScopeError("Memory versions cannot move backwards")
            for source_id, grant_id in self._required_provenance(resource.id):
                if source_id in inherited and inherited[source_id] != grant_id:
                    raise ScopeError("Conflicting memory provenance requires review")
                inherited[source_id] = grant_id
        row = ResourceVersion(id=stable_id(resource.id, version), resource_id=resource.id,
                              version=version, content_digest=digest)
        self.db.add(row)
        self.db.flush()
        for source_id, grant_id in inherited.items():
            self.db.add(MemoryProvenance(derived_version_id=row.id,
                                        source_version_id=source_id, grant_id=grant_id))
        self.db.flush()
        return row

    def _required_provenance(self, resource_id):
        rows = list(self.db.execute(select(
            MemoryProvenance.source_version_id, MemoryProvenance.grant_id,
        ).join(ResourceVersion, ResourceVersion.id == MemoryProvenance.derived_version_id).where(
            ResourceVersion.resource_id == resource_id).distinct().limit(65)))
        if len(rows) > 64:
            raise ScopeError("Memory provenance limit requires review")
        return rows

    def _source(self, resource):
        model = SOURCE_MODELS.get(resource.kind)
        if model is None:
            return None
        row = self.db.get(model, getattr(resource, resource.kind + "_id"), populate_existing=True)
        return row if row is not None and row.user_id == resource.user_id else None

    def grant_read(self, user_id, version_id, project_id):
        version = self.db.get(ResourceVersion, version_id)
        resource = self.db.get(ScopedResource, version.resource_id) if version else None
        project = self.db.get(Project, project_id)
        if (not resource or resource.user_id != user_id or not project
                or project.user_id != user_id or project.state != "active"
                or not self.can_read_version(user_id, resource.project_id, version_id)):
            raise ScopeError("Read grant is outside the current owned scope")
        existing = self.db.scalar(select(ProjectReadGrant).where(
            ProjectReadGrant.version_id == version_id, ProjectReadGrant.project_id == project_id,
            ProjectReadGrant.revoked_at.is_(None)))
        if existing:
            return existing
        grant = ProjectReadGrant(version_id=version_id, project_id=project_id, granted_by=user_id)
        self.db.add(grant)
        self.db.flush()
        return grant

    def revoke_read(self, user_id, grant_id):
        grant = self.db.get(ProjectReadGrant, grant_id)
        if not grant or grant.granted_by != user_id:
            raise ScopeError("Read grant is missing or not owned")
        if grant.revoked_at is None:
            grant.revoked_at = max(time.time(), grant.created_at)
            self.db.flush()
            # Database trigger changes the generation even for direct SQL revocation.
            self.db.expire_all()

    def record_provenance(self, user_id, derived_version_id, source_version_id, grant_id=None):
        derived = self.db.get(ResourceVersion, derived_version_id)
        resource = self.db.get(ScopedResource, derived.resource_id) if derived else None
        if (not resource or resource.user_id != user_id or resource.kind != "memory"
                or not self.can_read_version(user_id, resource.project_id, derived_version_id)
                or not self.can_read_version(user_id, resource.project_id, source_version_id)):
            raise ScopeError("Memory provenance is outside the current scope")
        edge = MemoryProvenance(derived_version_id=derived_version_id,
                                source_version_id=source_version_id, grant_id=grant_id)
        self.db.add(edge)
        self.db.flush()  # DB validates exact grant/owner/location and rejects cycles.
        return edge

    def can_read_version(self, user_id, project_id, version_id, *, now=None):
        """Future policy building block only. No route uses it yet; no cached ACLs.

        None is the explicit personal inbox, never an all-project wildcard.
        Evaluate every ancestor in the requested context, including the original
        grant's tombstone. A new grant does not revive old derived memories.
        """
        self.db.flush()
        if project_id is not None:
            project = self.db.get(Project, project_id, populate_existing=True)
            if not project or project.user_id != user_id or project.state != "active":
                return False
        clock = time.time() if now is None else now
        visiting, memo = set(), {}

        def visit(vid):
            if vid in memo:
                return memo[vid]
            if vid in visiting or len(visiting) >= 64 or len(memo) + len(visiting) >= 256:
                return False
            visiting.add(vid)
            result = evaluate(vid)
            visiting.remove(vid)
            memo[vid] = result
            return result

        def evaluate(vid):
            version = self.db.get(ResourceVersion, vid, populate_existing=True)
            resource = self.db.get(ScopedResource, version.resource_id, populate_existing=True) if version else None
            if resource is not None and self.db.get(SourceArchive, resource.id) is not None:
                return False
            if (not resource or resource.user_id != user_id or resource.classification != "ready"
                    or resource.scope == "unresolved"):
                return False
            if resource.scope == "project":
                origin = self.db.get(Project, resource.project_id, populate_existing=True)
                if not origin or origin.user_id != user_id or origin.state != "active":
                    return False
            source = self._source(resource)
            if source is None or getattr(source, "project", "") != resource.legacy_project:
                return False
            if source_fingerprint(source) != (version.version, version.content_digest):
                return False
            if isinstance(source, Memory) and (source.state != "confirmed" or (
                    source.expires_at is not None and source.expires_at <= clock)):
                return False
            direct = ((resource.scope == "personal" and project_id is None)
                      or (resource.scope == "project" and resource.project_id == project_id))
            if not direct and (project_id is None or self.db.scalar(select(ProjectReadGrant.id).where(
                    ProjectReadGrant.version_id == vid, ProjectReadGrant.project_id == project_id,
                    ProjectReadGrant.granted_by == user_id, ProjectReadGrant.revoked_at.is_(None))) is None):
                return False
            edges = list(self.db.scalars(select(MemoryProvenance).where(
                MemoryProvenance.derived_version_id == vid).limit(65)))
            if len(edges) > 64:
                return False
            if resource.kind == "memory":
                try:
                    required = set(map(tuple, self._required_provenance(resource.id)))
                except ScopeError:
                    return False
                if not required.issubset({(e.source_version_id, e.grant_id) for e in edges}):
                    return False  # Revision changes must not launder revoked dependencies.
            for edge in edges:
                if edge.grant_id is not None:
                    grant = self.db.get(ProjectReadGrant, edge.grant_id, populate_existing=True)
                    if (not grant or grant.revoked_at is not None or grant.granted_by != user_id
                            or grant.version_id != edge.source_version_id
                            or grant.project_id != resource.project_id):
                        return False
                if not visit(edge.source_version_id):
                    return False
            return True

        return visit(version_id)


def classify_legacy_resources(connection):
    """Migration hook: add metadata only, never grant, edit or delete legacy content."""
    with Session(bind=connection) as db:
        repo = ScopeRepository(db)
        for user_id in db.scalars(select(User.id)):
            repo._user(user_id)
        for kind, model in SOURCE_MODELS.items():
            for source in db.scalars(select(model)):
                resource = repo.register_source(source.user_id, kind, source.id)
                if resource.classification == "ready":
                    repo.record_current_version(source.user_id, resource.id)
        db.flush()
