"""Contract snapshot (B-006): the committed OPENAPI.json must equal
the live schema. Change a schema → this goes red → regenerate with
scripts/export_openapi.py (+ generate_ts_types.py) → green again."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_openapi_snapshot_matches_live_schema(app):
    snapshot_path = ROOT / "OPENAPI.json"
    assert snapshot_path.exists(), "OPENAPI.json missing — run scripts/export_openapi.py"
    committed = json.loads(snapshot_path.read_text(encoding="utf-8"))
    live = json.loads(json.dumps(app.openapi()))
    assert committed == live, (
        "OPENAPI.json has drifted from the code. "
        "Run: uv run python scripts/export_openapi.py && "
        "uv run python scripts/generate_ts_types.py"
    )
