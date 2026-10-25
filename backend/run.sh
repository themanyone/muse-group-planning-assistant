#!/bin/sh
# Group Planning Assistant — native start script (roadmap B-001).
# No Docker, ever: this runs the app directly under uvicorn.
# Usage: ./run.sh [--demo]   (--demo loads the demo seed first)
set -e
cd "$(dirname "$0")"

if [ "$1" = "--demo" ]; then
    uv run python scripts/seed.py --demo
fi

exec uv run uvicorn gpa.main:create_app --factory --host 127.0.0.1 --port 8000
