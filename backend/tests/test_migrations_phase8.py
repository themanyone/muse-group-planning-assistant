"""Phase 8 migration tests (roadmap B-003 discipline).

Revision 0011 adds the comms schema — notifications,
notification_preferences, member_notification_settings,
event_invites, digests, mail_events — plus the groups settings
columns and the announcement drafts' sending record. It must
take a Phase 7-era database UP and back DOWN cleanly, on top
of the guarded 0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys

PHASE8_TABLES = {
    "notifications", "notification_preferences",
    "member_notification_settings", "event_invites", "digests",
    "mail_events",
}
GROUP_COLUMNS = {
    "reminder_rules_json", "digest_weekday", "digest_time",
    "digest_enabled", "ai_reminder_wording",
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


def test_phase8_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    assert PHASE8_TABLES <= _tables(tmp_path)
    assert GROUP_COLUMNS <= _columns(tmp_path, "groups")
    assert {"sent_at", "sent_by"} <= _columns(tmp_path, "announcement_drafts")
    # The columns the outbox's laws ride on exist.
    assert {"send_after", "status", "job_id", "template_body", "wording_source"} <= _columns(
        tmp_path, "notifications"
    )
    assert {"token_hash", "token_prefix"} <= _columns(tmp_path, "event_invites")

    _alembic(tmp_path, "downgrade", "0010_phase7_planning")
    assert PHASE8_TABLES.isdisjoint(_tables(tmp_path))
    assert GROUP_COLUMNS.isdisjoint(_columns(tmp_path, "groups"))
    assert {"sent_at", "sent_by"}.isdisjoint(_columns(tmp_path, "announcement_drafts"))
    # The Phase 7 planning schema is untouched by the round trip.
    assert "tasks" in _tables(tmp_path)

    _alembic(tmp_path, "upgrade", "head")
    assert PHASE8_TABLES <= _tables(tmp_path)
