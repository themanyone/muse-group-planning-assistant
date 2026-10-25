"""Phase 13 migration tests (roadmap B-003 discipline).

Revision 0016 adds no tables — two guarded columns on
``groups``: ``upload_quota_bytes`` (the per-group storage
quota, B-141) and ``source_retention_days`` (the retention
dial, B-144). It must take a Phase 12-era database UP and
back DOWN cleanly, backfill the quota default for existing
rows (retention stays NULL — keep forever — until a group
chooses otherwise), and sit happily on top of the guarded
0001 that builds from live metadata.
"""

import os
import sqlite3
import subprocess
import sys

ADDED = {
    ("groups", "upload_quota_bytes"),
    ("groups", "source_retention_days"),
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


def test_phase13_migration_up_down_up(tmp_path):
    # Note the chain's shape: 0001 builds from LIVE metadata, so
    # on a fresh database the columns exist from the start and
    # 0016's guarded adds are no-ops; on a database created by
    # older code they are genuinely missing until 0016. The
    # backfill below is the part that must work on both roads.
    _alembic(tmp_path, "upgrade", "0015_phase12_publishing")

    # A group row carrying no dial values (as a pre-Phase-13
    # row would). The raw insert fills every NOT NULL column
    # that has no database default — 0001 builds from metadata,
    # whose Python-side defaults are not server defaults.
    conn = sqlite3.connect(tmp_path / "mig.db")
    named = {"id": "g-old", "name": "Old Group", "slug": "old-group"}
    values: dict[str, object] = {}
    for _cid, column, ctype, notnull, default, _pk in conn.execute(
        "PRAGMA table_info(groups)"
    ):
        if column in named:
            values[column] = named[column]
        elif notnull and default is None:
            values[column] = 0 if "INT" in ctype.upper() else "2026-10-01 00:00:00" if column == "created_at" else ""
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO groups ({columns}) VALUES ({marks})",
        tuple(values.values()),
    )
    conn.commit()
    conn.close()

    _alembic(tmp_path, "upgrade", "head")
    for table, column in ADDED:
        assert column in _columns(tmp_path, table), f"{table}.{column}"
    conn = sqlite3.connect(tmp_path / "mig.db")
    quota, retention = conn.execute(
        "SELECT upload_quota_bytes, source_retention_days "
        "FROM groups WHERE id = 'g-old'"
    ).fetchone()
    conn.close()
    # …is backfilled with the quota default, and keeps its
    # sources forever until the group itself chooses a window.
    assert quota == 104_857_600
    assert retention is None

    _alembic(tmp_path, "downgrade", "0015_phase12_publishing")
    for table, column in ADDED:
        assert column not in _columns(tmp_path, table), f"{table}.{column}"
    # The Phase 12 schema is untouched by the round trip.
    assert "embed_origins_json" in _columns(tmp_path, "groups")

    _alembic(tmp_path, "upgrade", "head")
    for table, column in ADDED:
        assert column in _columns(tmp_path, table), f"{table}.{column}"
