import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import inspect

from role_ai.artifacts import storage_lock
from role_ai.config import Settings
from role_ai.database import build_database
from role_ai.migrations import MIGRATIONS, MigrationRequired, initialize_database, upgrade_offline
from role_ai.models import CollectionPlan, DatasetSnapshot, TrainingJob, WorkerPresence
from role_ai.training import ResourcesBusy, run_one
from role_ai.workers import WorkerMonitor, worker_views
from test_learning import learning_app as _shared_learning_app, queued
from test_collection import owner as _shared_collection_owner, create as create_plan

learning_app = _shared_learning_app
collection_owner = _shared_collection_owner


def test_worker_heartbeat_stale_and_shutdown(tmp_path, monkeypatch):
    from role_ai import workers
    monkeypatch.setattr(workers, 'HEARTBEAT_SECONDS', .02)
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "db"}')
    with WorkerMonitor(settings, 'training') as monitor:
        monitor.stage('training', completed=2, total=10)
        deadline = time.monotonic() + 2
        initial = time.time()
        while True:
            with monitor.factory() as db:
                seen = db.get(WorkerPresence, monitor.id).last_seen
            if seen > initial:
                break
            assert time.monotonic() < deadline
            time.sleep(.01)
        with monitor.factory() as db:
            live = worker_views(db, now=seen + 1)[0]
            stale = worker_views(db, now=seen + 100)[0]
        assert live['online'] and live['progress_percent'] == 20
        assert stale['online'] is False and stale['phase'] == 'unknown'
        assert stale['progress_percent'] is None
    engine, factory = build_database(settings.database_url)
    with factory() as db:
        assert worker_views(db)[0]['state'] == 'stopped'
    engine.dispose()


@pytest.mark.parametrize("timing", ["checkpoint", "resource_error", "after_result"])
def test_training_live_progress_and_cooperative_cancel(learning_app, timing):
    client, settings, _ = learning_app
    record, job = queued(client)
    def training(plan, train, evaluation, output):
        monitor = plan['_worker_monitor']
        monitor.stage('training', completed=2, total=10)
        rows = client.get('/api/workers').json()['workers']
        assert rows[0]['online'] is True and rows[0]['progress_percent'] == 20
        assert client.post('/api/workers/training/' + job['id'] + '/cancel').status_code == 200
        if timing == 'resource_error':
            raise ResourcesBusy('test resource race')
        if timing == 'after_result':
            return {'execution': 'test_only', 'activation': 'not_activated'}
        monitor.check_cancel()
        pytest.fail('must not finish after cancellation')
    result = run_one(settings, trainer=training)
    assert result['state'] == 'cancelled'
    with client.app.state.database_factory() as db:
        assert db.get(DatasetSnapshot, record['id']).approval_hash is None
    data = client.get('/api/workers').json()
    assert data['workers'][0]['state'] == 'stopped'
    history = next(j['history'] for j in data['jobs'] if j['id'] == job['id'])
    assert [h['action'] for h in history] == ['started', 'cancel_requested', 'cancelled']
    assert client.post('/api/workers/training/' + job['id'] + '/retry').status_code == 409


def test_training_retry_preserves_and_rechecks_approval(learning_app):
    client, settings, _ = learning_app
    record, job = queued(client)
    def blocked(plan, train, evaluation, output):
        (output / 'partial.txt').write_text('keep partial output')
        raise ResourcesBusy('test resource limit')
    assert run_one(settings, trainer=blocked)['state'] == 'blocked'
    path = '/api/workers/training/' + job['id'] + '/retry'
    replacement = client.post(path).json()
    assert replacement['state'] == 'queued' and replacement['job_id'] != job['id']
    def successful(*args):
        return {'execution': 'test_only', 'activation': 'not_activated'}
    assert run_one(settings, trainer=successful)['job_id'] == replacement['job_id']
    assert list(Path(settings.storage_dir).rglob('partial.txt'))[0].read_text() == 'keep partial output'
    with client.app.state.database_factory() as db:
        db.get(TrainingJob, job['id']).state = 'blocked'
        db.get(DatasetSnapshot, record['id']).approval_hash = 'tampered'
        db.commit()
    response = client.post(path)
    assert response.status_code == 409 and '승인' in response.json()['detail']


def test_training_interrupted_recovery_requires_free_lock(learning_app):
    client, settings, _ = learning_app
    record, job = queued(client)
    with client.app.state.database_factory() as db:
        db.get(TrainingJob, job['id']).state = 'running'
        db.commit()
    path = '/api/workers/training/' + job['id'] + '/recover'
    with storage_lock(settings.storage_dir):
        assert client.post(path).status_code == 409
    assert client.post(path).json()['requires_reapproval'] is True
    with client.app.state.database_factory() as db:
        assert db.get(TrainingJob, job['id']).state == 'interrupted'
        assert db.get(DatasetSnapshot, record['id']).approval_hash is None


def test_collection_cancel_retry_and_budget(collection_owner, monkeypatch):
    from role_ai import collection
    client = collection_owner
    settings = client.app.state.settings
    plan = create_plan(client, max_runs=3).json()
    async def collect(*args):
        assert client.post('/api/workers/collection/' + plan['id'] + '/cancel').status_code == 200
        args[-1].check_cancel()
        raise AssertionError('cancel was ignored')
    monkeypatch.setattr(collection, 'collect', collect)
    result = asyncio.run(collection.run_one(settings, client.app.state.database_factory,
                                           client.app.state.providers, None))
    assert result['state'] == 'cancelled'
    path = '/api/workers/collection/' + plan['id'] + '/retry'
    assert client.post(path).json()['state'] == 'waiting'
    with client.app.state.database_factory() as db:
        item = db.get(CollectionPlan, plan['id'])
        assert item.runs == 1 and item.enabled
        item.runs = item.max_runs
        db.commit()
    assert client.post(path).status_code == 409


def test_worker_auth_csrf_and_ownership(signed_in, learning_app):
    assert signed_in.get('/api/workers').status_code == 403
    assert signed_in.post('/api/workers/training/unknown/cancel').status_code == 403
    client, _, _ = learning_app
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/workers/training/unknown/cancel').status_code == 403
    csrf = client.get('/api/auth/me').json()['csrf_token']
    client.headers['X-CSRF-Token'] = csrf
    assert client.post('/api/workers/training/unknown/cancel').status_code == 404
    _, job = queued(client)
    with client.app.state.database_factory() as db:
        # Keep the FK valid while proving owner-scoped lookup does not expose a different user job.
        from role_ai.models import User
        other = User(email='other-worker@example.test', password_hash='not-a-login')
        db.add(other)
        db.flush()
        db.get(TrainingJob, job['id']).user_id = other.id
        db.commit()
    assert client.post('/api/workers/training/' + job['id'] + '/cancel').status_code == 404
    assert job['id'] not in {j['id'] for j in client.get('/api/workers').json()['jobs']}


def test_v1_worker_migration_requires_offline_backup(tmp_path, monkeypatch):
    from role_ai import migrations
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "db"}', storage_dir=str(tmp_path / 'artifacts'))
    engine, _ = build_database(settings.database_url)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'MIGRATIONS', [(1, migrations.BASELINE)])
        initialize_database(engine)
    with pytest.raises(MigrationRequired):
        initialize_database(engine)
    with engine.connect() as db:
        assert 'worker_presence' not in inspect(db).get_table_names()
    engine.dispose()
    assert upgrade_offline(settings, tmp_path / 'backup', offline=True)['current'] == MIGRATIONS[-1][0]
    assert (tmp_path / 'backup/manifest.json').is_file()


def test_actual_worker_cli_idle_no_gpu_or_download(tmp_path):
    env = {**os.environ, 'ROLE_AI_DATABASE_URL': f'sqlite:///{tmp_path / "db"}',
           'ROLE_AI_STORAGE_DIR': str(tmp_path / 'artifacts'), 'ROLE_AI_TRAINING_ENABLED': 'true',
           'ROLE_AI_HF_DOWNLOAD_ENABLED': 'false', 'ROLE_AI_OPENAI_ENABLED': 'false',
           'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, '-m', 'role_ai.training', '--once'], env=env,
                            capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(result.stdout)['state'] == 'idle'
    engine, factory = build_database(env['ROLE_AI_DATABASE_URL'])
    with factory() as db:
        assert worker_views(db)[0]['kind'] == 'training'
        assert worker_views(db)[0]['state'] == 'stopped'
    engine.dispose()
