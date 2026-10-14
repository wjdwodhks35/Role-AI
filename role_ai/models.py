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
