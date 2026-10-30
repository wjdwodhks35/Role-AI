"""Authenticated owner control plane, never an Agent execution credential."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from role_ai.models import Project, ProjectReadGrant, ResourceVersion, ScopePolicy
from role_ai.task_contracts import ResourceRef
from role_ai.project_scopes import ScopeRepository, ScopeError
from role_ai.scope_data import project_id, resource_for


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


def scope_router(authenticated, repository):
    router = APIRouter(prefix="/api/projects", tags=["project-scopes"])

    @router.get("")
    def listing(actor=Depends(authenticated), repo=Depends(repository)):
        policy = repo.db.get(ScopePolicy, actor[0].id)
        rows = repo.db.scalars(select(Project).where(Project.user_id == actor[0].id))
        return {
            "policy_generation": policy.generation if policy else None,
            "projects": [{"id": p.id, "name": p.legacy_name, "state": p.state} for p in rows],
        }

    @router.post("", status_code=201)
    def create(body: ProjectInput, actor=Depends(authenticated), repo=Depends(repository)):
        p = ScopeRepository(repo.db).create_project(actor[0].id, body.name)
        repo.db.commit()
        return {"id": p.id, "name": p.legacy_name, "state": p.state}

    @router.post("/{pid}/read-grants", status_code=201)
    def share(pid: str, body: ResourceRef, actor=Depends(authenticated), repo=Depends(repository)):
        uid = actor[0].id
        project_id(repo.db, uid, explicit=pid)
        if body.kind not in {"document", "memory"}:
            raise HTTPException(422, "문서·기억의 정확한 버전만 공유할 수 있습니다.")
        resource = resource_for(repo.db, body.kind, body.resource_id)
        if not resource or resource.user_id != uid:
            raise HTTPException(404, "자료를 찾을 수 없습니다.")
        version = repo.db.scalar(
            select(ResourceVersion).where(
                ResourceVersion.resource_id == resource.id, ResourceVersion.version == body.version
            )
        )
        if not version:
            raise HTTPException(404, "자료 버전을 찾을 수 없습니다.")
        try:
            grant = ScopeRepository(repo.db).grant_read(uid, version.id, pid)
        except ScopeError:
            raise HTTPException(409, "공유할 수 없는 자료 버전입니다.") from None
        repo.db.commit()
        return {
            "id": grant.id,
            "permission": grant.permission,
            "version_id": grant.version_id,
            "project_id": pid,
        }

    @router.delete("/{pid}/read-grants/{gid}", status_code=204)
    def revoke(pid: str, gid: str, actor=Depends(authenticated), repo=Depends(repository)):
        uid = actor[0].id
        project_id(repo.db, uid, explicit=pid)
        g = repo.db.get(ProjectReadGrant, gid)
        if not g or g.project_id != pid or g.granted_by != uid:
            raise HTTPException(404, "공유 기록을 찾을 수 없습니다.")
        ScopeRepository(repo.db).revoke_read(uid, gid)
        repo.db.commit()

    return router
