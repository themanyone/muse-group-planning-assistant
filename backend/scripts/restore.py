#!/usr/bin/env python3
"""Restore a backup bundle (roadmap B-146).

    uv run python scripts/restore.py <bundle-dir> <target-dir>

Verifies EVERY sha256 in the bundle's manifest.json first —
a damaged bundle restores nothing and says so — then places
``data.db`` and ``storage/`` under the target directory.
Pure standard library: a restore must work in exactly the
situation where the app's environment may not.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    bundle = Path(sys.argv[1])
    target = Path(sys.argv[2])
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        print(f"not a backup bundle (no manifest.json): {bundle}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Verify first, place nothing until the whole bundle
    # proves sound.
    checks = [(bundle / "data.db", manifest["database"])]
    checks += [
        (bundle / "storage" / entry["path"], entry)
        for entry in manifest.get("storage_files", [])
    ]
    for path, entry in checks:
        if not path.is_file():
            print(f"bundle is incomplete: missing {path}")
            return 1
        if _sha256(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            print(f"bundle is damaged: {path} fails its manifest check")
            return 1

    if target.exists() and any(target.iterdir()):
        print(f"target directory is not empty: {target}")
        return 1
    (target / "storage").mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle / "data.db", target / "data.db")
    for entry in manifest.get("storage_files", []):
        destination = target / "storage" / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / "storage" / entry["path"], destination)
    print(
        f"restored: {target} "
        f"(database + {len(manifest.get('storage_files', []))} storage files, "
        "all manifest checks passed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
