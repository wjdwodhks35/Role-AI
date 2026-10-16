"""Owner-only benchmark, candidate version and unified job management."""
import json
import math
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from role_ai.artifacts import ArtifactStore, canonical, storage_lock
from role_ai.models import (
    AutoApprovalAudit, BenchmarkSuite, CandidateSelection, CollectionPlan,
    DatasetSnapshot, TrainingJob,
)
from role_ai.quality import BenchmarkInput, suite_hash
from role_ai.training import model_fingerprint


class SelectionInput(BaseModel):
    revision: int = Field(ge=0)
    job_id: str = Field(min_length=36, max_length=36)


class RollbackInput(BaseModel):
    revision: int = Field(ge=1)


def selection_dict(item):
    return {"job_id": item.job_id if item else None,
            "revision": item.revision if item else 0,
            "history": json.loads(item.history) if item else [],
            "scope": "deployment_candidate_only", "serving_changed": False,
            "automatic_activation": False}


def lifecycle_router(authenticated, repository, settings):
    router = APIRouter(prefix="/api/learning", tags=["model-lifecycle"])
    store = ArtifactStore(settings)

    def owner(actor=Depends(authenticated)):
        if not actor[0].is_owner or settings.mode != "local":
            raise HTTPException(403, "모델·작업 관리는 로컬 소유자 전용입니다.")
        return actor[0]

    @router.get("/benchmarks")
    def suites(user=Depends(owner), repo=Depends(repository)):
        return [{"id": s.id, "name": s.name, "digest": s.digest,
                 "cases": json.loads(s.cases)} for s in repo.db.scalars(
                     select(BenchmarkSuite).where(BenchmarkSuite.user_id == user.id)
                     .order_by(BenchmarkSuite.created_at.desc()))]

    @router.post("/benchmarks", status_code=201)
    def create_suite(body: BenchmarkInput, user=Depends(owner), repo=Depends(repository)):
        uid = user.id
        cases = [c.model_dump() for c in body.cases]
        repo.db.rollback()
        repo.db.execute(text("BEGIN IMMEDIATE"))
        count = repo.db.scalar(select(func.count()).select_from(BenchmarkSuite)
                               .where(BenchmarkSuite.user_id == uid))
        if count >= 20:
            raise HTTPException(409, "평가 질문 묶음은 최대 20개입니다.")
        item = BenchmarkSuite(user_id=uid, name=body.name, cases=canonical(cases),
                              digest=suite_hash(cases))
        repo.db.add(item)
        repo.db.commit()
        return {"id": item.id, "name": item.name, "digest": item.digest, "cases": cases}

    @router.get("/dashboard")
    def dashboard(user=Depends(owner), repo=Depends(repository)):
        entries = []
        for p in repo.db.scalars(select(CollectionPlan).where(CollectionPlan.user_id == user.id)):
            entries.append({"id": p.id, "kind": "collection", "state": p.state,
                            "label": p.topic, "attempts": p.runs, "max_attempts": p.max_runs,
                            "next_run": p.next_run, "history": json.loads(p.history)})
        for j in repo.db.scalars(select(TrainingJob).where(TrainingJob.user_id == user.id)
                                .order_by(TrainingJob.created_at.desc()).limit(100)):
            entries.append({"id": j.id, "kind": "training", "state": j.state,
                            "label": j.dataset_id, "created_at": j.created_at,
                            "report": json.loads(j.report)})
        for a in repo.db.scalars(select(AutoApprovalAudit).where(AutoApprovalAudit.user_id == user.id)
                                .order_by(AutoApprovalAudit.created_at.desc()).limit(100)):
            entries.append({"id": a.id, "kind": "gpt_review", "state": a.state,
                            "label": a.dataset_id, "created_at": a.created_at,
                            "report": json.loads(a.detail)})
        from role_ai.workers import worker_views
        return {"entries": entries, "worker_connectivity": "heartbeat",
                "workers": worker_views(repo.db),
                "progress_percent": None,
                "counts": {state: sum(e["state"] == state for e in entries)
                           for state in sorted({e["state"] for e in entries})}}

    @router.get("/versions")
    def versions(user=Depends(owner), repo=Depends(repository)):
        rows = []
        for job in repo.db.scalars(select(TrainingJob).where(
            TrainingJob.user_id == user.id, TrainingJob.state == "evaluated")
            .order_by(TrainingJob.created_at.desc()).limit(100)):
            report = json.loads(job.report)
            rows.append({"job_id": job.id, "dataset_id": job.dataset_id,
                         "report": report, "created_at": job.created_at,
                         "artifact_recorded": bool(report.get("adapter_fingerprint"))})
        return {"versions": rows, "selection": selection_dict(
            repo.db.get(CandidateSelection, user.id))}

    def validate_candidate(repo, uid, jid):
        job = repo.db.scalar(select(TrainingJob).where(
            TrainingJob.id == jid, TrainingJob.user_id == uid))
        if not job:
            raise HTTPException(404, "모델 버전을 찾을 수 없습니다.")
        report = json.loads(job.report)
        if job.state != "evaluated" or report.get("execution") != "gpu_sft_qlora":
            raise HTTPException(409, "실제 GPU 학습과 평가가 완료된 버전만 선택할 수 있습니다.")
        benchmark = report.get("benchmark") or {}
        try:
            before, after = report["baseline_eval_loss"], report["trained_eval_loss"]
            old, new = benchmark["baseline"]["score"], benchmark["trained"]["score"]
            if any(type(x) not in {float, int} or not math.isfinite(x)
                   for x in [before, after, old, new]):
                raise ValueError("Invalid metrics")
            if min(before, after) < 0 or not (0 <= old <= new <= 1) or after > before:
                raise ValueError("Evaluation regressed")
            item = repo.db.get(DatasetSnapshot, job.dataset_id)
            if not item or item.user_id != uid:
                raise ValueError("Dataset missing")
            store.read(uid, item.id, report["data_hash"])
            adapter = store.path(uid, item.id) / ("run-" + job.id) / "adapter"
            if adapter.parent.is_symlink() or adapter.is_symlink():
                raise ValueError("Symbolic adapter")
            if model_fingerprint(adapter) != report["adapter_fingerprint"]:
                raise ValueError("Adapter changed")
        except (ValueError, OSError, KeyError, TypeError):
            raise HTTPException(409, "평가 질문·성능 또는 모델 파일 검증에 실패했습니다.") from None
        return job

    def change(repo, uid, revision, jid, rollback=False):
        try:
            with storage_lock(store.root):
                repo.db.rollback()
                repo.db.execute(text("BEGIN IMMEDIATE"))
                item = repo.db.get(CandidateSelection, uid)
                if (item.revision if item else 0) != revision:
                    raise HTTPException(409, "선택이 변경됐습니다. 새로고침하세요.")
                history = json.loads(item.history) if item else []
                target = jid
                if rollback:
                    if not history:
                        raise HTTPException(409, "이전 배포 후보가 없습니다.")
                    target = history[-1]["previous"]
                    if target is None:
                        raise HTTPException(409, "이전 배포 후보가 없습니다.")
                validate_candidate(repo, uid, target)
                if item and item.job_id == target:
                    raise HTTPException(409, "이미 선택된 버전입니다.")
                history.append({"previous": item.job_id if item else None, "selected": target,
                                "action": "rollback" if rollback else "select",
                                "changed_at": time.time()})
                if not item:
                    item = CandidateSelection(user_id=uid)
                    repo.db.add(item)
                    item.revision = 0
                item.job_id, item.revision = target, revision + 1
                item.history = canonical(history[-100:])
                repo.db.commit()
                return selection_dict(item)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/versions/select")
    def choose(body: SelectionInput, user=Depends(owner), repo=Depends(repository)):
        return change(repo, user.id, body.revision, body.job_id)

    @router.post("/versions/rollback")
    def rollback(body: RollbackInput, user=Depends(owner), repo=Depends(repository)):
        return change(repo, user.id, body.revision, None, True)

    return router
