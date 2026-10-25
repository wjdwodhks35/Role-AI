import json
import os
import subprocess
import time
from pathlib import Path

env = dict(os.environ)
env['OPENCLAW_CONFIG_PATH'] = '/home/serveradmin/.openclaw/windows-control.json'
env['OPENCLAW_STATE_DIR'] = '/home/serveradmin/.openclaw/windows-control-state'
try:
    config = json.loads(Path(env['OPENCLAW_CONFIG_PATH']).read_text())
    expected = config['tools']['exec']['node']
    for attempt in range(5):
        result = subprocess.run(
            ['/home/serveradmin/.openclaw/bin/openclaw', 'nodes', 'status', '--json', '--timeout', '3000'],
            env=env, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            nodes = json.loads(result.stdout).get('nodes', [])
            if any(node.get('nodeId') == expected and node.get('connected') and node.get('paired') for node in nodes):
                print('Windows Companion configured node: paired and connected')
                break
        if attempt < 4:
            time.sleep(2)
    else:
        print('WARNING: Windows Companion node is not connected. Open Companion, connect to the existing Gateway at ws://127.0.0.1:18790, and check Node mode. No token or approval policy was changed.')
        raise SystemExit(1)
except Exception as error:
    print('WARNING: Companion node check failed (' + type(error).__name__ + '). Check the Companion connection manually.')
    raise SystemExit(1)
