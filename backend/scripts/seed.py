#!/usr/bin/env python3
"""Load seed data (roadmap B-009/B-010).

    uv run python scripts/seed.py            # every seeds/*.json
    uv run python scripts/seed.py --demo     # just the demo seed

Seeding is idempotent: running it twice changes nothing the second
time (proven by the double-seed test).
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gpa.config import get_settings  # noqa: E402
from gpa.db import Database  # noqa: E402
from gpa.services.seed import load_all_seeds, load_seed_file  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Load Group Planning Assistant seeds.")
    parser.add_argument("--demo", action="store_true", help="Load only seeds/demo.json")
    args = parser.parse_args()

    settings = get_settings()
    db = Database(settings.database_url)
    db.create_all()
    session = db.session()
    try:
        if args.demo:
            result = load_seed_file(session, ROOT / "seeds" / "demo.json")
        else:
            result = load_all_seeds(session, ROOT / "seeds")
        print(f"Seed complete: {result}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
