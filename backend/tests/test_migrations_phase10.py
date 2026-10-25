"""Phase 10 migration tests (roadmap B-003 discipline).

Revision 0013 adds the memory schema — ``summaries`` and
``prune_runs`` — plus the settings pack's memory dials on
``groups`` (prune pass cap, token budget, judging driver). It
must take a Phase 9-era database UP and back DOWN cleanly, on
top of the guarded 0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys

PHASE10_TABLES = {"summaries", "prune_runs"}
GROUP_COLUMNS = {"prune_max_passes", "prune_token_budget", "prune_driver"}


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


def test_phase10_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    assert PHASE10_TABLES <= _tables(tmp_path)
    assert GROUP_COLUMNS <= _columns(tmp_path, "groups")
    # The accountability columns the memory laws ride on exist.
    assert {"range_json", "citations_json", "verified"} <= _columns(
        tmp_path, "summaries"
    )
    assert {"kept_json", "dropped_json", "passes", "cost_micros"} <= _columns(
        tmp_path, "prune_runs"
    )

    _alembic(tmp_path, "downgrade", "0012_phase9_nonprofit")
    assert PHASE10_TABLES.isdisjoint(_tables(tmp_path))
    assert GROUP_COLUMNS.isdisjoint(_columns(tmp_path, "groups"))
    # The Phase 9 nonprofit schema is untouched by the round trip.
    assert "subgroups" in _tables(tmp_path)

    _alembic(tmp_path, "upgrade", "head")
    assert PHASE10_TABLES <= _tables(tmp_path)
