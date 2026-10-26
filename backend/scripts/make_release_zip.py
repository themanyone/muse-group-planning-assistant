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
              RESULTS.md             # the generated numbers page
                                     # (B-149; in the tree since
                                     # then, top-level from ZIP-Final)
              SHA256SUMS.txt         # hash of every file in the zip
              sbom.spdx.json         # SPDX bill of materials from
                                     # uv.lock (B-179)
              release-manifest.json  # version, git hash, slice
                                     # range, AI driver modes,
                                     # known limits (from the
                                     # limits file, one per line),
                                     # and the signing key's
                                     # fingerprint (B-179)

        Beside the ZIP land its detached Ed25519 signature
        (``<zip>.sig``) and the signer's public key (``<zip>.pub``),
        written by scripts/release_signing.py; verify with
        ``scripts/release_signing.py verify <zip>`` before
        unzipping. The top-level documents are read from the repo
        root — the release is only as honest as what is committed
        there, and the script refuses to build without them.

Excluded, always: .git, virtualenvs, caches, databases, runtime
data, and ANY .env file — a ZIP must never carry a secret (D-25).
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The B-179 signing + SBOM machinery lives in its own script so the
# verifier can be run without building anything; load it by path
# (scripts/ is not a package) exactly as the tests load this file.
_signing_spec = importlib.util.spec_from_file_location(
    "release_signing", Path(__file__).resolve().parent / "release_signing.py"
)
release_signing = importlib.util.module_from_spec(_signing_spec)
_signing_spec.loader.exec_module(release_signing)

EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "data", "releases", ".mypy_cache", ".ruff_cache", ".forge-lock"}
EXCLUDED_SUFFIXES = {".pyc", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".zip"}

TOP_LEVEL_DOCS = ("OPENAPI.json", "FRONTEND_HANDOFF.md", "CHANGELOG.md", "TEST_REPORT.md", "RESULTS.md")


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


def _zip_info(name: str, mode: int, mtime: float) -> zipfile.ZipInfo:
    """A ZipInfo that carries the source file's Unix permissions.

    writestr with a bare name writes archive-default 0644 for
    every entry — which lands demo.sh / run.sh / test.sh
    non-executable after unzip, so a cold reader's first command
    fails with Permission denied. The B-151 freeze drill found
    exactly that against the first ZIP-Final candidate (the
    earlier train ZIPs share the flaw; no earlier drill had
    unzipped one and tried to run the demo). Executable bits
    are part of the artifact: carry them.
    """
    info = zipfile.ZipInfo(name, date_time=time.localtime(mtime)[:6])
    info.create_system = 3  # Unix — external_attr holds the mode.
    info.external_attr = (mode & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


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

    # B-179: even the founding shape is signed and carries an SBOM.
    private_key, ephemeral = release_signing.load_private_key(ROOT)
    files = _tree_files()
    manifest = {"release": name, "files": {}}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = str(path.relative_to(ROOT))
            data = path.read_bytes()
            manifest["files"][rel] = hashlib.sha256(data).hexdigest()
            stat = path.stat()
            zf.writestr(_zip_info(rel, stat.st_mode, stat.st_mtime), data)
        zf.writestr(
            _zip_info("sbom.spdx.json", 0o100644, time.time()),
            release_signing.sbom_bytes(ROOT),
        )
        manifest["signing"] = {
            "algorithm": "Ed25519",
            "key_id": release_signing.key_fingerprint(private_key.public_key()),
            "ephemeral": ephemeral,
        }
        zf.writestr(
            _zip_info("manifest.json", 0o100644, time.time()),
            json.dumps(manifest, indent=2, sort_keys=True),
        )

    digest = _write_sidecar(zip_path)
    release_signing.sign_zip(zip_path, private_key)
    if ephemeral:
        print("WARNING: signed with an EPHEMERAL key (no GPA_RELEASE_* key configured).")
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
    entry_modes: dict[str, tuple[int, float]] = {}
    for path in _tree_files():
        rel = str(path.relative_to(ROOT))
        entries[f"gpa-backend/{rel}"] = path.read_bytes()
        stat = path.stat()
        entry_modes[f"gpa-backend/{rel}"] = (stat.st_mode, stat.st_mtime)
    for doc in TOP_LEVEL_DOCS:
        entries[doc] = (ROOT / doc).read_bytes()
        stat = (ROOT / doc).stat()
        entry_modes[doc] = (stat.st_mode, stat.st_mtime)

    # B-179: load the signing key BEFORE assembling entries — the
    # manifest inside the ZIP names the key's fingerprint, and the
    # detached signature over the finished ZIP is written after.
    private_key, ephemeral = release_signing.load_private_key(ROOT)
    manifest = {
        "version": version,
        "date": date,
        "git_hash": git_hash,
        "slice_range": f"B-001 → {slice_end}",
        "ai_driver_modes": ["live", "fixture", "mock"],
        "known_limits": known_limits,
        "sbom": "sbom.spdx.json",
        "signing": {
            "algorithm": "Ed25519",
            "key_id": release_signing.key_fingerprint(private_key.public_key()),
            "ephemeral": ephemeral,
        },
    }
    entries["release-manifest.json"] = json.dumps(
        manifest, indent=2, sort_keys=True
    ).encode("utf-8")
    entries["sbom.spdx.json"] = release_signing.sbom_bytes(ROOT)
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(entries.items())
    )
    entries["SHA256SUMS.txt"] = sums.encode("utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in sorted(entries):
            mode, mtime = entry_modes.get(name, (0o100644, time.time()))
            zf.writestr(_zip_info(name, mode, mtime), entries[name])

    digest = _write_sidecar(zip_path)
    signing = release_signing.sign_zip(zip_path, private_key)
    if ephemeral:
        print(
            "WARNING: signed with an EPHEMERAL key — no GPA_RELEASE_PRIVATE_KEY_B64 "
            "or GPA_RELEASE_KEY_FILE was configured. The manifest says so; a "
            "release for other people must be signed with the team's key."
        )
    print(f"Built {zip_path} ({zip_path.stat().st_size:,} bytes, {len(entries)} files)")
    print(f"sha256: {digest}")
    print(f"signing key: {signing['key_id']} (Ed25519, .sig + .pub beside the ZIP)")


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
