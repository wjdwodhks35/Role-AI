import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import contextlib
import io

SCRIPT = Path(__file__).resolve().parents[1] / 'run_logged.py'
spec = importlib.util.spec_from_file_location('run_logged', SCRIPT)
logged = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logged)


class SummaryTests(unittest.TestCase):
    def test_short_unknown_json_diff_source_are_kept(self):
        for text in ['짧은 로그\n', 'unknown output\n' * 1000,
                     json.dumps({'text': 'ok' * 4000}),
                     '{"ok":true}\n' * 1000,
                     'diff --git a/a b/a\n' + '+ ok\n' * 2000,
                     'def example():\n' + '    pass\n' * 1000]:
            with self.subTest(prefix=text[:20]):
                self.assertEqual(logged.summarize(text), text)

    def test_known_success_noise_shrinks_but_all_errors_stay(self):
        text = ('test_case (test_suite.Unit) ... ok\n' * 1000
                + 'WARNING: check settings\nAssertionError: 값이 다름\n'
                + 'tests/test_example.py:42: error\n')
        result = logged.summarize(text)
        self.assertLess(len(result), len(text) // 10)
        self.assertIn('AssertionError: 값이 다름', result)
        self.assertIn('tests/test_example.py:42: error', result)
        self.assertIn('WARNING: check settings', result)

    def test_traceback_is_never_collapsed(self):
        trace = ('Traceback (most recent call last):\n'
                 + '  File "example.py", line 1, in run\n' * 200
                 + 'AssertionError: nope\n')
        self.assertIn(trace, logged.summarize('test_x (unit.Case) ... ok\n' * 1000 + trace))

    def test_ansi_removed_and_redaction(self):
        text = '\x1b[31mERROR\x1b[0m token=abc123 Bearer abc.def\napi_key="sk-12345678901234567890" user@example.com'
        result = logged.summarize(text)
        for secret in ['abc123', 'abc.def', 'sk-12345678901234567890', 'user@example.com', '\x1b']:
            self.assertNotIn(secret, result)
        self.assertIn('ERROR', result)


    def test_display_bound_keeps_middle_failure_context(self):
        text = ('unknown line\n' * 2000 + 'Traceback (most recent call last):\n'
                + '  File "example.py", line 42, in run\nValueError: broken\n'
                + 'unknown tail\n' * 2000)
        result = logged.bounded_preview(text, 2000)
        self.assertLessEqual(len(result), 2000)
        for fact in ['Traceback', 'example.py', 'line 42', 'ValueError: broken']:
            self.assertIn(fact, result)

    def test_single_success_is_not_expanded(self):
        text = 'unknown\n' * 1000 + '.\n'
        self.assertEqual(logged.summarize(text), text)

    def test_quoted_secret_and_private_key_redaction(self):
        text = 'password="synthetic secret phrase"\n-----BEGIN PRIVATE KEY-----\nsynthetic-key\n-----END PRIVATE KEY-----'
        result = logged.redact(text)
        for value in ['synthetic secret phrase', 'synthetic-key']:
            self.assertNotIn(value, result)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'logs'

    def execute(self, source, code=0, cap=None):
        args = [sys.executable, '-X', 'utf8', str(SCRIPT), '--log-root', str(self.root)]
        if cap:
            args += ['--max-log-bytes', str(cap)]
        child = [sys.executable, '-X', 'utf8', '-c', source]
        direct = subprocess.run(child, capture_output=True, timeout=15)
        result = subprocess.run(args + ['--'] + child, capture_output=True, timeout=30)
        self.assertEqual(direct.returncode, code)
        self.assertEqual(result.returncode, direct.returncode, result.stderr.decode(errors='replace'))
        folder = max(self.root.glob('run-*'), key=lambda p: p.stat().st_mtime_ns)
        meta = json.loads((folder / 'result.json').read_text())
        self.assertEqual(meta['exit_code'], code)
        if not meta['truncated']:
            self.assertEqual((folder / 'stdout.log').read_bytes(), direct.stdout)
            self.assertEqual((folder / 'stderr.log').read_bytes(), direct.stderr)
        return result, folder, meta

    def test_success_stdout_stderr_unicode(self):
        result, _, _ = self.execute('import sys; print("한글 출력"); print("경고", file=sys.stderr)')
        self.assertIn("한글 출력", result.stdout.decode("utf-8"))
        self.assertEqual(result.stdout.decode("utf-8"), "한글 출력" + os.linesep)
        self.assertEqual(result.stderr.decode("utf-8"), "경고" + os.linesep)

    def test_failure_exit_and_diagnostics(self):
        result, _, _ = self.execute('import sys; print("AssertionError: failure", file=sys.stderr); sys.exit(37)', 37)
        self.assertIn(b'AssertionError: failure', result.stderr)

    def test_large_stdout_and_stderr_no_deadlock_and_caps(self):
        result, folder, meta = self.execute(
            'import sys; print("x"*100000); print("y"*100000, file=sys.stderr); print("AssertionError: tail", file=sys.stderr)', cap=2048)
        self.assertFalse(meta['truncated'])
        self.assertTrue(meta['preview_truncated'])
        self.assertGreater((folder / 'stdout.log').stat().st_size, 100000)
        self.assertGreater((folder / 'stderr.log').stat().st_size, 100000)
        self.assertIn(b'AssertionError: tail', result.stderr)
        self.assertIn(b'PREVIEW CAP', result.stdout)

    def test_raw_secret_kept_privately_public_output_redacted(self):
        result, folder, _ = self.execute('print("token=do_not_publish")')
        self.assertNotIn(b'do_not_publish', result.stdout)
        self.assertIn(b'do_not_publish', (folder / 'stdout.log').read_bytes())
        if os.name != 'nt':
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((folder / 'stdout.log').stat().st_mode & 0o777, 0o600)

    def test_compression_exception_preserves_original_and_exit(self):
        with patch.object(logged, 'summarize', side_effect=RuntimeError('synthetic')):
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                code = logged.run([sys.executable, '-c', 'print("ValueError: original");raise SystemExit(17)'], self.root, 4096)
        self.assertEqual(code, 17)
        self.assertIn('ValueError: original', out.getvalue())
        self.assertIn('compression failed', err.getvalue())
        folder = next(self.root.glob('run-*'))
        self.assertIn(b'ValueError: original', (folder / 'stdout.log').read_bytes())
        self.assertTrue(json.loads((folder / 'result.json').read_text())['summary_failed'])

    def test_preview_failure_does_not_publish_raw_secrets(self):
        with patch.object(logged, 'bounded_preview', side_effect=RuntimeError('synthetic')):
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                code = logged.run([sys.executable, '-c', 'print("token=synthetic-private")'], self.root, 4096)
        self.assertEqual(code, 0)
        self.assertNotIn('synthetic-private', out.getvalue())
        self.assertIn('public output withheld', out.getvalue())

    def test_storage_failure_drains_and_preserves_exit(self):
        real_open = logged.os.open
        def fail_log(path, *args, **kwargs):
            if str(path).endswith('stdout.log'):
                raise OSError('synthetic full disk')
            return real_open(path, *args, **kwargs)
        with patch.object(logged.os, 'open', side_effect=fail_log):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = logged.run([sys.executable, '-c', 'print("x"*100000);raise SystemExit(19)'], self.root, 4096)
        self.assertEqual(code, 19)
        self.assertIn('incomplete logs', err.getvalue())

    def test_concurrent_run_paths_and_results(self):
        args = [sys.executable, str(SCRIPT), '--log-root', str(self.root), '--',
                sys.executable, '-c', 'print("concurrent")']
        children = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(2)]
        for child in children:
            child.communicate(timeout=30)
        for child in children:
            self.assertEqual(child.returncode, 0)
        self.assertEqual(len(list(self.root.glob('run-*'))), 2)

    def test_quota_does_not_delete_active_logs(self):
        logged.secure_directory(self.root)
        active = self.root / 'run-active'
        active.mkdir()
        (active / 'stdout.log').write_bytes(b'x' * 2000)
        with self.assertRaises(OSError):
            logged.prune(self.root, budget=1024)
        self.assertTrue(active.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL check')
    def test_windows_log_acl_is_private(self):
        self.execute('print("private logs")')
        acl = subprocess.run(['icacls', str(self.root)], capture_output=True, check=True)
        self.assertNotIn(b'(I)', acl.stdout)
        self.assertEqual(acl.stdout.count(b'(F)'), 1, acl.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows module-path isolation')
    def test_windows_acl_does_not_depend_on_powershell_modules(self):
        env = dict(os.environ, PSModulePath=str(Path(self.tmp.name) / 'empty-modules'))
        result = subprocess.run([sys.executable, str(SCRIPT), '--log-root', str(self.root), '--',
                                 sys.executable, '-c', 'print("isolated modules")'], env=env,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'isolated modules', result.stdout)

    def test_unique_run_paths(self):
        _, first, _ = self.execute('print("one")')
        _, second, _ = self.execute('print("two")')
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists())

    def test_missing_command_is_127(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--log-root', str(self.root),
                                 '--', 'roleai-no-such-command-7281'], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 127)

    @unittest.skipIf(os.name == 'nt', 'POSIX signal status')
    def test_signal_exit_is_shell_equivalent(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--log-root', str(self.root), '--',
                                 sys.executable, '-c', 'import os,signal;os.kill(os.getpid(),signal.SIGTERM)'],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 143)

    def test_unowned_nonempty_directory_is_not_modified(self):
        self.root.mkdir()
        unrelated = self.root / 'existing-user-file.txt'
        unrelated.write_text('keep')
        before = self.root.stat().st_mode
        with self.assertRaises(OSError):
            logged.secure_directory(self.root)
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(self.root.stat().st_mode, before)

    def test_retention_only_prunes_completed_runs(self):
        logged.secure_directory(self.root)
        old = self.root / 'run-old'
        active = self.root / 'run-active'
        old.mkdir()
        active.mkdir()
        (old / 'result.json').write_text('{}')
        earlier = time.time() - 9 * 86400
        os.utime(old / 'result.json', (earlier, earlier))
        logged.prune(self.root)
        self.assertFalse(old.exists())
        self.assertTrue(active.exists())

    def test_symlink_log_directory_refused(self):
        if os.name == 'nt':
            self.skipTest('Windows symlinks require separate privileges')
        target = Path(self.tmp.name) / 'target'
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            logged.secure_directory(self.root)


if __name__ == '__main__':
    unittest.main()
