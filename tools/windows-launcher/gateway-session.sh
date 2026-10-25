#!/usr/bin/env bash
set -euo pipefail
[[ $(id -un) == serveradmin ]] || { echo 'Gateway must run as serveradmin'; exit 1; }
export OPENCLAW_CONFIG_PATH=/home/serveradmin/.openclaw/windows-control.json
export OPENCLAW_STATE_DIR=/home/serveradmin/.openclaw/windows-control-state
cli=/home/serveradmin/.openclaw/bin/openclaw
[[ -f $OPENCLAW_CONFIG_PATH && -x $cli ]] || { echo 'PC Gateway configuration or CLI missing'; exit 1; }
umask 077
mkdir -p "$OPENCLAW_STATE_DIR/launcher"
exec 9>"$OPENCLAW_STATE_DIR/launcher/gateway.lock"
flock -n 9 || { echo 'Gateway launcher already running'; exit 0; }
if python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",18790),2); s.close()' 2>/dev/null; then
  "$cli" health >/dev/null 2>&1 || { echo 'Port 18790 occupied; Gateway health failed. No process was stopped.'; exit 1; }
  echo 'Existing PC Gateway is healthy'; exit 0
fi
"$cli" config validate
"$cli" gateway 2>&1 | tee -a "$OPENCLAW_STATE_DIR/launcher/gateway.log"
