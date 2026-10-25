"""Phase 11 migration tests (roadmap B-003 discipline).

Revision 0014 adds the bridge schema — account links, live
sync state, integrations, the webhook doors, external event
links, outbound deliveries — plus ``sender_ref`` on
source_chunks (the speaker id an account link keys on). It
must take a Phase 10-era database UP and back DOWN cleanly,
on top of the guarded 0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys

PHASE11_TABLES = {
    "external_account_links",
    "live_sync_states",
    "integrations",
    "webhook_endpoints",
    "webhook_receipts",
    "external_event_links",
    "outbound_webhooks",
    "outbound_deliveries",
}


def _alembic(tmp_path, *args):
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


def test_phase11_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    assert PHASE11_TABLES <= _tables(tmp_path)
    assert "sender_ref" in _columns(tmp_path, "source_chunks")
    # The columns the phase's laws ride on exist.
    assert {"config_sealed", "status", "last_error"} <= _columns(
        tmp_path, "integrations"
    )
    assert {"cursor", "messages_seen"} <= _columns(tmp_path, "live_sync_states")
    assert {"payload_hash", "external_id"} <= _columns(
        tmp_path, "external_event_links"
    )

    _alembic(tmp_path, "downgrade", "0013_phase10_memory")
    assert PHASE11_TABLES.isdisjoint(_tables(tmp_path))
    assert "sender_ref" not in _columns(tmp_path, "source_chunks")
    # The Phase 10 memory schema is untouched by the round trip.
    assert "summaries" in _tables(tmp_path)

    _alembic(tmp_path, "upgrade", "head")
    assert PHASE11_TABLES <= _tables(tmp_path)
