#!/bin/sh
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

echo "Activating virtual environment..."
. .venv/bin/activate

echo "Starting Flask web server..."
exec python app.py
