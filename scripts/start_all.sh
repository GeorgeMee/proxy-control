#!/bin/sh
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ -f runtime/supervisor.pid ]; then
  pid="$(cat runtime/supervisor.pid 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "supervisor already running pid=$pid"
  else
    python3 supervisor.py >/dev/null 2>&1 &
    sleep 1
  fi
else
  python3 supervisor.py >/dev/null 2>&1 &
  sleep 1
fi

python3 - <<'PY'
import json, time, uuid
from pathlib import Path
p = Path("runtime/control.json")
p.write_text(json.dumps({"id": str(uuid.uuid4()), "action": "start", "payload": {}, "timestamp": int(time.time())}, indent=2) + "\n", encoding="utf-8")
print("start requested")
PY