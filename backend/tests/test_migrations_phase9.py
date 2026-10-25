"""Phase 9 migration tests (roadmap B-003 discipline).

Revision 0012 adds the nonprofit-pack schema — subgroups,
subgroup_memberships, announcements, resources,
resource_bookings, attendance_marks, candidate_approvals —
plus the scope columns on events/tasks, the shift window on
role_slots, and the settings pack's columns on groups. It must
take a Phase 8-era database UP and back DOWN cleanly, on top
of the guarded 0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys

PHASE9_TABLES = {
    "subgroups", "subgroup_memberships", "announcements",
    "resources", "resource_bookings", "attendance_marks",
    "candidate_approvals",
}
GROUP_COLUMNS = {"review_min_role", "review_approvals_required", "template_set"}


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


def test_phase9_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    assert PHASE9_TABLES <= _tables(tmp_path)
    assert GROUP_COLUMNS <= _columns(tmp_path, "groups")
    assert "subgroup_id" in _columns(tmp_path, "events")
    assert "subgroup_id" in _columns(tmp_path, "tasks")
    assert {"starts_at", "ends_at"} <= _columns(tmp_path, "role_slots")
    # The columns the pack's laws ride on exist.
    assert {"subgroup_id", "user_id"} <= _columns(tmp_path, "subgroup_memberships")
    assert {"resource_id", "event_id"} <= _columns(tmp_path, "resource_bookings")
    assert {"candidate_id", "approver_user_id"} <= _columns(tmp_path, "candidate_approvals")

    _alembic(tmp_path, "downgrade", "0011_phase8_notifications")
    assert PHASE9_TABLES.isdisjoint(_tables(tmp_path))
    assert GROUP_COLUMNS.isdisjoint(_columns(tmp_path, "groups"))
    assert "subgroup_id" not in _columns(tmp_path, "events")
    assert "subgroup_id" not in _columns(tmp_path, "tasks")
    assert {"starts_at", "ends_at"}.isdisjoint(_columns(tmp_path, "role_slots"))
    # The Phase 8 comms schema is untouched by the round trip.
    assert "notifications" in _tables(tmp_path)

    _alembic(tmp_path, "upgrade", "head")
    assert PHASE9_TABLES <= _tables(tmp_path)
