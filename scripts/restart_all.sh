#!/bin/sh
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

sh scripts/stop_all.sh || true
sleep 1
sh scripts/start_all.sh