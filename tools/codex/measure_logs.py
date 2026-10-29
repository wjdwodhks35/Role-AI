"""Offline paired terminal-output measurements, not Codex token measurements."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

SCRIPT = Path(__file__).with_name('run_logged.py')


def measure():
    cases = {
        'short': ('print("ok")', 0, ['ok']),
        'long_success': ('print("test_case (suite.Unit) ... ok\\n"*2000, end="");print("2000 tests passed")', 0, ['2000 tests passed']),
        'failure': ('import sys;print("test_case (suite.Unit) ... ok\\n"*2000,end="");print(\'Traceback (most recent call last):\\n  File "example.py", line 42, in run\\nValueError: synthetic failure\',file=sys.stderr);sys.exit(37)', 37, ['Traceback', 'example.py', 'line 42', 'ValueError: synthetic failure']),
    }
    rows = []
    with tempfile.TemporaryDirectory() as temp:
        for name, (source, code, facts) in cases.items():
            command = [sys.executable, '-X', 'utf8', '-c', source]
            direct = subprocess.run(command, capture_output=True, timeout=30)
            wrapped = subprocess.run([sys.executable, str(SCRIPT), '--log-root', str(Path(temp) / 'logs'), '--', *command], capture_output=True, timeout=30)
            before = (direct.stdout + direct.stderr).decode('utf-8')
            after = (wrapped.stdout + wrapped.stderr).decode('utf-8')
            assert direct.returncode == wrapped.returncode == code
            assert all(fact in before and fact in after for fact in facts)
            if name == 'short':
                assert direct.stdout == wrapped.stdout and direct.stderr == wrapped.stderr
            else:
                assert len(after) < len(before) // 10
            rows.append({'case': name, 'before_characters': len(before), 'after_characters': len(after),
                         'before_utf8_bytes': len(direct.stdout + direct.stderr),
                         'after_utf8_bytes': len(wrapped.stdout + wrapped.stderr),
                         'exit_code': code, 'diagnostic_facts_preserved': len(facts)})
    return rows


if __name__ == '__main__':
    print(json.dumps({'metric': 'terminal characters / UTF-8 bytes, NOT tokens', 'cases': measure()}, indent=2))
