import time
import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from role_ai.database import Base


def new_id():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_owner: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[float] = mapped_column(default=time.time)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(index=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[float] = mapped_column(default=time.time)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(20), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    skill: Mapped[str] = mapped_column(String(20), default="assistant")
    sequence: Mapped[int]
    created_at: Mapped[float] = mapped_column(default=time.time)


class ApiUsage(Base):
    __tablename__ = "api_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="reserved", index=True)
    input_rate: Mapped[str] = mapped_column(String(40))
    output_rate: Mapped[str] = mapped_column(String(40))
    reserved_microusd: Mapped[int]
    charged_microusd: Mapped[int]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    created_at: Mapped[float] = mapped_column(default=time.time, index=True)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    project: Mapped[str] = mapped_column(String(80), default="")
    content: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    state: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    revision: Mapped[int] = mapped_column(default=1)
    expires_at: Mapped[float | None]
    created_at: Mapped[float] = mapped_column(default=time.time)


class MemoryHistory(Base):
    __tablename__ = "memory_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    revision: Mapped[int]
    changed_at: Mapped[float] = mapped_column(default=time.time)


class DatasetSnapshot(Base):
    __tablename__ = "dataset_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    manifest: Mapped[str] = mapped_column(Text)
    data_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(default=1)
    state: Mapped[str] = mapped_column(String(20), default="staged")
    reviews: Mapped[str] = mapped_column(Text, default="{}")
    plan: Mapped[str | None] = mapped_column(Text)
    approval_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(default=time.time)


class TrainingJob(Base):
    __tablename__ = "training_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset_snapshots.id"), index=True)
    approval_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    report: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(default=time.time)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(120))
    project: Mapped[str] = mapped_column(String(80), default="")
    digest: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(default=time.time)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int]
    content: Mapped[str] = mapped_column(Text)


class MessageRetrieval(Base):
    __tablename__ = "message_retrievals"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    sources: Mapped[str] = mapped_column(Text)


class CollectionPlan(Base):
    __tablename__ = "collection_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(100))
    interval_hours: Mapped[int] = mapped_column(default=24)
    max_runs: Mapped[int] = mapped_column(default=3)
    runs: Mapped[int] = mapped_column(default=0)
    enabled: Mapped[bool] = mapped_column(default=True)
    state: Mapped[str] = mapped_column(String(20), default="waiting")
    next_run: Mapped[float] = mapped_column(default=time.time, index=True)
    history: Mapped[str] = mapped_column(Text, default="[]")


class AutoApprovalPolicy(Base):
    __tablename__ = "auto_approval_policies"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)
    options: Mapped[str] = mapped_column(Text, default="{}")
    binding: Mapped[str] = mapped_column(String(64), default="")


class AutoApprovalAudit(Base):
    __tablename__ = "auto_approval_audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset_snapshots.id", ondelete="CASCADE"), index=True)
    policy_revision: Mapped[int]
    dataset_revision: Mapped[int]
    state: Mapped[str] = mapped_column(String(20), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="{}")
    approval_hash: Mapped[str | None]
    created_at: Mapped[float] = mapped_column(default=time.time)



class BenchmarkSuite(Base):
    __tablename__ = "benchmark_suites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    cases: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(default=time.time)


class CandidateSelection(Base):
    __tablename__ = "candidate_selections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(default=1)
    history: Mapped[str] = mapped_column(Text, default="[]")
