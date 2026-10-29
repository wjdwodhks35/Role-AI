"""Local-owner Coding API; existing session, origin and CSRF checks are reused."""

import json

from fastapi import APIRouter, Depends, HTTPException

from role_ai.coding_agent import runtime_identity
from role_ai.coding_jobs import CodingJobError, CodingJobStore, JobBusy, TaskCreate, checksum
from role_ai.coding_project import cumulative_diff, repository
from role_ai.coding_runner import PINNED_IMAGE
from role_ai.coding_tasks import Contract
from role_ai.coding_workspace import git


class ResumeRequest(Contract):
    approved: bool = False
    acknowledge_unknown: bool = False


def task_view(state, *, detail=False):
    result = {key: state.get(key) for key in (
        "id", "status", "phase", "created_at", "updated_at", "steps", "calls", "tokens",
        "active_seconds", "retries", "repo_revision", "revision", "tested_revision", "recorder_gap", "error",
    )}
    usage = state.get("usage") or {}
    result.update(repository_id=state["spec"]["repository_id"], budget=state["spec"]["budget"],
                  model=state["spec"].get("model"), pending=state.get("pending"), progress=None,
                  usage={k: v for k, v in usage.items() if k != "calls"} or None)
    if detail:
        result.update(allowed_files=state["spec"]["allowed_files"], command=state["spec"]["command"])
        result.update(request=state["spec"]["request"], plan=state["plan"], history=state["history"],
                      last_test=state.get("last_test"), report=state.get("report"))
        if state.get("files"):
            result["patch"] = cumulative_diff(state)
    return result


def coding_router(authenticated, settings):
    router = APIRouter(prefix="/api/coding", tags=["coding"])

    def owner(actor=Depends(authenticated)):
        if not actor[0].is_owner or settings.mode != "local":
            raise HTTPException(403, "Coding Agent는 로컬 소유자 전용입니다.")
        return actor[0]

    def store():
        if not settings.coding_enabled:
            raise HTTPException(503, "Coding Agent가 비활성화되어 있습니다.")
        return CodingJobStore(settings.coding_root)

    @router.get("/status")
    def status(user=Depends(owner)):
        return {"enabled": settings.coding_enabled, "repositories": list(settings.coding_repositories),
                "gpu_concurrency": 1, "execution": "separate_local_worker", "auto_deploy": False}

    @router.get("/tasks")
    def listing(user=Depends(owner)):
        try:
            return {"tasks": [task_view(state) for state in store().jobs(user.id)]}
        except CodingJobError:
            raise HTTPException(409, "체크포인트를 확인하세요.") from None

    @router.post("/tasks", status_code=201)
    def create(body: dict, user=Depends(owner)):
        try:
            parsed = TaskCreate.model_validate_json(json.dumps(body))
        except (ValueError, TypeError):
            raise HTTPException(422, "작업 범위·예산·동의 입력을 확인하세요.") from None
        if not parsed.approved:
            raise HTTPException(400, "허용 파일·명령·예산 및 로컬 모델 전송 승인이 필요합니다.")
        active = store()
        try:
            source = repository(settings, parsed.repository_id)
            if not PINNED_IMAGE.fullmatch(settings.coding_image):
                raise CodingJobError("Configure pinned coding image")
            revision = git(source, "rev-parse", "HEAD^{commit}").decode().strip()
            state = active.create(user.id, parsed, revision, checksum(str(source)),
                                  runtime_hash=runtime_identity(settings, parsed.repository_id))
            return task_view(state)
        except (CodingJobError, ValueError, OSError):
            raise HTTPException(409, "등록 저장소·격리 이미지·작업 저장 경로를 확인하세요.") from None

    @router.get("/tasks/{task_id}")
    def get(task_id: str, user=Depends(owner)):
        try:
            _, state = store().load(user.id, task_id)
            return task_view(state, detail=True)
        except (CodingJobError, ValueError):
            raise HTTPException(404, "작업을 찾을 수 없거나 체크포인트가 손상되었습니다.") from None

    @router.post("/tasks/{task_id}/{action}")
    def control(task_id: str, action: str, body: ResumeRequest, user=Depends(owner)):
        active = store()
        try:
            if action in {"pause", "cancel"}:
                active.request_control(user.id, task_id, action)
                return {"requested": action, "notice": "실행 중인 제한된 단계가 끝난 뒤 반영됩니다."}
            if action == "resume":
                if not body.approved:
                    raise HTTPException(400, "동일 범위 재개 승인이 필요합니다.")
                return task_view(active.resume(user.id, task_id, acknowledge_unknown=body.acknowledge_unknown))
            raise HTTPException(404, "알 수 없는 작업 제어입니다.")
        except JobBusy:
            raise HTTPException(409, "작업이 실행 중입니다. 먼저 일시중지를 요청하세요.") from None
        except (CodingJobError, ValueError):
            raise HTTPException(409, "작업 상태·체크포인트 또는 불명확한 실행 결과를 확인하세요.") from None

    return router
