#!/usr/bin/env python3
"""Build a release ZIP (roadmap B-010; anatomy in roadmap §8).

Two shapes:

    uv run python scripts/make_release_zip.py ZIP-0

        The founding shape: the repo tree flat in the archive,
        a manifest.json of per-file SHA-256 hashes, and a
        sidecar ``<name>.zip.sha256``.

    GPA_RELEASE_LIMITS_FILE=<path> \\
        uv run python scripts/make_release_zip.py v0.3 2026-10-24 B-104

        The §8 train anatomy (ZIP-Alpha onward):

            gpa-backend-v<VERSION>-<DATE>.zip
              gpa-backend/…          # the repo tree, runnable as-is
              OPENAPI.json           # the frozen contract
              FRONTEND_HANDOFF.md    # plain words first
              CHANGELOG.md           # per-slice list
              TEST_REPORT.md         # this ZIP's real test counts
              SHA256SUMS.txt         # hash of every file in the zip
              release-manifest.json  # version, git hash, slice
                                     # range, AI driver modes,
                                     # known limits (from the
                                     # limits file, one per line)

        The top-level documents are read from the repo root —
        the release is only as honest as what is committed
        there, and the script refuses to build without them.

Excluded, always: .git, virtualenvs, caches, databases, runtime
data, and ANY .env file — a ZIP must never carry a secret (D-25).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "data", "releases", ".mypy_cache", ".ruff_cache", ".forge-lock"}
EXCLUDED_SUFFIXES = {".pyc", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".zip"}

TOP_LEVEL_DOCS = ("OPENAPI.json", "FRONTEND_HANDOFF.md", "CHANGELOG.md", "TEST_REPORT.md")


def _included(path: Path) -> bool:
    """Is this file allowed into a release ZIP?"""
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False  # Real env files are secrets; only the example ships.
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    return True


def _tree_files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and _included(p))


def _write_sidecar(zip_path: Path) -> str:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (zip_path.parent / f"{zip_path.name}.sha256").write_text(
        f"{digest}  {zip_path.name}\n", encoding="utf-8"
    )
    return digest


def build_flat(name: str) -> None:
    """The ZIP-0 shape (kept for reproducibility)."""
    out_dir = ROOT / "releases"
    out_dir.mkdir(exist_ok=True)
    zip_path = out_dir / f"group-planning-assistant-backend-{name}.zip"

    files = _tree_files()
    manifest = {"release": name, "files": {}}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = str(path.relative_to(ROOT))
            data = path.read_bytes()
            manifest["files"][rel] = hashlib.sha256(data).hexdigest()
            zf.writestr(rel, data)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))

    digest = _write_sidecar(zip_path)
    print(f"Built {zip_path} ({zip_path.stat().st_size:,} bytes, {len(files)} files)")
    print(f"sha256: {digest}")


def build_train(version: str, date: str, slice_end: str) -> None:
    """The §8 anatomy: tree under gpa-backend/, the release
    documents at the top level, hashes for everything."""
    for doc in TOP_LEVEL_DOCS:
        if not (ROOT / doc).is_file():
            raise SystemExit(
                f"Refusing to build: {doc} is missing from the repo root. "
                "A train ZIP's documents are committed artefacts, not afterthoughts."
            )
    limits_path = os.environ.get("GPA_RELEASE_LIMITS_FILE")
    known_limits = []
    if limits_path:
        known_limits = [
            line.strip()
            for line in Path(limits_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    git_hash = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    out_dir = ROOT / "releases"
    out_dir.mkdir(exist_ok=True)
    zip_path = out_dir / f"gpa-backend-{version}-{date}.zip"

    entries: dict[str, bytes] = {}
    for path in _tree_files():
        rel = str(path.relative_to(ROOT))
        entries[f"gpa-backend/{rel}"] = path.read_bytes()
    for doc in TOP_LEVEL_DOCS:
        entries[doc] = (ROOT / doc).read_bytes()

    manifest = {
        "version": version,
        "date": date,
        "git_hash": git_hash,
        "slice_range": f"B-001 → {slice_end}",
        "ai_driver_modes": ["live", "fixture", "mock"],
        "known_limits": known_limits,
    }
    entries["release-manifest.json"] = json.dumps(
        manifest, indent=2, sort_keys=True
    ).encode("utf-8")
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(entries.items())
    )
    entries["SHA256SUMS.txt"] = sums.encode("utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in sorted(entries):
            zf.writestr(name, entries[name])

    digest = _write_sidecar(zip_path)
    print(f"Built {zip_path} ({zip_path.stat().st_size:,} bytes, {len(entries)} files)")
    print(f"sha256: {digest}")


def main() -> None:
    if len(sys.argv) >= 2 and re.fullmatch(r"v\d+\.\d+", sys.argv[1]):
        if len(sys.argv) != 4:
            raise SystemExit(
                "Train shape: make_release_zip.py <version> <date> <slice-end>\n"
                "  e.g. make_release_zip.py v0.3 2026-10-24 B-104"
            )
        build_train(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        build_flat(sys.argv[1] if len(sys.argv) > 1 else "ZIP-0")


if __name__ == "__main__":
    main()
