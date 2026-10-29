"""Additive scope metadata. Existing content tables and API policies stay unchanged."""

import time
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from role_ai.database import Base


def scope_id():
    return str(uuid4())


class Project(Base):
    __tablename__ = "scope_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=scope_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # Preserve exact legacy names, including Unicode/case/whitespace. IDs are opaque.
    legacy_name: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[float] = mapped_column(default=time.time)
    __table_args__ = (
        CheckConstraint("length(id) BETWEEN 1 AND 64 AND id NOT GLOB '*[^A-Za-z0-9_-]*'",
                        name="scope_project_identifier"),
        CheckConstraint("state IN ('active','archived')", name="scope_project_state"),
        CheckConstraint("legacy_name IS NULL OR length(legacy_name) BETWEEN 1 AND 80", name="scope_project_name"),
        Index("uq_scope_project_alias", "user_id", "legacy_name", unique=True),
    )


class ScopePolicy(Base):
    __tablename__ = "scope_policies"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    generation: Mapped[int] = mapped_column(default=1)
    __table_args__ = (CheckConstraint("generation >= 1", name="scope_policy_generation"),)


class ScopedResource(Base):
    __tablename__ = "scoped_resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=scope_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(16))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    memory_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id", ondelete="RESTRICT"))
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id", ondelete="RESTRICT"))
    scope: Mapped[str] = mapped_column(String(16))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("scope_projects.id", ondelete="RESTRICT"))
    legacy_project: Mapped[str] = mapped_column(String(80), default="")
    classification: Mapped[str] = mapped_column(String(24), default="review_required")
    reason: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[float] = mapped_column(default=time.time)
    __table_args__ = (
        CheckConstraint(
            "(kind='document' AND document_id IS NOT NULL AND memory_id IS NULL AND conversation_id IS NULL) OR "
            "(kind='memory' AND memory_id IS NOT NULL AND document_id IS NULL AND conversation_id IS NULL) OR "
            "(kind='conversation' AND conversation_id IS NOT NULL AND document_id IS NULL AND memory_id IS NULL)",
            name="scope_resource_target"),
        CheckConstraint(
            "(scope='project' AND project_id IS NOT NULL) OR "
            "(scope IN ('personal','unresolved') AND project_id IS NULL)", name="scope_resource_location"),
        CheckConstraint("classification IN ('ready','review_required')", name="scope_classification"),
        Index("uq_scope_document", "document_id", unique=True),
        Index("uq_scope_memory", "memory_id", unique=True),
        Index("uq_scope_conversation", "conversation_id", unique=True),
        Index("ix_scope_resource_owner", "user_id", "project_id"),
    )


class ResourceVersion(Base):
    __tablename__ = "scope_resource_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=scope_id)
    resource_id: Mapped[str] = mapped_column(ForeignKey("scoped_resources.id", ondelete="RESTRICT"))
    version: Mapped[str] = mapped_column(String(64))
    content_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(default=time.time)
    __table_args__ = (
        CheckConstraint("length(content_digest)=64 AND content_digest NOT GLOB '*[^0-9a-f]*'",
                        name="scope_version_digest"),
        Index("uq_scope_resource_version", "resource_id", "version", unique=True),
    )


class ProjectReadGrant(Base):
    __tablename__ = "scope_read_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=scope_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("scope_resource_versions.id", ondelete="RESTRICT"))
    project_id: Mapped[str] = mapped_column(ForeignKey("scope_projects.id", ondelete="RESTRICT"))
    granted_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    permission: Mapped[str] = mapped_column(String(8), default="read")
    created_at: Mapped[float] = mapped_column(default=time.time)
    revoked_at: Mapped[float | None]
    __table_args__ = (
        CheckConstraint("permission='read'", name="scope_grant_read_only"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="scope_grant_time"),
        Index("uq_scope_active_grant", "version_id", "project_id", unique=True,
              sqlite_where=text("revoked_at IS NULL")),
    )


class MemoryProvenance(Base):
    __tablename__ = "scope_memory_provenance"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=scope_id)
    derived_version_id: Mapped[str] = mapped_column(ForeignKey("scope_resource_versions.id", ondelete="RESTRICT"))
    source_version_id: Mapped[str] = mapped_column(ForeignKey("scope_resource_versions.id", ondelete="RESTRICT"))
    grant_id: Mapped[str | None] = mapped_column(ForeignKey("scope_read_grants.id", ondelete="RESTRICT"))
    created_at: Mapped[float] = mapped_column(default=time.time)
    __table_args__ = (
        CheckConstraint("derived_version_id != source_version_id", name="scope_provenance_self"),
        Index("uq_scope_memory_source", "derived_version_id", "source_version_id", unique=True),
    )
