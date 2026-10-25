#!/usr/bin/env python3
"""Take a backup bundle (roadmap B-146).

    uv run python scripts/backup.py [--out DIR]

A bundle is a directory:

    <out>/
      data.db            the database, copied with SQLite's
                         ONLINE backup API (safe against a
                         live, WAL-mode database — no torn
                         pages, no stopping the world)
      storage/…          every stored file, byte for byte
      manifest.json      sha256 + size of every file in the
                         bundle, so a restore can verify the
                         bundle before trusting it
      RESTORE.md         how to bring this bundle back

Source paths come from the normal settings (GPA_DATABASE_URL,
GPA_STORAGE_DIR). The default output is
``backups/gpa-backup-<UTC timestamp>`` beside the deployment.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gpa.config import get_settings  # noqa: E402

RESTORE_MD = """# Restoring this backup

    uv run python scripts/restore.py <this-bundle-dir> <target-dir>

The restore verifies every sha256 in manifest.json BEFORE
placing anything, then writes:

    <target-dir>/data.db     the database
    <target-dir>/storage/    the stored files

Run the app against it with:

    GPA_DATABASE_URL=sqlite:///<target-dir>/data.db \\
    GPA_STORAGE_DIR=<target-dir>/storage \\
    uv run gpa-serve

(or the equivalent worker command). If verification fails,
the bundle is damaged — do not force it; use an older one.
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _db_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise SystemExit(
            "backup.py backs up SQLite deployments; the configured "
            f"database URL is not sqlite: {database_url!r}"
        )
    return Path(database_url.split("sqlite:///", 1)[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up the Group Planning Assistant.")
    parser.add_argument("--out", type=Path, default=None, help="Bundle directory")
    args = parser.parse_args()

    settings = get_settings()
    source_db = _db_path(settings.database_url)
    if not source_db.is_file():
        raise SystemExit(f"database not found: {source_db}")
    storage_dir = Path(settings.storage_dir)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or (ROOT / "backups" / f"gpa-backup-{stamp}")
    if out.exists():
        raise SystemExit(f"bundle directory already exists: {out}")
    (out / "storage").mkdir(parents=True)

    # Online backup: SQLite's own API copies a consistent
    # snapshot of a live database, WAL and all.
    source = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(out / "data.db")
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    storage_entries = []
    if storage_dir.is_dir():
        for path in sorted(storage_dir.rglob("*")):
            if path.is_file():
                relative = path.relative_to(storage_dir)
                destination = out / "storage" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                storage_entries.append({
                    "path": str(relative),
                    "sha256": _sha256(path),
                    "bytes": path.stat().st_size,
                })

    manifest = {
        "product": "Group Planning Assistant — backup bundle (B-146)",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "file": "data.db",
            "sha256": _sha256(out / "data.db"),
            "bytes": (out / "data.db").stat().st_size,
        },
        "storage_files": storage_entries,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (out / "RESTORE.md").write_text(RESTORE_MD, encoding="utf-8")
    print(
        f"backup written: {out} "
        f"(database {manifest['database']['bytes']} bytes, "
        f"{len(storage_entries)} storage files)"
    )


if __name__ == "__main__":
    main()
