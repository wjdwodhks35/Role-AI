import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer

FILE = Path(__file__).resolve().parents[1] / 'gateway.py'
spec = importlib.util.spec_from_file_location('privacy_gateway', FILE)
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)


class FilterTests(unittest.TestCase):
    def test_pii_and_private_terms_masked_stably(self):
        f = g.Filter(b'local-key', ['비밀회사', '홍길동'])
        raw = '홍길동 비밀회사 user@example.com 010-1234-5678 900101-1234567 /home/alice/private.txt'
        result = f.inspect(raw)
        self.assertFalse(result['blocked'])
        for secret in ['홍길동', '비밀회사', 'user@example.com', '010-1234-5678', '900101-1234567', '/home/alice']:
            self.assertNotIn(secret, result['text'])
        self.assertEqual(result, f.inspect(raw))
        self.assertNotEqual(result['text'], g.Filter(b'other-key').inspect(raw)['text'])

    def test_credentials_and_opaque_payloads_blocked(self):
        for text in ['api_key="sk-testabcdefghijklmnop"', 'password=x', 'TOKEN=abc',
                     'Bearer abc.def', 'A' * 90, '-----BEGIN PRIVATE KEY-----\nPRIVATEBODY\n-----END PRIVATE KEY-----']:
            with self.subTest(text=text[:15]):
                self.assertTrue(g.Filter(b'key').inspect(text)['blocked'])
        self.assertNotIn('PRIVATEBODY', g.Filter(b'key').inspect('-----BEGIN PRIVATE KEY-----\nPRIVATEBODY')['text'])

    def test_unknown_text_preserved_and_oversized_rejected(self):
        self.assertEqual(g.Filter(b'key').inspect('함수 add를 수정해줘')['text'], '함수 add를 수정해줘')
        with self.assertRaises(g.Rejected): g.Filter(b'key').inspect('x' * (g.LIMIT + 1))
        with self.assertRaises(g.Rejected): g.Filter(b'key').inspect({'image': 'not supported'})


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state = g.State(Path(self.temp.name) / 'state')

    def test_no_network_by_default_and_explicit_approval_required(self):
        draft = self.state.draft('공개 코드 질문')
        with patch.object(g.urllib.request, 'build_opener') as network:
            with self.assertRaises(g.Rejected): self.state.send(draft['draft_id'], False)
            with self.assertRaises(g.Rejected): self.state.send(draft['draft_id'], True)
            network.assert_not_called()

    def test_secret_draft_blocked_and_notes_not_written(self):
        draft = self.state.draft('password=abcd')
        self.assertTrue(draft['blocked'])
        with self.assertRaises(g.Rejected): self.state.send(draft['draft_id'], True)
        with self.assertRaises(g.Rejected): self.state.save_notes('token=abc')
        self.assertFalse((self.state.folder / 'WORK_NOTES.md').exists())

    def test_restart_notes_and_final_context_inspected(self):
        result = self.state.save_notes('작업 담당 user@example.com')
        restarted = g.State(self.state.folder)
        self.assertEqual(self.state.token, restarted.token)
        self.assertEqual(restarted.notes(), result['text'])
        self.assertNotIn('user@example.com', json.dumps(restarted.draft('다음 작업')))
        g.write_private(self.state.folder / 'WORK_NOTES.md', 'password=bad')
        self.assertTrue(restarted.draft('정상 질문')['blocked'])
        self.assertEqual((self.state.folder / 'access-token').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.folder.stat().st_mode & 0o777, 0o700)

    def test_policy_change_invalidates_approval(self):
        draft = self.state.draft('hello')
        policy = self.state.policy(); policy['private_terms'] = ['hello']
        g.write_private(self.state.folder / 'policy.json', json.dumps(policy))
        with self.assertRaisesRegex(g.Rejected, 'Policy changed'): self.state.send(draft['draft_id'], True)

    def test_unowned_directory_and_symlinks_rejected(self):
        path = Path(self.temp.name) / 'unowned'; path.mkdir(); (path / 'file').write_text('keep')
        with self.assertRaises(g.Rejected): g.State(path)
        link = Path(self.temp.name) / 'link'; link.symlink_to(self.state.folder)
        with self.assertRaises(g.Rejected): g.State(link)

    def test_mock_upstream_receives_only_reviewed_masked_request(self):
        received = []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                received.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                data = json.dumps({'choices': [{'message': {'content': '완료'}}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(data)
        server = HTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            policy = self.state.policy(); policy['upstream'] = {'enabled': True, 'url': f'http://127.0.0.1:{server.server_port}/v1/chat/completions', 'model': 'fixture'}
            g.write_private(self.state.folder / 'policy.json', json.dumps(policy))
            draft = self.state.draft('연락 user@example.com')
            with patch.dict(os.environ, {'PRIVACY_UPSTREAM_KEY': 'local-fixture-key'}):
                self.assertEqual(self.state.send(draft['draft_id'], True)['text'], '완료')
            headers, body = received[0]
            self.assertNotIn('user@example.com', json.dumps(body))
            self.assertEqual(headers['Authorization'], 'Bearer local-fixture-key')
            self.assertFalse(body['store']); self.assertNotIn('tools', body)
            with self.assertRaises(g.Rejected): self.state.send(draft['draft_id'], True)
            self.assertEqual(len(received), 1)
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_redirects_and_tool_calls_are_rejected(self):
        paths = []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length'])); paths.append(self.path)
                if self.path == '/redirect':
                    self.send_response(302); self.send_header('Location', '/tools'); self.end_headers()
                else:
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({'choices': [{'message': {'content': '', 'tool_calls': [{'id': 'fake'}]}}]}).encode())
        server = HTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.dict(os.environ, {'PRIVACY_UPSTREAM_KEY': 'local-fixture-key'}):
                for endpoint in ['/redirect', '/tools']:
                    policy = self.state.policy(); policy['upstream'] = {'enabled': True, 'url': f'http://127.0.0.1:{server.server_port}' + endpoint, 'model': 'fixture'}
                    g.write_private(self.state.folder / 'policy.json', json.dumps(policy))
                    draft = self.state.draft('public question')
                    with self.assertRaises(g.Rejected): self.state.send(draft['draft_id'], True)
            self.assertEqual(paths, ['/redirect', '/tools'])
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_auth_origin_host_and_body_limits(self):
        server = g.Server(self.state, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path, headers, content=None):
            return opener.open(urllib.request.Request(url + path, data=content, headers=headers), timeout=3)
        good = {'Authorization': 'Bearer ' + self.state.token, 'Content-Type': 'application/json'}
        try:
            for headers, expected in [({'Content-Type': 'application/json'}, 401),
                                      ({**good, 'Origin': 'https://attacker.example'}, 403),
                                      ({**good, 'Host': 'attacker.example'}, 403)]:
                with self.assertRaises(urllib.error.HTTPError) as error: request('/draft', headers, b'{"text":"hi"}')
                self.assertEqual(error.exception.code, expected)
            with request('/draft', good, b'{"text":"safe"}') as response:
                self.assertIn('draft_id', json.load(response))
            with request('/', {}) as response:
                self.assertNotIn(self.state.token, response.read().decode())
            with self.assertRaises(urllib.error.HTTPError): request('/v1/chat/completions', good, b'{}')
        finally: server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
