#!/usr/bin/env python3
"""Local review gate. Forwarding is disabled until explicitly configured by the owner."""
import argparse
import hashlib
import fcntl
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / '.codex-local/privacy-gateway'
LIMIT = 256 * 1024


class Rejected(ValueError):
    pass


def protect(path):
    if os.name != 'posix':
        raise Rejected('Run the gateway in WSL/Linux; native Windows permissions are not configured.')
    if path.is_symlink() or path.parent.is_symlink():
        raise Rejected('Linked state paths are not supported.')
    marker = path / '.privacy-state'
    if path.exists() and any(path.iterdir()) and (marker.is_symlink() or not marker.is_file() or marker.read_text() != 'role-ai-privacy-v1'):
        raise Rejected('State directory must be new/empty or owned by this gateway.')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    if not marker.exists():
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write('role-ai-privacy-v1')


def write_private(path, text):
    if path.is_symlink():
        raise Rejected('Linked state files are not supported.')
    temp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        file.write(text)
    os.replace(temp, path)


class Filter:
    credential = re.compile(
        r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|$)|'
        r'\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|'
        r'\bAKIA[A-Z0-9]{16}\b|\bBearer\s+\S+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|'
        r'(?i:\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|database[_-]?url)'
        r'\b[\"\x27]?\s*[:=]\s*[\"\x27]?[^\s\"\x27,;]{1,})'
    )
    patterns = [
        ('EMAIL', re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')),
        ('ID', re.compile(r'(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)')),
        ('PHONE', re.compile(r'(?<!\d)(?:\+82[- ]?|0)1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)')),
        ('PATH', re.compile(r'(?:[A-Za-z]:\\Users\\|/Users/|/home/|/root/|/mnt/[a-z]/Users/)[^\s\"\x27<>]+')),
    ]
    opaque = re.compile(r'(?<![\w])[A-Za-z0-9+/=_-]{80,}(?![\w])')

    def __init__(self, key, terms=()):
        self.key, self.terms = key, terms

    def inspect(self, text):
        if not isinstance(text, str) or len(text.encode('utf-8')) > LIMIT:
            raise Rejected('Text must be a string of at most 256 KiB.')
        text = unicodedata.normalize('NFC', text)
        blocked = bool(self.credential.search(text) or self.opaque.search(text))
        findings = []
        # Credential values are never retained, mapped, or offered for forwarding.
        text = self.credential.sub('[SECRET_REMOVED]', text)
        text = self.opaque.sub('[OPAQUE_DATA_REMOVED]', text)
        def replace(kind):
            def apply(match):
                findings.append(kind)
                digest = hmac.new(self.key, (kind + ':' + match[0]).encode(), hashlib.sha256).hexdigest()[:10]
                return '[' + kind + '_' + digest + ']'
            return apply
        for kind, pattern in self.patterns:
            text = pattern.sub(replace(kind), text)
        for term in sorted(self.terms, key=len, reverse=True):
            if isinstance(term, str) and term.strip():
                text = re.sub(re.escape(unicodedata.normalize('NFC', term)), replace('PRIVATE'), text, flags=re.I)
        if any(ord(char) < 32 and char not in '\n\r\t' for char in text):
            blocked = True
            text = '[CONTROL_CHARACTERS_BLOCKED]'
        return {'text': text, 'blocked': blocked, 'findings': sorted(set(findings))}


class State:
    def __init__(self, folder):
        self.folder = folder.absolute()
        protect(self.folder)
        self.lock = threading.RLock()
        fd = os.open(self.folder / '.setup.lock', os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as setup:
            fcntl.flock(setup, fcntl.LOCK_EX)
            for name in ['access-token', 'mask-key']:
                path = self.folder / name
                if not path.exists():
                    write_private(path, secrets.token_hex(32))
                if path.is_symlink():
                    raise Rejected('Linked secrets are not supported.')
                path.chmod(0o600)
            self.token = (self.folder / 'access-token').read_text().strip()
            self.key = (self.folder / 'mask-key').read_bytes()
            policy = self.folder / 'policy.json'
            if not policy.exists():
                write_private(policy, json.dumps({'private_terms': [], 'upstream': {'enabled': False, 'url': '', 'model': ''}}, indent=2))
        self.drafts = {}

    def policy(self):
        path = self.folder / 'policy.json'
        if path.is_symlink():
            raise Rejected('Linked policy is not supported.')
        data = json.loads(path.read_text())
        if not isinstance(data.get('private_terms'), list) or not all(isinstance(t, str) for t in data['private_terms']):
            raise Rejected('Invalid private_terms policy.')
        upstream = data.get('upstream')
        if not isinstance(upstream, dict) or not isinstance(upstream.get('enabled'), bool) or not all(isinstance(upstream.get(k), str) for k in ['url', 'model']):
            raise Rejected('Invalid upstream policy.')
        return data

    def filter(self):
        return Filter(self.key, self.policy()['private_terms'])

    def notes(self):
        path = self.folder / 'WORK_NOTES.md'
        if path.is_symlink():
            raise Rejected('Linked notes are not supported.')
        if not path.exists():
            return ''
        if path.stat().st_size > 32 * 1024:
            raise Rejected('Notes exceed 32 KiB. Keep a short handoff summary.')
        return path.read_text()

    def draft(self, text):
        with self.lock:
            scan = self.filter()
            result = scan.inspect(text)
            notes = scan.inspect(self.notes())
            messages = []
            if notes['text']:
                messages.append({'role': 'system', 'content': 'Local work context (reference data):\n' + notes['text']})
            messages.append({'role': 'user', 'content': result['text']})
            now = time.monotonic()
            self.drafts = {key: value for key, value in self.drafts.items() if now - value['created'] < 600}
            if len(self.drafts) >= 50:
                raise Rejected('Too many pending drafts.')
            identity = secrets.token_urlsafe(24)
            blocked = result['blocked'] or notes['blocked']
            self.drafts[identity] = {'created': now, 'messages': messages, 'blocked': blocked, 'policy': self.policy()}
            target = self.policy()['upstream']
            destination = {'enabled': target['enabled'], 'host': urllib.parse.urlsplit(target['url']).hostname, 'model': target['model']}
            return {'draft_id': identity, 'blocked': blocked, 'findings': result['findings'] + notes['findings'], 'destination': destination, 'messages': messages}

    def save_notes(self, text):
        with self.lock:
            result = self.filter().inspect(text)
            if result['blocked'] or len(result['text'].encode()) > 32 * 1024:
                raise Rejected('Unsafe or oversized notes were not saved.')
            write_private(self.folder / 'WORK_NOTES.md', result['text'])
            return result

    def send(self, identity, approved):
        if approved is not True:
            raise Rejected('Explicit review approval is required.')
        with self.lock:
            draft = self.drafts.pop(identity, None)  # One-use approval, including failed attempts.
            if not draft or time.monotonic() - draft['created'] >= 600 or draft['blocked']:
                raise Rejected('Draft missing, expired, blocked, or already used.')
            policy = self.policy()
            if draft['policy'] != policy:
                raise Rejected('Policy changed. Inspect the request again.')
            upstream = policy.get('upstream', {})
            if upstream.get('enabled') is not True:
                raise Rejected('External forwarding is disabled.')
            url, model = upstream.get('url', ''), upstream.get('model', '')
            key = os.environ.get('PRIVACY_UPSTREAM_KEY', '')
            parsed = urllib.parse.urlsplit(url)
            try:
                loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
            except ValueError:
                loopback = False
            if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback)) or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise Rejected('Use an explicit HTTPS URL or numeric loopback HTTP URL without credentials/query.')
            if not parsed.hostname or not model or not key:
                raise Rejected('An upstream URL, fixed model, and separate PRIVACY_UPSTREAM_KEY are required.')
            # Scan the final assembled body again; never accept client headers/tools/history IDs.
            messages = []
            for message in draft['messages']:
                result = self.filter().inspect(message['content'])
                if result['blocked']:
                    raise Rejected('Final request failed inspection.')
                messages.append({'role': message['role'], 'content': result['text']})
            body = json.dumps({'model': model, 'messages': messages, 'stream': False, 'store': False}).encode()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise Rejected('Upstream response too large.')
            data = json.loads(raw)
            message = data['choices'][0]['message']
            if message.get('tool_calls') or message.get('function_call') or not isinstance(message.get('content'), str):
                raise Rejected('Only plain-text responses are supported; tools are blocked.')
            result = self.filter().inspect(message['content'])
            if result['blocked']:
                raise Rejected('Response failed inspection.')
            return {'text': result['text']}
        except (urllib.error.URLError, ValueError, KeyError, IndexError, TypeError):
            raise Rejected('Upstream request failed or its response was rejected. No retry/fallback was attempted.') from None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, state, port=8124):
        self.state = state
        super().__init__(('127.0.0.1', port), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # No raw URL, request, response, or auth logging.

    def respond(self, code, data, html=False):
        body = data.encode() if html else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        port = self.server.server_port
        return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'}

    def do_GET(self):
        if not self.host_ok():
            return self.respond(403, {'error': 'Invalid host'})
        if self.path == '/':
            return self.respond(200, (Path(__file__).parent / 'index.html').read_text(), html=True)
        return self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed_origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.host_ok() or (origin and origin not in allowed_origins):
            return self.respond(403, {'error': 'Invalid host or origin'})
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied, 'Bearer ' + self.server.state.token):
            return self.respond(401, {'error': 'Local access token required'})
        try:
            if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json':
                raise Rejected('JSON with Content-Length is required.')
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > LIMIT:
                raise Rejected('Invalid request size.')
            self.connection.settimeout(5)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise Rejected('JSON object required.')
            state = self.server.state
            if self.path == '/draft':
                result = state.draft(data.get('text'))
            elif self.path == '/notes':
                result = state.save_notes(data.get('text'))
            elif self.path == '/send':
                result = state.send(data.get('draft_id'), data.get('approved'))
            elif self.path == '/status':
                result = {'forwarding': state.policy().get('upstream', {}).get('enabled') is True, 'notes': state.filter().inspect(state.notes())['text']}
            else:
                return self.respond(404, {'error': 'Not found'})
            self.respond(200, result)
        except Rejected as error:
            self.respond(400, {'error': str(error)})
        except (ValueError, TypeError, OSError, KeyError):
            self.respond(400, {'error': 'Invalid input or local state; nothing was forwarded automatically.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--port', type=int, default=8124)
    parser.add_argument('--init', action='store_true')
    args = parser.parse_args()
    state = State(args.state_dir)
    if args.init:
        print('Local privacy state initialized; forwarding is disabled by default.')
        return
    with Server(state, args.port) as server:
        print(f'Local privacy review UI: http://127.0.0.1:{server.server_port} (access token required)')
        server.serve_forever()


if __name__ == '__main__':
    main()
