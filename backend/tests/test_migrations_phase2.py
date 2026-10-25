"""Phase 2 migration tests (roadmap B-003 discipline, B-027).

Revision 0002 must take a Phase 0/1-era database UP to the Phase 2
schema and back DOWN cleanly — and, because revision 0001 builds
from live metadata, it must also survive running against a
brand-new file where the tables already exist when 0002 starts.
"""

import os
import sqlite3
import subprocess
import sys


def _alembic(tmp_path, *args):
    """Run one Alembic command against a throwaway database file."""
    env = dict(os.environ, GPA_DATABASE_URL=f"sqlite:///{tmp_path}/mig.db")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_repo_root()), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def _tables(tmp_path) -> set[str]:
    conn = sqlite3.connect(tmp_path / "mig.db")
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _columns(tmp_path, table: str) -> set[str]:
    conn = sqlite3.connect(tmp_path / "mig.db")
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_phase2_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    tables = _tables(tmp_path)
    assert {"sources", "source_chunks", "provenance", "flyers",
            "inbound_addresses"} <= tables
    assert {"stage", "progress"} <= _columns(tmp_path, "sources")
    assert {"source_id", "source_deleted_at"} <= _columns(tmp_path, "candidates")

    _alembic(tmp_path, "downgrade", "0001_initial")
    tables = _tables(tmp_path)
    assert "flyers" not in tables and "sources" not in tables
    assert "users" in tables  # The Phase 0/1 schema survives the trip down.
    assert "source_id" not in _columns(tmp_path, "candidates")

    _alembic(tmp_path, "upgrade", "head")
    assert "flyers" in _tables(tmp_path)
