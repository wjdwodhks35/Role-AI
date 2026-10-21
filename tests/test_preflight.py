import json

import httpx
import pytest

from role_ai import preflight


def test_inventory_never_reads_settings_or_database(tmp_path, monkeypatch, capsys):
    (tmp_path / '.env').write_text('ROLE_AI_OPENAI_API_KEY=PRIVATE_SENTINEL\n')
    data = tmp_path / 'data'
    data.mkdir()
    database = data / 'role-ai.db'
    database.write_bytes(b'PRIVATE_DATABASE_SENTINEL')
    (data / 'role-ai.db-wal').write_bytes(b'PRIVATE_WAL_SENTINEL')
    artifacts = data / 'artifacts'
    artifacts.mkdir()
    (artifacts / 'private-model.safetensors').write_bytes(b'MODEL_SENTINEL')
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob('*') if p.is_file()}
    commands = []

    def command(args, cwd=None):
        commands.append(args)
        return 'missing', ''

    monkeypatch.setattr(preflight, 'command', command)
    preflight.main(['--project-dir', str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert report['data']['database']['exists'] is True
    assert report['data']['database_wal']['exists'] is True
    assert report['env_file']['exists'] is True
    assert report['scope'] == 'current_host_only'
    assert report['repository']['dirty'] is None
    assert 'PRIVATE' not in json.dumps(report) and 'SENTINEL' not in json.dumps(report)
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob('*') if p.is_file()}
    assert not any('pull' in c or 'up' in c or 'install' in c for c in commands)


def test_inventory_missing_paths_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, 'command', lambda *a: ('missing', ''))
    root = tmp_path / 'absent'
    report = preflight.inspect_local(root, root / 'db', root / 'artifacts')
    assert report['data']['database']['exists'] is False
    assert report['docker']['running_count'] is None
    assert not root.exists()


def test_inventory_parses_whitelisted_fields_only(tmp_path, monkeypatch):
    def command(args, cwd=None):
        if args[0] == 'git':
            return 'ok', 'a' * 40 if 'rev-parse' in args else ('feature/test' if 'symbolic-ref' in args else '?? PRIVATE_FILE')
        if args[0] == 'nvidia-smi':
            return 'ok', 'RTX 4060 Ti, 555.42, 8188, 100\n'
        if args[0] == 'ss':
            return 'ok', 'LISTEN 0 100 127.0.0.1:11434 0.0.0.0:*\n'
        if args[:3] == ['docker', 'context', 'inspect']:
            return 'ok', 'unix:///var/run/docker.sock'
        if args[:2] == ['docker', 'ps']:
            return 'ok', 'running\nrunning\n'
        return 'ok', 'PRIVATE_CONFIGURATION'

    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setattr(preflight, 'command', command)
    result = preflight.inspect_local(tmp_path, tmp_path / 'db', tmp_path / 'artifacts')
    assert result['repository']['dirty'] is True
    assert result['gpu']['devices'][0]['total_mib'] == 8188
    assert result['docker']['running_count'] == 2
    assert result['listeners'] == {'8000': False, '11434': True}
    assert 'PRIVATE' not in json.dumps(result)


def test_symlink_metadata_does_not_follow_target(tmp_path):
    target = tmp_path / 'secret'
    target.write_text('PRIVATE')
    link = tmp_path / '.env'
    link.symlink_to(target)
    assert preflight.exists_info(link)['symlink'] is True
    assert 'PRIVATE' not in json.dumps(preflight.exists_info(link))


@pytest.mark.parametrize('url', ['https://localhost', 'http://example.com', 'http://localhost/private',
    'http://key:secret@localhost', 'http://localhost?token=secret', 'http://localhost:bad', 'http://localhost#secret'])
def test_remote_or_credential_urls_are_rejected(url):
    with pytest.raises(ValueError):
        preflight.check_ollama(url)


def ollama_transport(mode, calls):
    def handler(request):
        calls.append(request)
        if mode == 'network':
            raise httpx.ConnectError('PRIVATE_UPSTREAM', request=request)
        if mode == 'redirect':
            return httpx.Response(302, headers={'Location': 'http://external.invalid'})
        if mode == 'huge':
            return httpx.Response(200, content=b'x' * (preflight.MAX_RESPONSE + 1))
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.1.0'})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'local:small'}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['completion'],
                'remote_host': 'private.cloud' if mode == 'cloud' else ''})
        assert request.url.path == '/api/chat'
        body = json.loads(request.content)
        assert 'keep_alive' not in body and body['options']['num_predict'] == 8
        assert 'tools' not in body
        return httpx.Response(200, json={'model': 'local:small', 'done': True,
                                        'message': {'content': 'OK'}})
    return httpx.MockTransport(handler)


@pytest.mark.parametrize('model', [None, 'local:small', 'missing:small'])
def test_metadata_checks_never_generate(model):
    calls = []
    result = preflight.check_ollama('http://localhost:11434', model=model,
                                   transport=ollama_transport('valid', calls))
    assert result['connection'] == 'ok' and result['quality_verified'] is False
    assert not any(r.url.path == '/api/chat' for r in calls)
    if model:
        assert result['model_installed'] == (model == 'local:small')


@pytest.mark.parametrize('mode', ['valid', 'cloud', 'network', 'redirect', 'huge'])
def test_inference_is_explicit_bounded_local_and_sanitized(mode):
    calls = []
    result = preflight.check_ollama('http://localhost:11434', model='local:small', generate=True,
                                   transport=ollama_transport(mode, calls))
    assert result['quality_verified'] is False
    assert (result['inference'] == 'response_received') == (mode == 'valid')
    assert 'PRIVATE' not in json.dumps(result)
    assert all(r.url.host == 'localhost' for r in calls)
    assert sum(r.url.path == '/api/chat' for r in calls) == (1 if mode == 'valid' else 0)


@pytest.mark.parametrize('args', [['--ollama-generate'], ['--ollama-generate', '--ollama-check'],
    ['--ollama-check', '--ollama-url', 'http://secret:key@localhost']])
def test_bad_cli_options_fail_without_inventory(args, monkeypatch, capsys):
    def forbidden(*a):
        pytest.fail('invalid arguments must fail before inspection')
    monkeypatch.setattr(preflight, 'inspect_local', forbidden)
    with pytest.raises(SystemExit) as exc:
        preflight.main(args)
    assert exc.value.code == 2
    # argparse should not echo the entered credential URL.
    assert 'secret:key' not in capsys.readouterr().err


def test_command_errors_are_sanitized(monkeypatch):
    import subprocess
    monkeypatch.setattr(preflight.shutil, 'which', lambda _: '/usr/bin/tool')
    monkeypatch.setattr(preflight.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', 'PRIVATE'))
    assert preflight.command(['tool']) == ('unavailable', '')
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(a, 1, stderr='PRIVATE')
    monkeypatch.setattr(preflight.subprocess, 'run', timeout)
    assert preflight.command(['tool']) == ('unavailable', '')


@pytest.mark.parametrize('endpoint', ['tcp://remote:2376', 'ssh://remote', ''])
def test_remote_docker_context_is_not_reported_as_local(endpoint, monkeypatch):
    calls = []
    def command(args, cwd=None):
        calls.append(args)
        return 'ok', endpoint
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setattr(preflight, 'command', command)
    assert preflight.docker_status()[0] == 'remote_or_unknown_context'
    assert len(calls) == 1 and calls[0][1:3] == ['context', 'inspect']


def test_docker_host_override_is_not_contacted(monkeypatch):
    monkeypatch.setenv('DOCKER_HOST', 'tcp://remote:2376')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    def forbidden(*args):
        pytest.fail('remote daemon must not be contacted')
    monkeypatch.setattr(preflight, 'command', forbidden)
    assert preflight.docker_status()[0] == 'remote_or_unknown_context'
