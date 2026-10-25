"""Phase 6 migration tests (roadmap B-003 discipline).

Revision 0009 adds the truth-keeping schema — the events
tombstone stamp, contradiction rows, announcement drafts,
and the tombstone records. It must take a Phase 5-era
database UP and back DOWN cleanly, on top of the guarded
0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys


def _alembic(tmp_path, *args):
    url = "sqlite:///" + str(tmp_path / "mig.db")
    env = dict(os.environ, GPA_DATABASE_URL=url)
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


def test_phase6_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    assert "deleted_at" in _columns(tmp_path, "events")
    assert {"event_contradictions", "announcement_drafts", "tombstones"} <= _tables(tmp_path)

    _alembic(tmp_path, "downgrade", "0008_phase5_vision")
    assert "deleted_at" not in _columns(tmp_path, "events")
    assert "event_contradictions" not in _tables(tmp_path)
    assert "announcement_drafts" not in _tables(tmp_path)
    assert "tombstones" not in _tables(tmp_path)

    _alembic(tmp_path, "upgrade", "head")
    assert "deleted_at" in _columns(tmp_path, "events")
    assert {"event_contradictions", "announcement_drafts", "tombstones"} <= _tables(tmp_path)
