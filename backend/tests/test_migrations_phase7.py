"""Phase 7 migration tests (roadmap B-003 discipline).

Revision 0010 adds the planning schema — tasks, decisions,
open questions, polls and their votes, checklist items, role
slots, cost lines, availability, templates. It must take a
Phase 6-era database UP and back DOWN cleanly, on top of the
guarded 0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys

PLANNING_TABLES = {
    "tasks", "decisions", "open_questions", "polls", "poll_options",
    "poll_votes", "checklist_items", "role_slots", "cost_lines",
    "availability_slots", "availability_marks", "templates",
}


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


def test_phase7_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    assert PLANNING_TABLES <= _tables(tmp_path)
    # The columns the phase's laws ride on exist.
    assert {"owner_id", "suggested_owner_label", "due_at"} <= _columns(tmp_path, "tasks")
    assert {"supersedes_id", "superseded_by_id"} <= _columns(tmp_path, "decisions")
    assert {"asked_of_id", "resolution_decision_id"} <= _columns(tmp_path, "open_questions")
    assert {"amount_cents", "paid_by_id"} <= _columns(tmp_path, "cost_lines")

    _alembic(tmp_path, "downgrade", "0009_phase6_truth")
    assert PLANNING_TABLES.isdisjoint(_tables(tmp_path))
    # The Phase 6 truth schema is untouched by the round trip.
    assert "event_contradictions" in _tables(tmp_path)

    _alembic(tmp_path, "upgrade", "head")
    assert PLANNING_TABLES <= _tables(tmp_path)
