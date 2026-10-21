"""Read-only local readiness checks; no Settings loading, credentials or service startup."""
import argparse
import json
import os
import platform
import re
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE = 262144


def command(args, cwd=None):
    """Never return raw stderr (it may contain configuration or credentials)."""
    if not shutil.which(args[0]):
        return 'missing', ''
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5,
                                env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        return ('ok', result.stdout[:MAX_RESPONSE]) if result.returncode == 0 else ('unavailable', '')
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return 'unavailable', ''


def exists_info(path):
    # Follow no file symlinks and read no contents, including databases and .env.
    try:
        value = path.lstat()
        return {'exists': True, 'symlink': stat.S_ISLNK(value.st_mode),
                'kind': 'directory' if stat.S_ISDIR(value.st_mode) else 'file',
                'bytes': value.st_size if stat.S_ISREG(value.st_mode) else None,
                'group_or_other_access': bool(stat.S_IMODE(value.st_mode) & 0o077)}
    except FileNotFoundError:
        return {'exists': False}
    except OSError:
        return {'exists': None, 'state': 'unavailable'}


def memory_info():
    try:
        lines = Path('/proc/meminfo').read_text().splitlines()
        values = {parts[0].rstrip(':'): int(parts[1]) * 1024
                  for line in lines if (parts := line.split()) and len(parts) >= 2}
        return {k: values.get(v) for k, v in [('total_bytes', 'MemTotal'), ('available_bytes', 'MemAvailable')]}
    except (OSError, ValueError):
        return {'state': 'unavailable'}


def docker_status():
    # A configured SSH/TCP daemon can be another machine; do not label it as this host.
    endpoint = os.environ.get('DOCKER_HOST') if not os.environ.get('DOCKER_CONTEXT') else None
    if endpoint is None:
        context_state, endpoint = command(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
        if context_state != 'ok':
            return context_state, 'unavailable', 'unavailable', ''
    if not endpoint.strip().startswith(('unix://', 'npipe://')):
        return 'remote_or_unknown_context', 'unavailable', 'unavailable', ''
    docker, _ = command(['docker', 'info', '--format', '{{.ServerVersion}}'])
    compose, _ = command(['docker', 'compose', 'version', '--short'])
    containers_state, containers = command(['docker', 'ps', '--format', '{{.State}}']) if docker == 'ok' else ('unavailable', '')
    return docker, compose, containers_state, containers


def inspect_local(project, database, storage):
    status, head = command(['git', 'rev-parse', 'HEAD'], project)
    _, branch = command(['git', 'symbolic-ref', '--short', 'HEAD'], project)
    changes, dirty = command(['git', 'status', '--porcelain', '--untracked-files=normal'], project)
    env = exists_info(project / '.env')
    data = {'database': exists_info(database), 'database_wal': exists_info(Path(str(database) + '-wal')),
            'database_shm': exists_info(Path(str(database) + '-shm')), 'artifacts': exists_info(storage)}
    gpu_state, gpu_csv = command(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,memory.used',
                                  '--format=csv,noheader,nounits'])
    gpus = []
    if gpu_state == 'ok':
        for line in gpu_csv.splitlines()[:16]:
            parts = [p.strip() for p in line.split(',')]
            if (len(parts) == 4 and re.fullmatch(r'\d+(?:\.\d+)*', parts[1])
                    and all(re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts[2:])):
                gpus.append({'name': parts[0][:100], 'driver': parts[1],
                             'total_mib': float(parts[2]), 'used_mib': float(parts[3])})
        if not gpus:
            gpu_state = 'unavailable'
    docker, compose, containers_state, containers = docker_status()
    ports_state, ports = command(['ss', '-H', '-ltn'])
    listeners = {str(port): any(re.search(rf':{port}\s', line) for line in ports.splitlines())
                 for port in (8000, 11434)} if ports_state == 'ok' else None
    try:
        disk = shutil.disk_usage(project)
        disk_info = {'total_bytes': disk.total, 'free_bytes': disk.free}
    except OSError:
        disk_info = {'state': 'unavailable'}
    issues = []
    if status != 'ok':
        issues.append('저장소 커밋을 확인할 수 없습니다.')
    if changes != 'ok' or dirty:
        issues.append('Git 변경 여부를 확인하고 기존 변경을 보존하세요.')
    if not env.get('exists'):
        issues.append('.env 존재를 확인하지 못했습니다. 예제 복사는 기존 파일이 없을 때만 하세요.')
    elif env.get('symlink') or env.get('group_or_other_access'):
        issues.append('.env 링크 또는 접근 권한을 검토하세요. 내용은 읽지 않았습니다.')
    if any(item.get('exists') for item in data.values()):
        issues.append('기존 데이터가 있습니다. 서비스 중지와 일관된 백업을 먼저 계획하세요.')
    if gpu_state != 'ok':
        issues.append('실제 GPU·드라이버를 확인하지 못했습니다. GPU 학습을 시작하지 마세요.')
    if docker != 'ok' or compose != 'ok':
        issues.append('Docker/Compose 접근이 확인되지 않았습니다. 직접 실행 경로도 선택할 수 있습니다.')
    try:
        distro = platform.freedesktop_os_release().get('PRETTY_NAME', '')[:120]
    except OSError:
        distro = None
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'scope': 'current_host_only',
            'host': {'system': platform.system(), 'distribution': distro, 'release': platform.release(),
                     'wsl': 'microsoft' in platform.release().lower(), 'machine': platform.machine(),
                     'root': os.geteuid() == 0 if hasattr(os, 'geteuid') else None,
                     'memory': memory_info(), 'disk': disk_info},
            'repository': {'state': status, 'commit': head.strip() if re.fullmatch(r'[0-9a-f]{40}\s*', head) else None,
                           'branch': branch.strip() if re.fullmatch(r'[\w/.-]{1,100}\s*', branch) else None,
                           'dirty': bool(dirty) if changes == 'ok' else None},
            'env_file': env, 'data': data, 'gpu': {'state': gpu_state, 'devices': gpus},
            'docker': {'daemon': docker, 'compose': compose, 'containers_state': containers_state,
                       'running_count': len(containers.splitlines()) if containers_state == 'ok' else None},
            'listeners': listeners, 'next_steps': issues,
            'unverified': ['별도 사용자 서버 접근', 'Docker volume 내부 데이터·모델 경로',
                           'DB 스키마·무결성·백업 복원', 'Ollama 추론·모델 품질',
                           'OpenClaw 대화', '브라우저 E2E', '실제 GPU 학습·평가']}


def local_url(value):
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1', 'host.docker.internal'}
            or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}):
        raise ValueError('HTTP 로컬 주소만 허용합니다.')
    # Force validation of malformed ports before opening any connection.
    _ = url.port
    return value.rstrip('/') + '/'


def read_json(client, method, endpoint, **kwargs):
    with client.stream(method, endpoint, **kwargs) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_RESPONSE:
                raise ValueError('Response too large')
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError('Expected object')
    return value


def check_ollama(url, model=None, generate=False, transport=None):
    # Validation intentionally precedes exception sanitization: bad CLI input should fail fast.
    url = local_url(url)
    if generate and not model:
        raise ValueError('추론 검사에는 --model이 필요합니다.')
    result = {'connection': 'unavailable', 'model_installed': None, 'local_completion': None,
              'inference': 'blocked' if generate else 'not_requested', 'quality_verified': False}
    try:
        with httpx.Client(base_url=url, timeout=httpx.Timeout(60, connect=3), follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            version = read_json(client, 'GET', 'api/version')
            if not isinstance(version.get('version'), str):
                raise ValueError('Invalid version')
            result['connection'] = 'ok'
            if not model:
                return result
            tags = read_json(client, 'GET', 'api/tags')
            models = tags.get('models')
            if not isinstance(models, list):
                raise ValueError('Invalid model list')
            result['model_installed'] = any(isinstance(m, dict) and model in {m.get('name'), m.get('model')}
                                            for m in models)
            if not result['model_installed']:
                return result
            info = read_json(client, 'POST', 'api/show', json={'model': model})
            result['local_completion'] = bool(not info.get('remote_host') and not info.get('remote_model')
                                               and 'completion' in info.get('capabilities', []))
            if generate and result['local_completion']:
                reply = read_json(client, 'POST', 'api/chat', json={
                    'model': model, 'stream': False,
                    'messages': [{'role': 'user', 'content': '연결 검사입니다. OK만 답하세요.'}],
                    'options': {'num_predict': 8, 'num_ctx': 2048}})
                text = reply.get('message', {}).get('content')
                result['inference'] = 'response_received' if (isinstance(text, str) and text.strip()
                    and reply.get('model') == model and reply.get('done') is True) else 'invalid_response'
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        result['error'] = 'connection_or_response_failed'
        if generate:
            result['inference'] = 'failed'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Role-AI 로컬 준비 상태 진단 (설치·기동·수정 없음)')
    parser.add_argument('--project-dir', type=Path, default=Path.cwd())
    parser.add_argument('--database', type=Path, default=Path('data/role-ai.db'))
    parser.add_argument('--storage', type=Path, default=Path('data/artifacts'))
    parser.add_argument('--ollama-check', action='store_true', help='로컬 Ollama 버전·모델 메타데이터 검사')
    parser.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    parser.add_argument('--model', help='정확한 설치 모델명. 보고서에는 표시하지 않습니다.')
    parser.add_argument('--ollama-generate', action='store_true', help='짧은 추론 1회 명시적 실행 (GPU 사용 가능)')
    args = parser.parse_args(argv)
    if args.ollama_generate and (not args.ollama_check or not args.model):
        parser.error('--ollama-generate에는 --ollama-check와 --model이 필요합니다.')
    try:
        if args.ollama_check:
            local_url(args.ollama_url)
    except ValueError:
        parser.error('Ollama 주소는 자격 증명 없는 HTTP 로컬 주소여야 합니다.')
    project = args.project_dir.absolute()
    report = inspect_local(project, project / args.database, project / args.storage)
    if args.ollama_check:
        report['ollama'] = check_ollama(args.ollama_url, args.model, args.ollama_generate)
        if report['ollama']['inference'] == 'response_received':
            report['unverified'].remove('Ollama 추론·모델 품질')
            report['unverified'].append('Ollama 모델 품질·요구사항 분석 정확도')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
