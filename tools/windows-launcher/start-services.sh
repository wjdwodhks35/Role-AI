#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'WSL root required'; exit 1; }
files=(/root/Role-AI/compose.yaml /root/Role-AI/compose.override.yaml /etc/role-ai/openclaw/compose.yaml)
for file in "${files[@]}"; do
  [[ -f $file ]] || { echo "Missing required file: $file"; exit 1; }
done
if ! docker info >/dev/null 2>&1; then
  systemctl start docker
fi
docker info >/dev/null
args=(--project-directory /root/Role-AI)
for file in "${files[@]}"; do args+=(-f "$file"); done
# A stopped Gateway can retain a network reference to a replaced API container.
# Recreate only that dependent container when its reference is provably stale.
docker compose "${args[@]}" up -d --no-build --pull never --no-recreate --wait --wait-timeout 120 api
api_id=$(docker compose "${args[@]}" ps -q api)
gateway_id=$(docker compose "${args[@]}" ps -aq openclaw)
stale=false
if [[ -n $gateway_id ]]; then
  current_api=$(docker inspect --format '{{.Id}}' "$api_id")
  network=$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$gateway_id")
  if [[ $network == container:* && $network != "container:$current_api" && $network != "container:$api_id" ]]; then
    stale=true
  fi
fi
if [[ $stale == true ]]; then
  echo 'Repairing stale OpenClaw API network reference using the existing image.'
  docker compose "${args[@]}" up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 openclaw
else
  docker compose "${args[@]}" up -d --no-deps --no-build --pull never --no-recreate --wait --wait-timeout 120 openclaw
fi
python3 - <<'HEALTH'
import json, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8000/api/health', timeout=10) as response:
    if json.load(response).get('status') != 'ok':
        raise SystemExit('API health failed')
print('API health: ok')
HEALTH
