"""Phase 5 migration tests (roadmap B-003 discipline).

Revision 0008 adds the vision columns — provenance regions, the
flyer's perceptual hash, the generated flag. It must take a
Phase 4-era database UP and back DOWN cleanly, on top of the
guarded 0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys


def _alembic(tmp_path, *args):
    env = dict(os.environ, GPA_DATABASE_URL='sqlite:///' + str(tmp_path / "mig.db"))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_repo_root()), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def _columns(tmp_path, table: str) -> set[str]:
    conn = sqlite3.connect(tmp_path / "mig.db")
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_phase5_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    assert "region_json" in _columns(tmp_path, "provenance")
    assert {"phash", "generated"} <= _columns(tmp_path, "flyers")

    _alembic(tmp_path, "downgrade", "0007_phase4_events")
    assert "region_json" not in _columns(tmp_path, "provenance")
    assert "phash" not in _columns(tmp_path, "flyers")

    _alembic(tmp_path, "upgrade", "head")
    assert "region_json" in _columns(tmp_path, "provenance")
    assert {"phash", "generated"} <= _columns(tmp_path, "flyers")
