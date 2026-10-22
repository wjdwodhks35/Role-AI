import json

import pytest

from role_ai import learning, rlaif
from role_ai.ai_judge import AIJudge, audit_digest
from role_ai.artifacts import ArtifactStore, canonical
from role_ai.models import TrainingJob
from role_ai.training import model_fingerprint, run_one
from test_ai_judge import transport
from test_learning import approval, learning_app as _shared, staged
from test_quality_lifecycle import candidate, choose, suite

learning_app = _shared


def setup(client, settings, monkeypatch, **changes):
    settings.rlaif_enabled = True
    settings.rlaif_judge_model = 'judge:test'
    def factory(*a, **kw):
        return AIJudge(*a, **kw, transport=transport())
    monkeypatch.setattr(learning, 'AIJudge', factory)
    record = staged(client)
    for stage in ['teacher', 'reviewer']:
        assert client.post(f"/api/learning/datasets/{record['id']}/review", json={'stage': stage}).status_code == 200
    fixed = suite(client)
    response = approval(client, record, method='rlaif_grpo_qlora', rlaif_approved=True,
                        max_steps=2, benchmark_id=fixed['id'], **changes)
    return record, response


def test_rlaif_approval_without_paid_orchestrator(learning_app, monkeypatch):
    client, settings, paid_calls = learning_app
    record, result = setup(client, settings, monkeypatch)
    assert result.status_code == 200, result.text
    plan = result.json()['plan']
    assert plan['method'] == 'rlaif_grpo_qlora' and plan['gradient_accumulation'] == 2
    assert plan['rlaif_judge']['digest'] == 'b' * 64 and plan['rlaif_calibration']['passed'] is True
    assert paid_calls == []
    assert 'orchestrator' not in result.json()['reviews']
    assert client.post(f"/api/learning/datasets/{record['id']}/enqueue").status_code == 201


@pytest.mark.parametrize('change,code', [({'rlaif_approved': False}, 403),
    ({'benchmark_id': None}, 422), ({'max_steps': 21}, 422),
    ({'max_steps': 10, 'rlaif_max_judge_calls': 5}, 422), ({'method': 'rlhf'}, 422)])
def test_rlaif_plan_gates(learning_app, monkeypatch, change, code):
    client, settings, _ = learning_app
    record, good = setup(client, settings, monkeypatch)
    assert good.status_code == 200
    body = {'method': 'rlaif_grpo_qlora', 'rlaif_approved': True, 'max_steps': 2,
            'benchmark_id': good.json()['plan']['benchmark_id'], **change}
    assert approval(client, record, **body).status_code == code


def test_disabled_rlaif_does_not_reuse_sft_permission(learning_app, monkeypatch):
    client, settings, _ = learning_app
    record, result = setup(client, settings, monkeypatch)
    assert result.status_code == 200
    settings.rlaif_enabled = False
    assert client.post(f"/api/learning/datasets/{record['id']}/enqueue").status_code == 403
    assert approval(client, record, method='rlaif_grpo_qlora', rlaif_approved=True).status_code == 403


@pytest.mark.parametrize('mode', ['schema', 'all_good'])
def test_failed_calibration_preserves_previous_approval(learning_app, monkeypatch, mode):
    client, settings, _ = learning_app
    record, good = setup(client, settings, monkeypatch)
    monkeypatch.setattr(learning, 'AIJudge', lambda *a, **kw: AIJudge(*a, **kw, transport=transport(mode)))
    response = approval(client, record, method='rlaif_grpo_qlora', rlaif_approved=True,
                        max_steps=2, benchmark_id=good.json()['plan']['benchmark_id'])
    assert response.status_code == 503
    current = client.get('/api/learning/datasets').json()[0]
    assert current['approval_hash'] == good.json()['approval_hash']


def test_worker_dispatches_current_policy_training_without_activation(learning_app, monkeypatch):
    client, settings, _ = learning_app
    record, approved = setup(client, settings, monkeypatch)
    assert approved.status_code == 200
    assert client.post(f"/api/learning/datasets/{record['id']}/enqueue").status_code == 201
    calls = []
    def trainer(plan, train, evaluation, output):
        calls.append(plan['method'])
        assert plan['rlaif_judge']['digest'] == 'b' * 64
        assert not {c['prompt'] for c in plan['benchmark']['cases']} & {r['prompt'] for r in train}
        assert evaluation and plan['_rlaif_settings'] is settings
        return {'execution': 'synthetic_grpo_dispatch_test', 'activation': 'not_activated'}
    monkeypatch.setattr(rlaif, 'run_rlaif', trainer)
    result = run_one(settings)
    assert result['state'] == 'evaluated' and calls == ['rlaif_grpo_qlora']
    assert choose(client, result['job_id'], 0).status_code == 409


def test_worker_rejects_revoked_rlaif_before_training(learning_app, monkeypatch):
    client, settings, _ = learning_app
    record, _ = setup(client, settings, monkeypatch)
    assert client.post(f"/api/learning/datasets/{record['id']}/enqueue").status_code == 201
    settings.rlaif_enabled = False
    def forbidden(*args):
        pytest.fail('Revoked policy must not train')
    assert run_one(settings, trainer=forbidden)['state'] == 'failed'


def test_parent_sft_adapter_is_pinned_and_revalidated(learning_app, monkeypatch):
    client, settings, _ = learning_app
    jid, adapter = candidate(client, settings)
    with client.app.state.database_factory() as db:
        job = db.get(TrainingJob, jid)
        report = json.loads(job.report)
        report['base_model_fingerprint'] = model_fingerprint(settings.training_base_model_path)
        job.report = canonical(report)
        db.commit()
    record, result = setup(client, settings, monkeypatch, parent_job_id=jid)
    assert result.status_code == 200, result.text
    assert result.json()['plan']['parent_adapter_fingerprint'] == model_fingerprint(adapter)
    assert client.post(f"/api/learning/datasets/{record['id']}/enqueue").status_code == 201
    (adapter / 'model.safetensors').write_bytes(b'tampered')
    def forbidden(*args):
        pytest.fail('Changed parent must not train')
    assert run_one(settings, trainer=forbidden)['state'] == 'failed'


def test_preview_requires_consent_owner_and_does_not_enqueue(learning_app, monkeypatch):
    client, settings, paid_calls = learning_app
    settings.rlaif_enabled, settings.rlaif_judge_model = True, 'judge:test'
    monkeypatch.setattr(learning, 'AIJudge', lambda *a, **kw: AIJudge(*a, **kw, transport=transport()))
    body = {'prompt': '2+2?', 'completion': '4', 'approved': True}
    assert client.post('/api/learning/rlaif/judge-preview', json={**body, 'approved': False}).status_code == 400
    response = client.post('/api/learning/rlaif/judge-preview', json=body)
    assert response.status_code == 200 and response.json()['training_started'] is False
    assert client.get('/api/learning/jobs').json() == [] and paid_calls == []
    token = client.headers.pop('X-CSRF-Token')
    assert client.post('/api/learning/rlaif/judge-preview', json=body).status_code == 403
    client.headers['X-CSRF-Token'] = token
    registered = client.post('/api/auth/register', json={'email': 'member@example.test', 'password': 'safe-member-password-123'})
    client.headers['X-CSRF-Token'] = registered.json()['csrf_token']
    assert client.post('/api/learning/rlaif/judge-preview', json=body).status_code == 403


@pytest.mark.parametrize('failure', ['unchanged', 'no_steps', 'low_benchmark', 'bad_audit', 'no_calibration', 'valid'])
def test_candidate_rejects_unqualified_rlaif(learning_app, failure):
    client, settings, _ = learning_app
    jid, adapter = candidate(client, settings)
    audit = adapter.parent / 'rewards.jsonl'
    audit.write_text('{}\n{}\n')
    with client.app.state.database_factory() as db:
        job = db.get(TrainingJob, jid)
        report = json.loads(job.report)
        report.update(execution='gpu_rlaif_grpo_qlora', optimizer_steps=2, policy_changed=True,
                      reward_records=2, calibration={'passed': True}, minimum_benchmark_score=0.5,
                      reward_audit_hash=audit_digest(audit))
        if failure == 'unchanged':
            report['policy_changed'] = False
        elif failure == 'no_steps':
            report['optimizer_steps'] = 0
        elif failure == 'low_benchmark':
            report['benchmark']['trained']['score'] = 0
        elif failure == 'bad_audit':
            audit.write_text('changed')
        elif failure == 'no_calibration':
            report['calibration']['passed'] = False
        job.report = canonical(report)
        db.commit()
    response = choose(client, jid, 0)
    assert response.status_code == (200 if failure == 'valid' else 409)
    if failure == 'valid':
        assert response.json()['serving_changed'] is False


def test_reward_history_is_owner_bound_and_hash_checked(learning_app):
    client, settings, _ = learning_app
    jid, adapter = candidate(client, settings)
    audit = adapter.parent / 'rewards.jsonl'
    audit.write_text('{"prompt":"synthetic","reward":1}\n')
    with client.app.state.database_factory() as db:
        job = db.get(TrainingJob, jid)
        report = json.loads(job.report)
        report['reward_audit_hash'] = audit_digest(audit)
        job.report = canonical(report)
        db.commit()
    result = client.get(f'/api/learning/jobs/{jid}/rewards')
    assert result.status_code == 200 and result.json()['count'] == 1
    audit.write_text('tampered')
    assert client.get(f'/api/learning/jobs/{jid}/rewards').status_code == 409
    assert client.get('/api/learning/jobs/missing/rewards').status_code == 404
    uid = client.get('/api/auth/me').json()['user']['id']
    assert ArtifactStore(settings).path(uid, client.get('/api/learning/jobs').json()[0]['dataset_id']).exists()


def test_grpo_generation_settings_and_long_prompt_rejection():
    plan = {'max_steps': 2, 'rlaif_max_completion_length': 64}
    config = rlaif.grpo_options(plan, '/tmp/not-written')
    assert config['num_generations'] == config['generation_batch_size'] == 2
    assert config['use_vllm'] is False and config['beta'] > 0
    class Tokenizer:
        def apply_chat_template(self, *args, **kwargs):
            return [0] * 500
    with pytest.raises(ValueError):
        rlaif.prepare_prompts([{'prompt': 'long', 'completion': 'answer'}], Tokenizer(), 'role', 512, 64)


def test_resource_retry_preserves_partial_attempt(learning_app, monkeypatch):
    from role_ai.training import ResourcesBusy
    client, settings, _ = learning_app
    record, _ = setup(client, settings, monkeypatch)
    job = client.post(f"/api/learning/datasets/{record['id']}/enqueue").json()
    def blocked(plan, train, evaluation, output):
        (output / 'rewards.jsonl').write_text('preserved partial attempt')
        raise ResourcesBusy('fixture memory busy')
    result = run_one(settings, trainer=blocked)
    assert result['state'] == 'blocked' and result['report']['partial_output'] == 'run-' + job['id']
    assert client.post(f"/api/learning/jobs/{job['id']}/retry").status_code == 200
    def successful(plan, train, evaluation, output):
        assert list(output.iterdir()) == []
        archived = list(output.parent.glob(output.name + '-attempt-*'))
        assert len(archived) == 1
        assert (archived[0] / 'rewards.jsonl').read_text() == 'preserved partial attempt'
        return {'execution': 'synthetic_retry', 'activation': 'not_activated'}
    assert run_one(settings, trainer=successful)['state'] == 'evaluated'
