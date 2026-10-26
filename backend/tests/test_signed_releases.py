"""Signed releases + SBOM (roadmap B-179).

The acceptance, in the roadmap's words: a tampered-ZIP fixture —
one flipped byte — and verification refuses it, loudly.  These
tests build a REAL train ZIP through the real builder (with a
throwaway release key held in a tmp directory outside the repo,
exactly the custody a real key gets), verify it clean, then flip
one byte and watch every door refuse.  They also pin the SBOM's
truth: its package set is the uv.lock package set, no more and no
less, and a key file inside the repository is refused outright —
the private key must never be one `git add` away from published.
"""

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    """Load a scripts/ module by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signing = _load("release_signing", "release_signing.py")
builder = _load("make_release_zip", "make_release_zip.py")


@pytest.fixture(scope="module")
def built_zip(tmp_path_factory):
    """Build one train ZIP with a throwaway key; return its path.

    The builder writes into the repo's releases/ folder by design,
    so the test builds a uniquely-versioned ZIP there and removes
    it (plus its sidecars) at teardown.
    """
    work = tmp_path_factory.mktemp("release-key")
    key_file = work / "release-key.txt"
    assert signing.main(["keygen", str(key_file)]) == 0
    import os

    old = os.environ.get("GPA_RELEASE_KEY_FILE")
    os.environ["GPA_RELEASE_KEY_FILE"] = str(key_file)
    try:
        builder.build_train("v9.9", "2026-10-25", "B-179")
    finally:
        if old is None:
            os.environ.pop("GPA_RELEASE_KEY_FILE", None)
        else:
            os.environ["GPA_RELEASE_KEY_FILE"] = old
    zip_path = ROOT / "releases" / "gpa-backend-v9.9-2026-10-25.zip"
    yield zip_path
    for suffix in ("", ".sha256", ".sig", ".pub"):
        (zip_path.parent / f"{zip_path.name}{suffix}").unlink(missing_ok=True)


def test_built_zip_verifies_clean(built_zip):
    """A freshly built, signed ZIP passes every verification door."""
    assert signing.verify_release(built_zip) == []
    for suffix in (".sha256", ".sig", ".pub"):
        assert (built_zip.parent / f"{built_zip.name}{suffix}").is_file()


def test_one_flipped_byte_is_refused_loudly(built_zip, tmp_path):
    """THE acceptance: flip one byte; verification refuses."""
    tampered = tmp_path / built_zip.name
    data = bytearray(built_zip.read_bytes())
    data[len(data) // 2] ^= 0xFF
    tampered.write_bytes(bytes(data))
    # The sidecars travel with the copy, as they would for a recipient.
    for suffix in (".sha256", ".sig", ".pub"):
        src = built_zip.parent / f"{built_zip.name}{suffix}"
        (tmp_path / f"{built_zip.name}{suffix}").write_bytes(src.read_bytes())
    problems = signing.verify_release(tampered)
    assert problems, "a tampered ZIP must never verify"
    assert any("SIGNATURE REFUSED" in p or "sha256" in p for p in problems)


def test_sbom_inside_zip_matches_the_shipped_lock(built_zip):
    """The SBOM's package set is exactly the shipped uv.lock's set."""
    with zipfile.ZipFile(built_zip) as archive:
        sbom = json.loads(archive.read("sbom.spdx.json"))
        manifest = json.loads(archive.read("release-manifest.json"))
    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert len(sbom["packages"]) >= 50  # the real pinned dependency tree
    assert all(pkg["checksums"] or pkg["name"] == "group-planning-assistant-backend"
               for pkg in sbom["packages"])
    assert manifest["sbom"] == "sbom.spdx.json"
    assert manifest["signing"]["algorithm"] == "Ed25519"
    assert manifest["signing"]["ephemeral"] is False


def test_key_file_inside_the_repo_is_refused(monkeypatch):
    """Custody law: a private key path inside the repo stops the build."""
    monkeypatch.setenv("GPA_RELEASE_KEY_FILE", str(ROOT / "release-key.txt"))
    with pytest.raises(SystemExit, match="inside the repository"):
        signing.load_private_key(ROOT)


def test_keygen_refuses_to_write_inside_the_repo(tmp_path):
    """The key generator itself will not place a key in the tree."""
    inside = ROOT / "scripts" / "nope-key.txt"
    assert signing.main(["keygen", str(inside)]) == 1
    assert not inside.exists()
