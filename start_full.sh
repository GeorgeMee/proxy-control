#!/bin/sh
set -eu

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

mkdir -p logs runtime

echo "[1/5] Checking virtual environment..."
if [ ! -x ".venv/bin/python" ]; then
  echo "ERROR: .venv not found. Please create it first:"
  echo "  python3 -m venv .venv"
  echo "  . .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  exit 1
fi

echo "[2/5] Stopping old web process if exists..."
if [ -f runtime/web.pid ]; then
  oldpid="$(cat runtime/web.pid 2>/dev/null || true)"
  if [ -n "${oldpid:-}" ] && kill -0 "$oldpid" 2>/dev/null; then
    kill "$oldpid" 2>/dev/null || true
    sleep 1
  fi
fi
pkill -f "python app.py" 2>/dev/null || true
pkill -f "python3 app.py" 2>/dev/null || true

echo "[3/5] Starting web app in background..."
nohup "$BASE_DIR/.venv/bin/python" app.py > "$BASE_DIR/logs/web.log" 2>&1 &
WEB_PID=$!
echo "$WEB_PID" > "$BASE_DIR/runtime/web.pid"
sleep 2

if kill -0 "$WEB_PID" 2>/dev/null; then
  echo "Web started: pid=$WEB_PID"
else
  echo "ERROR: Web failed to start. Check logs/web.log"
  exit 1
fi

echo "[4/5] Starting supervisor and proxy chain..."
sh "$BASE_DIR/scripts/start_all.sh"

echo "[5/5] Done."
echo
echo "Project dir: $BASE_DIR"
echo "Web log:     $BASE_DIR/logs/web.log"
echo "Status file: $BASE_DIR/runtime/status.json"
echo
echo "Open in browser:"
echo "  http://localhost:5001"
echo
echo "If localhost doesn't work in your browser, try:"
echo "  http://127.0.0.1:5001"
