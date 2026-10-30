"""v6 approval evidence; neither a contract nor an approval ID is a credential."""

import time
from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from role_ai.database import Base


class TaskApproval(Base):
    __tablename__ = "task_approvals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    session_hash: Mapped[str] = mapped_column(
        String(64)
    )  # historical, deliberately not an FK: logout must work
    task_id: Mapped[str] = mapped_column(String(64))
    contract_digest: Mapped[str] = mapped_column(String(64))
    contract_json: Mapped[str] = mapped_column(Text)
    environment_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(default=time.time)
    expires_at: Mapped[float]
    revoked_at: Mapped[float | None]
    __table_args__ = (
        CheckConstraint(
            "length(id) BETWEEN 1 AND 64 AND id NOT GLOB '*[^A-Za-z0-9_-]*'",
            name="task_approval_id",
        ),
        CheckConstraint(
            "length(contract_digest)=64 AND contract_digest NOT GLOB '*[^0-9a-f]*'",
            name="task_approval_digest",
        ),
        CheckConstraint(
            "length(environment_digest)=64 AND environment_digest NOT GLOB '*[^0-9a-f]*'",
            name="task_approval_environment",
        ),
        CheckConstraint(
            "expires_at>created_at AND expires_at-created_at<=3600", name="task_approval_expiry"
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at>=created_at", name="task_approval_revocation"
        ),
        Index("ix_task_approval_owner", "user_id", "task_id"),
    )
