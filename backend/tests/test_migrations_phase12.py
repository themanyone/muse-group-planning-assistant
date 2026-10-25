"""Phase 12 migration tests (roadmap B-003 discipline).

Revision 0015 adds no tables — four guarded columns:
``external_url`` on external_event_links (a Facebook copy's
address, B-131), ``send_messenger`` + ``messenger_status``
on notifications (the Messenger channel, B-134), and
``embed_origins_json`` on groups (the embed register,
B-135). It must take a Phase 11-era database UP and back
DOWN cleanly, on top of the guarded 0001 that builds from
live metadata.
"""

import os
import sqlite3
import subprocess
import sys

ADDED = {
    ("external_event_links", "external_url"),
    ("notifications", "send_messenger"),
    ("notifications", "messenger_status"),
    ("groups", "embed_origins_json"),
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


def _columns(tmp_path, table: str) -> set[str]:
    conn = sqlite3.connect(tmp_path / "mig.db")
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_phase12_migration_up_down_up(tmp_path):
    _alembic(tmp_path, "upgrade", "head")
    for table, column in ADDED:
        assert column in _columns(tmp_path, table), f"{table}.{column}"

    _alembic(tmp_path, "downgrade", "0014_phase11_integrations")
    for table, column in ADDED:
        assert column not in _columns(tmp_path, table), f"{table}.{column}"
    # The Phase 11 bridge schema is untouched by the round trip.
    assert "config_sealed" in _columns(tmp_path, "integrations")
    assert "payload_hash" in _columns(tmp_path, "external_event_links")

    _alembic(tmp_path, "upgrade", "head")
    for table, column in ADDED:
        assert column in _columns(tmp_path, table), f"{table}.{column}"
