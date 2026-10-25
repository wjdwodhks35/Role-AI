import json
import urllib.request
from pathlib import Path

try:
    config = json.loads(Path('/home/serveradmin/.openclaw/windows-control.json').read_text())
    provider = config['models']['providers']['ollama']
    base = provider['baseUrl'].rstrip('/')
    expected = config['agents']['defaults']['model']['primary'].removeprefix('ollama/')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + '/api/tags', timeout=8) as response:
        names = {model['name'] for model in json.load(response)['models']}
    if expected not in names:
        raise ValueError('Configured model is not installed')
    print('Ollama configured endpoint and model: ok')
except Exception as error:
    print('WARNING: Ollama check failed (' + type(error).__name__ + '). Check the configured endpoint and installed model. Settings were not changed.')
    raise SystemExit(1)
