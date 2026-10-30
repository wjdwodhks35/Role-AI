"""Authenticated metadata/control endpoints; no execution or arbitrary status API."""

import json
from sqlalchemy.exc import SQLAlchemyError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from role_ai.task_checkpoints import TaskLifecycle, TaskStateError
from role_ai.task_approvals import ApprovalDenied
from role_ai.task_contracts import TaskContract
from role_ai.coding_jobs import CodingJobError


class ContractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: dict


class QueueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=0)


class ControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(pause|cancel)$")


def task_state_router(authenticated, repository, settings):
    router = APIRouter(prefix="/api/contract-tasks", tags=["contract-tasks"])

    def call(repo, operation):
        if settings.mode != "local":
            raise HTTPException(403, "로컬 작업 상태만 지원합니다.")
        try:
            return operation(TaskLifecycle(repo.db, settings))
        except (
            ApprovalDenied,
            TaskStateError,
            CodingJobError,
            ValueError,
            OSError,
            SQLAlchemyError,
        ):
            raise HTTPException(409, "작업 상태 또는 현재 권한을 확인할 수 없습니다.") from None

    def receipt(value):
        return value["state"].model_dump(mode="json")

    @router.post("", status_code=201)
    def create(body: ContractInput, actor=Depends(authenticated), repo=Depends(repository)):
        return call(
            repo,
            lambda lifecycle: receipt(
                lifecycle.create(
                    actor[1].token_hash, TaskContract.model_validate_json(json.dumps(body.contract))
                )
            ),
        )

    @router.get("/{task_id}")
    def read(task_id: str, actor=Depends(authenticated), repo=Depends(repository)):
        return call(repo, lambda lifecycle: receipt(lifecycle.owned(actor[1].token_hash, task_id)))

    @router.post("/{task_id}/queue")
    def queue(
        task_id: str, body: QueueInput, actor=Depends(authenticated), repo=Depends(repository)
    ):
        return call(
            repo,
            lambda lifecycle: receipt(lifecycle.queue(actor[1].token_hash, task_id, body.revision)),
        )

    @router.post("/{task_id}/control")
    def control(
        task_id: str, body: ControlInput, actor=Depends(authenticated), repo=Depends(repository)
    ):
        return call(
            repo, lambda lifecycle: lifecycle.control(actor[1].token_hash, task_id, body.action)
        )

    return router
