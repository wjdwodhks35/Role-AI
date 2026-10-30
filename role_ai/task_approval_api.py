"""Authenticated, CSRF-protected approval control plane, never model output."""

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from role_ai.task_contracts import TaskContract
from role_ai.task_approvals import ApprovalService, ApprovalDenied


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: dict
    expires_in: int = Field(default=900, strict=True, ge=1, le=3600)


def approval_router(authenticated, repository, settings):
    router = APIRouter(prefix="/api/task-approvals", tags=["task-approvals"])

    def receipt(row):
        return {
            "id": row.id,
            "task_id": row.task_id,
            "contract_digest": row.contract_digest,
            "expires_at": row.expires_at,
            "revoked_at": row.revoked_at,
        }

    @router.post("", status_code=201)
    def issue(body: ApprovalInput, actor=Depends(authenticated), repo=Depends(repository)):
        try:
            contract = TaskContract.model_validate_json(json.dumps(body.contract))
            row = ApprovalService(repo.db, settings).issue(
                actor[1].token_hash, contract, body.expires_in
            )
            result = receipt(row)
            repo.db.commit()
            return result
        except (ApprovalDenied, ValueError, SQLAlchemyError, OSError):
            repo.db.rollback()
            raise HTTPException(409, "승인 조건을 확인할 수 없습니다.") from None

    @router.delete("/{reference}")
    def revoke(reference: str, actor=Depends(authenticated), repo=Depends(repository)):
        try:
            row = ApprovalService(repo.db, settings).revoke(actor[1].token_hash, reference)
            result = receipt(row)
            repo.db.commit()
            return result
        except (ApprovalDenied, SQLAlchemyError):
            repo.db.rollback()
            raise HTTPException(404, "승인 기록을 찾을 수 없습니다.") from None

    return router
