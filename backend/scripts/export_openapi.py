#!/usr/bin/env python3
"""Export the live OpenAPI schema to OPENAPI.json (roadmap B-006).

The committed OPENAPI.json is the frozen contract the frontend crew
builds against; a contract test fails the suite when the live schema
and the snapshot differ, so regenerating is a deliberate act:

    uv run python scripts/export_openapi.py
    uv run python scripts/generate_ts_types.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gpa.main import create_app  # noqa: E402


def main() -> None:
    app = create_app()
    schema = app.openapi()
    out = ROOT / "OPENAPI.json"
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
