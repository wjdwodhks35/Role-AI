"""Measured worker presence and cooperative controls; no process-killing or implicit approval."""
import json
import signal
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text

from role_ai.artifacts import canonical, digest, storage_lock
from role_ai.backup import sqlite_path
from role_ai.database import build_database
from role_ai.migrations import initialize_database
from role_ai.models import CollectionPlan, DatasetSnapshot, TrainingJob, WorkerControl, WorkerPresence

HEARTBEAT_SECONDS = 5
STALE_SECONDS = 20


class JobCancelled(Exception):
    pass


class WorkerMonitor:
    def __init__(self, settings, kind):
        if kind not in {'training', 'collection'}:
            raise ValueError('알 수 없는 Worker 종류')
        sqlite_path(settings)
        self.settings, self.kind = settings, kind
        self.id = str(uuid4())
        self.engine, self.factory = build_database(settings.database_url)
        self.halt, self.stopping = threading.Event(), threading.Event()
        self.mutex = threading.Lock()
        self.current = {'state': 'idle', 'phase': 'waiting', 'job_id': None,
                        'user_id': None, 'completed': None, 'total': None}
        self.thread = None
        self.old_signals = {}

    def __enter__(self):
        try:
            initialize_database(self.engine)
        except BaseException:
            self.engine.dispose()
            raise
        now = time.time()
        with self.factory() as db:
            db.add(WorkerPresence(id=self.id, kind=self.kind, state='idle', phase='waiting',
                                 started_at=now, last_seen=now))
            db.commit()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def _loop(self):
        while not self.halt.wait(HEARTBEAT_SECONDS):
            try:
                self.pulse()
            except Exception:
                # A failed heartbeat is not reported as online; last_seen simply becomes stale.
                pass

    def pulse(self):
        with self.mutex:
            current = dict(self.current)
        with self.factory() as db:
            row = db.get(WorkerPresence, self.id)
            for key, value in current.items():
                setattr(row, key, value)
            row.last_seen = time.time()
            if self.stopping.is_set() and row.state not in {'stopped', 'failed'}:
                row.state = 'stopping'
            db.commit()

    def stage(self, phase, *, completed=None, total=None):
        with self.mutex:
            self.current.update(phase=phase, completed=completed, total=total)
        self.pulse()
        self.check_cancel()

    def start_job(self, job_id, user_id):
        with self.mutex:
            self.current.update(state='running', phase='claimed', job_id=job_id,
                                user_id=user_id, completed=None, total=None)
        with self.factory() as db:
            audit_control(db, self.kind, job_id, user_id, 'started')
            db.commit()
        self.pulse()

    def record_result(self, state, report):
        with self.mutex:
            job_id, user_id = self.current['job_id'], self.current['user_id']
        if job_id:
            with self.factory() as db:
                audit_control(db, self.kind, job_id, user_id, state, report)
                db.commit()

    def finish_job(self):
        with self.mutex:
            self.current.update(state='idle', phase='waiting', job_id=None, user_id=None,
                                completed=None, total=None)
        self.pulse()

    def check_cancel(self):
        if self.stopping.is_set():
            raise JobCancelled('Worker 종료 요청')
        with self.mutex:
            job_id = self.current['job_id']
        if job_id:
            with self.factory() as db:
                control = db.get(WorkerControl, (self.kind, job_id))
                if control and control.cancel_requested:
                    raise JobCancelled('사용자 취소 요청')

    def install_signals(self):
        def stop(signum, frame):
            self.stopping.set()
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.old_signals[signum] = signal.signal(signum, stop)

    def __exit__(self, error_type, error, traceback):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=20)
        for signum, handler in self.old_signals.items():
            signal.signal(signum, handler)
        with self.mutex:
            self.current.update(state='failed' if error_type else 'stopped',
                                phase='error' if error_type else 'stopped', completed=None, total=None)
        try:
            self.pulse()
        finally:
            self.engine.dispose()


def worker_views(db, now=None):
    now = time.time() if now is None else now
    rows = db.scalars(select(WorkerPresence).order_by(WorkerPresence.last_seen.desc()).limit(100))
    result = []
    for row in rows:
        age = max(0, now - row.last_seen)
        online = row.state not in {'stopped', 'failed'} and age <= STALE_SECONDS
        measured = online and row.total is not None and row.total > 0 and row.completed is not None
        result.append({'id': row.id, 'kind': row.kind, 'state': row.state if online else (
                       row.state if row.state in {'stopped', 'failed'} else 'offline'),
                       'online': online, 'heartbeat_age_seconds': round(age, 1),
                       'last_seen': row.last_seen, 'started_at': row.started_at,
                       'job_id': row.job_id, 'phase': row.phase if online else 'unknown',
                       'last_phase': row.phase, 'completed': row.completed if measured else None,
                       'total': row.total if measured else None,
                       'progress_percent': round(min(100, 100 * row.completed / row.total), 1) if measured else None})
    return result


def audit_control(db, kind, job_id, user_id, action, previous=None):
    row = db.get(WorkerControl, (kind, job_id))
    if row is None:
        row = WorkerControl(kind=kind, job_id=job_id, user_id=user_id, history='[]', cancel_requested=False)
        db.add(row)
    history = json.loads(row.history)
    history.append({'action': action, 'time': time.time(), 'previous': previous})
    row.history = canonical(history[-50:])
    return row


def revoke_dataset(db, job):
    item = db.get(DatasetSnapshot, job.dataset_id)
    if item:
        item.state, item.plan, item.approval_hash = 'reviewed', None, None


def workers_router(authenticated, repository, settings):
    router = APIRouter(prefix='/api/workers')

    def owner(actor=Depends(authenticated)):
        if not actor[0].is_owner or settings.mode != 'local':
            raise HTTPException(403, '로컬 소유자만 Worker를 관리할 수 있습니다.')
        return actor[0]

    @router.get('')
    def listing(user=Depends(owner), repo=Depends(repository)):
        workers = worker_views(repo.db)
        jobs = []
        for kind, model in [('training', TrainingJob), ('collection', CollectionPlan)]:
            for item in repo.db.scalars(select(model).where(model.user_id == user.id).limit(100)):
                control = repo.db.get(WorkerControl, (kind, item.id))
                jobs.append({'id': item.id, 'kind': kind, 'state': item.state,
                             'cancel_requested': bool(control and control.cancel_requested),
                             'history': json.loads(control.history) if control else [],
                             'report': json.loads(item.report) if kind == 'training' else json.loads(item.history)})
        return {'workers': workers, 'jobs': jobs, 'stale_after_seconds': STALE_SECONDS,
                'notice': '온라인은 최근 heartbeat 기준이며 GPU·모델 품질 보증이 아닙니다. 취소는 다음 체크포인트에서 반영됩니다.'}

    @contextmanager
    def locked(repo):
        repo.db.execute(text('BEGIN IMMEDIATE'))
        try:
            yield
            repo.db.commit()
        except BaseException:
            repo.db.rollback()
            raise

    def get_job(repo, kind, job_id, user):
        model = {'training': TrainingJob, 'collection': CollectionPlan}.get(kind)
        if model is None:
            raise HTTPException(404, '알 수 없는 작업 종류입니다.')
        item = repo.db.scalar(select(model).where(model.id == job_id, model.user_id == user.id))
        if item is None:
            raise HTTPException(404, '작업을 찾을 수 없습니다.')
        return item

    @router.post('/{kind}/{job_id}/cancel')
    def cancel(kind: str, job_id: str, user=Depends(owner), repo=Depends(repository)):
        with locked(repo):
            item = get_job(repo, kind, job_id, user)
            if item.state not in {'queued', 'running', 'blocked', 'waiting'}:
                raise HTTPException(409, '이 상태에서는 취소할 수 없습니다.')
            control = audit_control(repo.db, kind, job_id, user.id, 'cancel_requested')
            control.cancel_requested = True
            if kind == 'training' and item.state != 'running':
                item.state = 'cancelled'
                item.report = canonical({'reason': '사용자 취소', 'activation': 'not_activated'})
                revoke_dataset(repo.db, item)
            elif kind == 'collection':
                item.enabled = False
                if item.state != 'running':
                    item.state = 'cancelled'
                    history = json.loads(item.history)
                    history.append({'state': 'cancelled', 'reason': '시작 전 사용자 취소', 'time': time.time()})
                    item.history = canonical(history[-30:])
        return {'state': item.state, 'cancel_requested': True}

    @router.post('/{kind}/{job_id}/retry')
    def retry(kind: str, job_id: str, user=Depends(owner), repo=Depends(repository)):
        with locked(repo):
            item = get_job(repo, kind, job_id, user)
            if kind == 'training':
                if not settings.training_enabled or item.state != 'blocked':
                    raise HTTPException(409, '자원 대기(blocked) 작업만 재시도할 수 있습니다. 실패·취소·중단 작업은 다시 승인하세요.')
                dataset = repo.db.get(DatasetSnapshot, item.dataset_id)
                try:
                    valid = dataset and dataset.approval_hash == item.approval_hash and dataset.approval_hash == digest({
                        'plan': json.loads(dataset.plan), 'reviews': json.loads(dataset.reviews), 'data_hash': dataset.data_hash})
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    raise HTTPException(409, '학습 승인이 변경됐습니다. 다시 승인하세요.')
                active = repo.db.scalar(select(TrainingJob).where(
                    TrainingJob.user_id == user.id, TrainingJob.dataset_id == item.dataset_id,
                    TrainingJob.state.in_(['queued', 'running'])))
                if active:
                    raise HTTPException(409, '동일 데이터의 대기/실행 작업이 이미 있습니다.')
                control = audit_control(repo.db, kind, job_id, user.id, 'retry', json.loads(item.report))
                replacement = TrainingJob(user_id=user.id, dataset_id=item.dataset_id,
                                          approval_hash=item.approval_hash, state='queued')
                repo.db.add(replacement)
                repo.db.flush()
                audit_control(repo.db, kind, replacement.id, user.id, 'retry_from', {'job_id': item.id})
                item.state, dataset.state = 'retried', 'queued'
            else:
                history = json.loads(item.history)
                if (not settings.hf_download_enabled or item.state == 'running' or item.runs >= item.max_runs
                        or not history or history[-1].get('state') not in {'failed', 'cancelled'}):
                    raise HTTPException(409, '실패·취소 이력과 남은 수집 횟수, 다운로드 설정을 확인하세요.')
                control = audit_control(repo.db, kind, job_id, user.id, 'retry', history[-1])
                item.state, item.enabled, item.next_run = 'waiting', True, time.time()
            control.cancel_requested = False
        return {'state': 'queued' if kind == 'training' else item.state,
                'job_id': replacement.id if kind == 'training' else item.id,
                'replaces': item.id if kind == 'training' else None, 'automatic_retry': False}

    @router.post('/training/{job_id}/recover')
    def recover(job_id: str, user=Depends(owner), repo=Depends(repository)):
        try:
            with storage_lock(settings.storage_dir):
                with locked(repo):
                    item = get_job(repo, 'training', job_id, user)
                    if item.state != 'running':
                        raise HTTPException(409, 'running 상태의 중단 작업만 복구할 수 있습니다.')
                    audit_control(repo.db, 'training', job_id, user.id, 'recover_interrupted', json.loads(item.report))
                    for presence in repo.db.scalars(select(WorkerPresence).where(
                            WorkerPresence.kind == 'training', WorkerPresence.job_id == job_id)):
                        presence.state, presence.phase = 'failed', 'interrupted'
                        presence.completed, presence.total = None, None
                    item.state = 'interrupted'
                    item.report = canonical({'reason': 'Worker 저장소 잠금 해제 확인 후 중단 처리', 'activation': 'not_activated'})
                    revoke_dataset(repo.db, item)
        except ValueError:
            raise HTTPException(409, '실행 중인 저장소 작업이 있습니다. 강제 복구하지 않습니다.') from None
        return {'state': 'interrupted', 'requires_reapproval': True}

    return router
