#!/bin/sh
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

REMOTE_HOST="$(python3 - <<'PY'
import json
cfg = json.load(open('config.json', 'r', encoding='utf-8'))
print(cfg['remote_host'])
PY
)"
REMOTE_USER="$(python3 - <<'PY'
import json
cfg = json.load(open('config.json', 'r', encoding='utf-8'))
print(cfg['remote_user'])
PY
)"
REMOTE_PORT="$(python3 - <<'PY'
import json
cfg = json.load(open('config.json', 'r', encoding='utf-8'))
print(cfg['remote_port'])
PY
)"

echo "[1/2] SSH reachability"
ssh -o BatchMode=yes -o ConnectTimeout=5 "${REMOTE_USER}@${REMOTE_HOST}" 'echo ok'

echo "[2/2] Remote port listen check"
if ssh -o BatchMode=yes -o ConnectTimeout=5 "${REMOTE_USER}@${REMOTE_HOST}" 'ss -lnt' 2>/dev/null | grep -q ":${REMOTE_PORT}"; then
  echo "remote port ${REMOTE_PORT} is listening (ss)"
  exit 0
fi

if ssh -o BatchMode=yes -o ConnectTimeout=5 "${REMOTE_USER}@${REMOTE_HOST}" 'netstat -lnt' 2>/dev/null | grep -q ":${REMOTE_PORT}"; then
  echo "remote port ${REMOTE_PORT} is listening (netstat)"
  exit 0
fi

echo "remote port ${REMOTE_PORT} is not listening"
exit 1