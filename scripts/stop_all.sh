#!/bin/sh
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

python3 - <<'PY'
import json, time, uuid
from pathlib import Path
p = Path("runtime/control.json")
p.write_text(json.dumps({"id": str(uuid.uuid4()), "action": "stop_supervisor", "payload": {}, "timestamp": int(time.time())}, indent=2) + "\n", encoding="utf-8")
print("stop requested")
PY

sleep 2

if [ -f runtime/supervisor.pid ]; then
  pid="$(cat runtime/supervisor.pid 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
fi

for f in runtime/autossh.pid runtime/pproxy.pid; do
  if [ -f "$f" ]; then
    pid="$(cat "$f" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  fi
done