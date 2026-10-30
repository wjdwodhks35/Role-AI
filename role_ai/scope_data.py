"""Trusted data adapters. Owners manage native scopes; Agents get current reads only."""

import time
from fastapi import HTTPException
from sqlalchemy import select, and_, or_
from role_ai.models import (
    Document,
    Memory,
    Conversation,
    Message,
    Project,
    ProjectReadGrant,
    ResourceVersion,
    ScopedResource,
    ScopePolicy,
    MemoryProvenance,
)
from role_ai.scope_models import SourceArchive, ConversationBinding, ConversationSource
from role_ai.project_scopes import ScopeRepository, source_fingerprint
from role_ai.scoped_reads import ScopedReads, ScopedReadDenied


def project_id(db, uid, alias="", explicit=None):
    if explicit is not None:
        p = db.get(Project, explicit, populate_existing=True)
    elif alias:
        p = db.scalar(select(Project).where(Project.user_id == uid, Project.legacy_name == alias))
    else:
        return None
    if p is None or p.user_id != uid or p.state != "active":
        raise HTTPException(404, "현재 범위를 찾을 수 없습니다.")
    return p.id


def resource_for(db, kind, source_id):
    column = getattr(ScopedResource, kind + "_id")
    return db.scalar(select(ScopedResource).where(ScopedResource.kind == kind, column == source_id))


def archived(db, kind, source_id):
    resource = resource_for(db, kind, source_id)
    return resource is not None and db.get(SourceArchive, resource.id) is not None


def bind_conversation(db, conversation, alias=""):
    repo = ScopeRepository(db)
    repo._user(conversation.user_id)
    project = repo.create_project(conversation.user_id, alias) if alias else None
    if project is not None and project.state != "active":
        raise HTTPException(409, "보관된 프로젝트에는 새 대화를 만들 수 없습니다.")
    generation = db.get(ScopePolicy, conversation.user_id, populate_existing=True).generation
    db.add(
        ConversationBinding(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            project_id=project.id if project else None,
            generation=generation,
        )
    )
    db.add(
        ScopedResource(
            user_id=conversation.user_id,
            kind="conversation",
            conversation_id=conversation.id,
            scope="project" if project else "personal",
            project_id=project.id if project else None,
            legacy_project=alias,
            classification="ready",
            reason="new_bound_conversation",
        )
    )
    db.flush()


def check_tags(db, uid, pid, tags):
    repo = ScopeRepository(db)
    for tag in tags:
        version_id = tag.get("version_id")
        if not version_id or not repo.can_read_version(uid, pid, version_id):
            raise HTTPException(409, "자료 권한이 변경됐습니다. 새 대화를 시작하세요.")
        grant_id = tag.get("grant_id")
        if grant_id:
            grant = db.get(ProjectReadGrant, grant_id, populate_existing=True)
            if (
                not grant
                or grant.revoked_at is not None
                or grant.version_id != version_id
                or grant.project_id != pid
                or grant.granted_by != uid
            ):
                raise HTTPException(409, "자료 권한이 변경됐습니다. 새 대화를 시작하세요.")


def bound_conversation(db, uid, cid, alias=None):
    binding = db.get(ConversationBinding, cid, populate_existing=True)
    policy = db.get(ScopePolicy, uid, populate_existing=True)
    if (
        not binding
        or binding.user_id != uid
        or not policy
        or binding.generation != policy.generation
    ):
        raise HTTPException(
            409, "보관된 대화는 문맥으로 재사용할 수 없습니다. 새 대화를 시작하세요."
        )
    if alias is not None and project_id(db, uid, alias) != binding.project_id:
        raise HTTPException(409, "대화의 프로젝트는 고정되어 있습니다. 새 대화를 시작하세요.")
    if binding.project_id is not None:
        project_id(db, uid, explicit=binding.project_id)
    rows = list(
        db.scalars(
            select(ConversationSource).where(ConversationSource.conversation_id == cid).limit(1025)
        )
    )
    if len(rows) > 1024:
        raise HTTPException(409, "출처가 많은 대화입니다. 새 대화를 시작하세요.")
    check_tags(
        db,
        uid,
        binding.project_id,
        [{"version_id": r.version_id, "grant_id": r.grant_id} for r in rows],
    )
    return binding


def record_conversation_sources(db, uid, cid, tags):
    binding = bound_conversation(db, uid, cid)
    check_tags(db, uid, binding.project_id, tags)
    existing = {
        (r.version_id, r.grant_id)
        for r in db.scalars(
            select(ConversationSource).where(ConversationSource.conversation_id == cid)
        )
    }
    for t in tags:
        key = (t["version_id"], t.get("grant_id"))
        if key not in existing:
            db.add(ConversationSource(conversation_id=cid, version_id=key[0], grant_id=key[1]))
            existing.add(key)
    db.flush()


def register_source(db, uid, kind, row):
    repo = ScopeRepository(db)
    was_new = resource_for(db, kind, row.id) is None
    resource = repo.register_source(uid, kind, row.id)
    if kind == "memory" and row.source_message_id and was_new:
        message = db.get(Message, row.source_message_id)
        conv = db.get(Conversation, message.conversation_id) if message else None
        if not conv or conv.user_id != uid or message.role != "user":
            raise HTTPException(404, "현재 범위에서 원문을 찾을 수 없습니다.")
        binding = bound_conversation(db, uid, conv.id)
        if binding.project_id != resource.project_id:
            raise HTTPException(409, "원문과 기억의 프로젝트가 다릅니다.")
        resource.classification = "ready"
        resource.reason = "verified_bound_message"
        db.flush()
    if resource.classification != "ready":
        raise HTTPException(409, "기존 자료의 출처 검토가 필요합니다.")
    version = repo.record_current_version(uid, resource.id)
    if kind == "memory" and row.source_message_id and was_new:
        message = db.get(Message, row.source_message_id)
        for edge in db.scalars(
            select(ConversationSource).where(
                ConversationSource.conversation_id == message.conversation_id
            )
        ):
            db.add(
                MemoryProvenance(
                    derived_version_id=version.id,
                    source_version_id=edge.version_id,
                    grant_id=edge.grant_id,
                )
            )
        db.flush()
    return resource


def archive_source(db, uid, kind, row):
    resource = resource_for(db, kind, row.id)
    if resource is None:
        # Owner explicitly requested archiving; no read-path backfill.
        resource = ScopeRepository(db).register_source(uid, kind, row.id)
    if resource.user_id != uid:
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    if db.get(SourceArchive, resource.id) is not None:
        return
    for grant in db.scalars(
        select(ProjectReadGrant)
        .join(ResourceVersion)
        .where(ResourceVersion.resource_id == resource.id, ProjectReadGrant.revoked_at.is_(None))
    ):
        grant.revoked_at = max(time.time(), grant.created_at)
    db.add(SourceArchive(resource_id=resource.id, user_id=uid))
    db.flush()
    db.expire_all()


class DataAccess:
    def __init__(self, db, uid, alias="", session_hash=None, explicit=None):
        self.db, self.uid, self.session_hash = db, uid, session_hash
        self.pid = project_id(db, uid, alias, explicit)

    def query(self, kind, review=False):
        model = {"document": Document, "memory": Memory}[kind]
        target = getattr(ScopedResource, kind + "_id")
        native = (
            and_(ScopedResource.scope == "project", ScopedResource.project_id == self.pid)
            if self.pid
            else and_(ScopedResource.scope == "personal", ScopedResource.project_id.is_(None))
        )
        granted = (
            select(1)
            .select_from(ResourceVersion)
            .join(ProjectReadGrant, ProjectReadGrant.version_id == ResourceVersion.id)
            .where(
                ResourceVersion.resource_id == ScopedResource.id,
                ProjectReadGrant.project_id == self.pid,
                ProjectReadGrant.granted_by == self.uid,
                ProjectReadGrant.revoked_at.is_(None),
            )
            .correlate(ScopedResource)
            .exists()
            if self.pid
            else False
        )
        retired = (
            select(1)
            .select_from(SourceArchive)
            .where(SourceArchive.resource_id == ScopedResource.id)
            .correlate(ScopedResource)
            .exists()
        )
        scoped = and_(ScopedResource.user_id == self.uid, or_(native, granted), ~retired)
        if not review:
            scoped = and_(scoped, ScopedResource.classification == "ready")
        else:
            alias = self.db.get(Project, self.pid).legacy_name if self.pid else ""
            scoped = or_(scoped, and_(ScopedResource.id.is_(None), model.project == alias))
        return (
            select(model)
            .outerjoin(ScopedResource, and_(target == model.id, ScopedResource.kind == kind))
            .where(model.user_id == self.uid, scoped)
        )

    def manage(self, kind, row, include_archived=False):
        if row is None or row.user_id != self.uid:
            return False
        if not include_archived and archived(self.db, kind, row.id):
            return False
        resource = resource_for(self.db, kind, row.id)
        if resource is None:
            return project_id(self.db, self.uid, getattr(row, "project", "")) == self.pid
        return resource.scope != "unresolved" and resource.project_id == self.pid

    def version(self, kind, row):
        resource = resource_for(self.db, kind, row.id)
        fingerprint = source_fingerprint(row)
        if not resource or not fingerprint:
            return None
        return self.db.scalar(
            select(ResourceVersion).where(
                ResourceVersion.resource_id == resource.id,
                ResourceVersion.version == fingerprint[0],
                ResourceVersion.content_digest == fingerprint[1],
            )
        )

    def readable(self, kind, row):
        if row is None or row.user_id != self.uid:
            return False
        if self.session_hash:
            try:
                ScopedReads(self.db, self.session_hash, self.pid).require(kind, row.id)
                return True
            except (ScopedReadDenied, ValueError):
                return False
        version = self.version(kind, row)
        return version is not None and ScopeRepository(self.db).can_read_version(
            self.uid, self.pid, version.id
        )

    def tag(self, kind, row):
        if not self.readable(kind, row):
            raise HTTPException(404, "현재 범위에서 자료를 찾을 수 없습니다.")
        version = self.version(kind, row)
        resource = resource_for(self.db, kind, row.id)
        grant_id = None
        if resource.project_id != self.pid:
            grant_id = self.db.scalar(
                select(ProjectReadGrant.id).where(
                    ProjectReadGrant.version_id == version.id,
                    ProjectReadGrant.project_id == self.pid,
                    ProjectReadGrant.revoked_at.is_(None),
                )
            )
        return {"version_id": version.id, "grant_id": grant_id, "version": version.version}


def require_live_session(db, session_hash, uid):
    from role_ai.models import AuthSession

    session = db.get(AuthSession, session_hash, populate_existing=True)
    if session is None or session.user_id != uid or session.expires_at <= time.time():
        raise HTTPException(401, "로그인이 필요합니다.")
