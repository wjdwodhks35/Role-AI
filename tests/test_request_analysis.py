import asyncio
import copy
import json

import httpx
import pytest
from sqlalchemy import select

from role_ai.models import Message, TrainingJob
from role_ai.providers import OllamaProvider
from role_ai.requirements_cases import builtin_suite
from role_ai.request_analysis import recommend
from role_ai.requirements_eval import Analysis


pytest_plugins = ["test_requirements_eval"]


def install(client, mode='valid'):
    calls = []
    value = copy.deepcopy(builtin_suite()['cases'][3]['expected'])
    value['intents'] = ['test_code', 'fix_code']

    def handler(request):
        calls.append(request)
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['completion'],
                                           'remote_model': 'cloud' if mode == 'cloud' else ''})
        body = json.loads(request.content)
        assert json.loads(body['messages'][1]['content'])['request'] == '테스트 작성, 실행 금지'
        assert 'tools' not in body
        if mode == 'network':
            raise httpx.ConnectError('SECRET upstream details', request=request)
        if mode == 'invalid':
            value['constraints'] = ['made_up']
        answer = 'not JSON' if mode == 'json' else json.dumps(value)
        return httpx.Response(200, json={'message': {'content': answer}})

    settings = client.app.state.settings
    provider = OllamaProvider(settings, httpx.MockTransport(handler))
    client.app.state.providers.providers['ollama'] = provider
    return provider, calls


def send(client, **kwargs):
    return client.post('/api/request-analysis/preview', json={
        'content': '테스트 작성, 실행 금지', 'approved': True, **kwargs})


def test_preview_only_suggests_roles_without_writes(req_owner):
    client = req_owner
    _, calls = install(client)
    result = send(client)
    assert result.status_code == 200, result.text
    assert result.json()['suggested_roles'] == ['code_tester', 'developer']
    assert result.json()['execution'] == 'none'
    assert len(calls) == 2
    with client.app.state.database_factory() as db:
        assert not list(db.scalars(select(Message)))
        assert not list(db.scalars(select(TrainingJob)))
    assert client.get('/api/status').json()['request_analysis_available'] is True


@pytest.mark.parametrize('mode', ['cloud', 'network', 'invalid', 'json'])
def test_preview_rejects_cloud_failures_and_bad_schema(req_owner, mode):
    _, calls = install(req_owner, mode)
    response = send(req_owner)
    assert response.status_code == 502
    assert 'SECRET' not in response.text
    assert len(calls) == (1 if mode == 'cloud' else 2)


@pytest.mark.parametrize('changes,code', [({'approved': False}, 400),
    ({'approved': 'true'}, 422), ({'content': ' '}, 422),
    ({'content': 'x' * 2001}, 422), ({'execute': True}, 422)])
def test_preview_validates_input_before_call(req_owner, changes, code):
    _, calls = install(req_owner)
    assert send(req_owner, **changes).status_code == code
    assert calls == []


@pytest.mark.parametrize('url', ['https://localhost', 'http://example.com',
    'http://user:secret@localhost', 'http://localhost/api', 'http://localhost?key=secret'])
def test_preview_requires_local_model_endpoint(req_owner, url):
    _, calls = install(req_owner)
    req_owner.app.state.settings.ollama_url = url
    assert send(req_owner).status_code == 403
    assert calls == []


def test_preview_auth_csrf_and_owner_guards(req_owner):
    _, calls = install(req_owner)
    csrf = req_owner.headers.pop('X-CSRF-Token')
    assert send(req_owner).status_code == 403
    req_owner.headers['X-CSRF-Token'] = csrf
    registered = req_owner.post('/api/auth/register', json={
        'email': 'member@example.test', 'password': 'safe-member-password-123'})
    assert registered.status_code in (200, 201), registered.text
    req_owner.headers['X-CSRF-Token'] = registered.json()['csrf_token']
    assert send(req_owner).status_code == 403
    req_owner.cookies.clear()
    assert send(req_owner).status_code == 401
    assert calls == []


def test_preview_timeout_and_lock_release(req_owner, monkeypatch):
    from role_ai import request_analysis
    provider, _ = install(req_owner)
    complete = provider.complete

    async def slow(messages):
        await asyncio.sleep(0.1)
        return await complete(messages)

    monkeypatch.setattr(request_analysis, 'ANALYSIS_TIMEOUT_SECONDS', 0.01)
    monkeypatch.setattr(provider, 'complete', slow)
    assert send(req_owner).status_code == 504
    monkeypatch.setattr(provider, 'complete', complete)
    monkeypatch.setattr(request_analysis, 'ANALYSIS_TIMEOUT_SECONDS', 90)
    assert send(req_owner).status_code == 200


def test_recommendation_deduplicates_and_does_not_dispatch():
    value = copy.deepcopy(builtin_suite()['cases'][0]['expected'])
    value['intents'] = ['write_code', 'fix_code', 'execute_code', 'train_model']
    assert recommend(Analysis.model_validate(value)) == ['developer']
    value['intents'] = ['train_model', 'manage_workers']
    assert recommend(Analysis.model_validate(value)) == ['assistant']


def test_preview_rejects_parallel_requests(req_owner, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    provider, _ = install(req_owner)
    complete = provider.complete
    entered, release = threading.Event(), threading.Event()

    async def blocked(messages):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return await complete(messages)

    monkeypatch.setattr(provider, 'complete', blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(send, req_owner)
        try:
            assert entered.wait(3)
            assert send(req_owner).status_code == 409
        finally:
            release.set()
        assert pending.result(timeout=10).status_code == 200
