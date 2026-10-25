#!/bin/sh
# Group Planning Assistant — the demo, from a blank machine
# (roadmap B-147). No Docker, ever: native uv + uvicorn only.
#   ./demo.sh            seed the demo story, then serve
#   ./demo.sh --seed-only   seed only (used by the freeze drill)
# Seeding is idempotent: running this twice changes nothing
# the second time. Demo logins (password demo-password-123 for
# every demo person): mara@example.org (Riverbend owner),
# ana@example.org (Hiking Club owner), demo@example.org.
set -e
cd "$(dirname "$0")"
uv run python scripts/seed.py --demo
if [ "$1" = "--seed-only" ]; then
    echo "Demo story seeded."
    exit 0
fi
exec uv run uvicorn gpa.main:create_app --factory --host 127.0.0.1 --port 8000
