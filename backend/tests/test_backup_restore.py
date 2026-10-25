"""Backup + restore scripts (roadmap B-146): a fast round
trip — build a small instance (a real SQLite file + storage
objects), back it up, restore it, and find the marker data
byte-identical. A tampered bundle must refuse to restore.
The full drill (fresh uv sync + the whole suite + a booted
read-back in a copied tree) is scripts/restore_drill.py; its
latest timings live in TEST_REPORT.md.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args, env_extra=None, expect=0):
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == expect, result.stdout + result.stderr
    return result


def test_backup_restore_round_trip(tmp_path):
    # A small live instance: one database with a marker row,
    # one storage object.
    data_dir = tmp_path / "live"
    (data_dir / "storage" / "ab" / "cd").mkdir(parents=True)
    db_path = data_dir / "app.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE marker (words TEXT)")
    conn.execute("INSERT INTO marker VALUES ('backup-marker-words')")
    conn.commit()
    conn.close()
    (data_dir / "storage" / "ab" / "cd" / "object.bin").write_bytes(
        b"stored-bytes-marker"
    )

    bundle = tmp_path / "bundle"
    _run(
        "scripts/backup.py", "--out", str(bundle),
        env_extra={
            "GPA_DATABASE_URL": f"sqlite:///{db_path}",
            "GPA_STORAGE_DIR": str(data_dir / "storage"),
            "GPA_SECRET_KEY": "test-secret-for-tests-only",
            "GPA_ENV": "test",
        },
    )
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "RESTORE.md").is_file()

    target = tmp_path / "restored"
    _run("scripts/restore.py", str(bundle), str(target))
    conn = sqlite3.connect(target / "data.db")
    (words,) = conn.execute("SELECT words FROM marker").fetchone()
    conn.close()
    assert words == "backup-marker-words"
    assert (
        target / "storage" / "ab" / "cd" / "object.bin"
    ).read_bytes() == b"stored-bytes-marker"

    # A tampered bundle restores NOTHING and says so.
    (bundle / "storage" / "ab" / "cd" / "object.bin").write_bytes(b"forged")
    target2 = tmp_path / "restored2"
    result = _run(
        "scripts/restore.py", str(bundle), str(target2), expect=1
    )
    assert "damaged" in result.stdout
    assert not (target2 / "data.db").exists()
