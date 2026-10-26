#!/usr/bin/env python3
"""Signed releases + SBOM (roadmap B-179, standard §6A.7 / D-33).

What this file is for
---------------------
A release ZIP is how this backend reaches Geoffrey, Henry, and any
future maintainer.  Before anyone unzips one they should be able to
answer two questions without trusting the sender's say-so:

1. *Is this exactly the ZIP the team built?*  The ZIP's bytes are
   signed with the team's Ed25519 release key.  The detached
   signature lives beside the ZIP (``<zip>.sig``), the signer's
   public key beside that (``<zip>.pub``), and the long-standing
   ``<zip>.sha256`` sidecar stays as the plain checksum.  Any one
   flipped byte in the ZIP makes the signature check fail, loudly.
2. *What is inside it, dependency-wise?*  Every train ZIP carries
   ``sbom.spdx.json`` — an SPDX 2.3 bill of materials generated
   from the committed ``uv.lock``: every pinned package, its exact
   version, and the SHA-256 hash uv recorded for its distribution
   files.  ``uv sync`` verifies those same hashes at install time
   (that is what a lockfile with hashes *is*), so the SBOM, the
   lockfile, and the installed environment are three readings of
   one pinned truth.

Key custody (the part that must never be improvised)
----------------------------------------------------
The release PRIVATE key never lives in this repository — not in a
file, not in ``.env`` (which is never committed either), not in a
test fixture.  A build takes it from exactly one of:

* ``GPA_RELEASE_PRIVATE_KEY_B64`` — the raw 32-byte Ed25519 seed,
  base64-encoded, in the builder's environment; or
* ``GPA_RELEASE_KEY_FILE`` — a path OUTSIDE the repo holding that
  same base64 text (mode 0600 is the operator's discipline).

``keygen`` writes such a key file (refusing to write inside the
repo) and prints the public key + its fingerprint.  The
fingerprint — the first 16 hex chars of the public key's SHA-256 —
is the out-of-band check: a recipient compares the fingerprint on
the ``.pub`` they received against the fingerprint the team
published somewhere they already trust.  A signature checked
against a public key that arrived in the same envelope as the ZIP
proves the ZIP is untampered *since signing*; the fingerprint is
what ties that key to the team.

If no key is configured, a train build does NOT silently ship
unsigned: it generates an EPHEMERAL key, signs with it, and stamps
``"signing": {"ephemeral": true, ...}`` into the release manifest
with a loud warning on the build output.  An ephemeral signature
still proves the ZIP matches its own manifest and sums, and the
ephemeral stamp tells every reader the truth — this build was not
signed with the team's standing key.  Release builds for other
people must set a real key; the B-179 gate uses a throwaway key
held outside the repo, exactly like a real one.

Verification (``verify_release`` / the CLI below)
-------------------------------------------------
Checks, in order, each refusing in plain words:

* the ``.sha256`` sidecar matches the ZIP's bytes;
* the Ed25519 ``.sig`` verifies over the ZIP's bytes under the
  ``.pub`` key (or a caller-supplied public key);
* inside the ZIP, ``SHA256SUMS.txt`` matches every listed entry
  byte-for-byte, and no unlisted entry hides in the archive;
* ``sbom.spdx.json`` parses, and its package set matches the
  ``uv.lock`` shipped inside the ZIP (``gpa-backend/uv.lock``);
* ``release-manifest.json`` names the same signing fingerprint
  as the public key that verified the signature.

One flipped byte anywhere fails step one or two before the
archive is even opened — that is the slice's acceptance test.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

# tomllib is stdlib since 3.11 — the lockfile is TOML, and parsing
# it with the real parser (never regexes) is what keeps the SBOM
# honest when uv changes its formatting details.
import tomllib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ROOT = Path(__file__).resolve().parent.parent

# SPDX identifiers this generator emits.  The document format is
# SPDX JSON 2.3; the package entries carry no licence conclusion —
# uv.lock records versions and hashes, not licences, and inventing
# a licence per package would be a claim without evidence (D-41).
SPDX_VERSION = "SPDX-2.3"
SBOM_NAME = "sbom.spdx.json"


def key_fingerprint(public_key: Ed25519PublicKey) -> str:
    """The short fingerprint a human compares out of band.

    Sixteen hex chars of the SHA-256 of the raw public key bytes:
    short enough to read over a call, long enough that a second
    key with the same fingerprint is not a practical forgery.
    """
    raw = public_key.public_bytes_raw()
    return hashlib.sha256(raw).hexdigest()[:16]


def load_private_key(root: Path) -> tuple[Ed25519PrivateKey, bool]:
    """Load the release key from the environment, or go ephemeral.

    Returns ``(key, ephemeral)``.  Refuses — in plain words — a
    configured-but-unreadable key and any key file inside the
    repository: a half-configured release build must stop, never
    quietly fall back to an ephemeral key and ship.
    """
    seed_b64 = os.environ.get("GPA_RELEASE_PRIVATE_KEY_B64", "").strip()
    key_file = os.environ.get("GPA_RELEASE_KEY_FILE", "").strip()
    if key_file:
        path = Path(key_file).expanduser()
        # The private key must never rest inside the repo tree:
        # one careless `git add -A` would publish it forever.
        if path.resolve().is_relative_to(root.resolve()):
            raise SystemExit(
                "Refusing to build: GPA_RELEASE_KEY_FILE points inside "
                "the repository. The release private key must live "
                "outside the repo, always."
            )
        if not path.is_file():
            raise SystemExit(
                f"Refusing to build: GPA_RELEASE_KEY_FILE={path} does "
                "not exist. Fix the path or unset it."
            )
        seed_b64 = path.read_text(encoding="utf-8").strip()
    if seed_b64:
        try:
            seed = base64.b64decode(seed_b64, validate=True)
            return Ed25519PrivateKey.from_private_bytes(seed), False
        except Exception as exc:  # a bad key is a stop, in plain words
            raise SystemExit(
                "Refusing to build: the configured release key is not "
                f"a base64 Ed25519 seed ({exc}). Regenerate it with "
                "scripts/release_signing.py keygen."
            ) from exc
    # No key configured: an ephemeral keypair for this one build.
    # The caller stamps the manifest ephemeral=true; nothing here
    # pretends this is the team's standing key.
    return Ed25519PrivateKey.generate(), True


def sign_zip(zip_path: Path, private_key: Ed25519PrivateKey) -> dict[str, str]:
    """Sign a built ZIP and write its ``.sig`` / ``.pub`` companions.

    The signature covers the ZIP file's exact bytes — that is the
    whole tamper story: change one byte and verification fails
    before the archive is opened.  Returns the manifest-ready
    signing facts (algorithm, fingerprint, ephemeral is added by
    the caller, which is the one who knows).
    """
    public = private_key.public_key()
    signature = private_key.sign(zip_path.read_bytes())
    (zip_path.parent / f"{zip_path.name}.sig").write_text(
        base64.b64encode(signature).decode("ascii") + "\n", encoding="utf-8"
    )
    (zip_path.parent / f"{zip_path.name}.pub").write_text(
        base64.b64encode(public.public_bytes_raw()).decode("ascii") + "\n",
        encoding="utf-8",
    )
    return {"algorithm": "Ed25519", "key_id": key_fingerprint(public)}


def build_sbom(root: Path) -> dict:
    """Build the SPDX document from the committed ``uv.lock``.

    Every ``[[package]]`` in the lock becomes one SPDX package:
    name, exact version, the registry it came from, and one
    checksum per distribution file uv pinned (sdist + wheels).
    The project's own package is included and marked as the
    document's root via DESCRIBES, so a reader can tell the
    product from its dependencies.
    """
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages: list[dict] = []
    for entry in lock.get("package", []):
        name = str(entry["name"])
        checksums: list[dict] = []
        seen: set[str] = set()
        artifacts: list[dict] = []
        if isinstance(entry.get("sdist"), dict):
            artifacts.append(entry["sdist"])
        artifacts.extend(entry.get("wheels") or [])
        for artifact in artifacts:
            digest = str(artifact.get("hash", ""))
            if digest.startswith("sha256:") and digest not in seen:
                seen.add(digest)
                checksums.append(
                    {"algorithm": "SHA256", "checksumValue": digest[7:]}
                )
        source = entry.get("source") or {}
        download = str(
            source.get("registry") or source.get("editable") or "NOASSERTION"
        )
        packages.append(
            {
                "name": name,
                "SPDXID": f"SPDXRef-Package-{_spdx_id(name)}",
                "versionInfo": str(entry["version"]),
                "downloadLocation": download,
                "filesAnalyzed": False,
                "checksums": checksums,
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            f"pkg:pypi/{name}@{entry['version']}"
                        ),
                    }
                ],
            }
        )
    packages.sort(key=lambda pkg: pkg["name"])
    return {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "gpa-backend-sbom",
        "documentNamespace": "https://example.invalid/gpa-backend/sbom",
        "creationInfo": {"creators": ["Tool: gpa-release-signing"], "created": "2026-01-01T00:00:00Z"},
        "packages": packages,
        "documentDescribes": ["SPDXRef-Package-group-planning-assistant-backend"],
    }


def _spdx_id(name: str) -> str:
    """A legal SPDX id fragment for a package name.

    SPDX ids allow letters, digits, ``.`` and ``-`` only; PyPI
    names may carry ``_``.  Normalising here keeps the document
    valid without renaming the package itself (``name`` stays
    verbatim in its own field).
    """
    return name.replace("_", "-")


def sbom_bytes(root: Path) -> bytes:
    """The SBOM as the exact bytes that ship inside the ZIP.

    ``created`` is pinned to a fixed stamp so the SBOM — and
    therefore SHA256SUMS and the ZIP — is reproducible from the
    same lockfile: two builds of one tree carry identical bills
    of materials, which is what lets a hash comparison mean
    anything at all.
    """
    return (
        json.dumps(build_sbom(root), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def lock_package_names(root: Path) -> set[tuple[str, str]]:
    """The (name, version) set a lockfile pins — the SBOM's truth."""
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    return {
        (str(entry["name"]), str(entry["version"]))
        for entry in lock.get("package", [])
    }


def verify_release(
    zip_path: Path, public_key_b64: str | None = None
) -> list[str]:
    """Verify a release ZIP end to end; return problem sentences.

    An empty list means verified.  Every check from the module
    docstring runs and ALL failures are reported (not just the
    first), each in plain words naming what refused.  This
    function never extracts anything — it reads the archive in
    place, so a hostile ZIP is inspected, never trusted.
    """
    problems: list[str] = []
    if not zip_path.is_file():
        return [f"{zip_path} does not exist."]
    data = zip_path.read_bytes()

    # 1. The plain checksum sidecar.
    sidecar = zip_path.parent / f"{zip_path.name}.sha256"
    if not sidecar.is_file():
        problems.append(f"missing checksum sidecar {sidecar.name}.")
    else:
        expected = sidecar.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(data).hexdigest()
        if expected != actual:
            problems.append(
                "sha256 sidecar does not match the ZIP bytes "
                f"(sidecar {expected[:12]}…, actual {actual[:12]}…) — "
                "the file changed after it was built."
            )

    # 2. The signature over the ZIP's exact bytes.
    pub_b64 = public_key_b64
    if pub_b64 is None:
        pub_path = zip_path.parent / f"{zip_path.name}.pub"
        if pub_path.is_file():
            pub_b64 = pub_path.read_text(encoding="utf-8").strip()
        else:
            problems.append(f"missing public key file {pub_path.name}.")
    sig_path = zip_path.parent / f"{zip_path.name}.sig"
    public: Ed25519PublicKey | None = None
    if pub_b64:
        try:
            public = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(pub_b64, validate=True)
            )
        except Exception as exc:
            problems.append(f"the public key is unreadable ({exc}).")
    if not sig_path.is_file():
        problems.append(f"missing signature file {sig_path.name}.")
    elif public is not None:
        try:
            signature = base64.b64decode(
                sig_path.read_text(encoding="utf-8").strip(), validate=True
            )
            public.verify(signature, data)
        except InvalidSignature:
            problems.append(
                "SIGNATURE REFUSED: the Ed25519 signature does not "
                "verify over this ZIP's bytes — the ZIP was tampered "
                "with after signing, or this is not the team's key."
            )
        except Exception as exc:
            problems.append(f"the signature file is unreadable ({exc}).")

    if problems:
        # A failed outer check ends the inspection: opening an
        # archive whose bytes already refused would be theatre.
        return problems

    # 3–5. Inside the archive: sums, SBOM vs shipped lock, manifest.
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        return ["the file is not a readable ZIP archive."]
    with archive:
        names = set(archive.namelist())
        for needed in ("SHA256SUMS.txt", "release-manifest.json", SBOM_NAME):
            if needed not in names:
                problems.append(f"the ZIP is missing {needed}.")
        if problems:
            return problems
        listed: dict[str, str] = {}
        for line in archive.read("SHA256SUMS.txt").decode("utf-8").splitlines():
            if line.strip():
                digest, _, entry_name = line.partition("  ")
                listed[entry_name.strip()] = digest.strip()
        for entry_name, digest in listed.items():
            if entry_name not in names:
                problems.append(f"SHA256SUMS lists {entry_name}, absent from the ZIP.")
            elif hashlib.sha256(archive.read(entry_name)).hexdigest() != digest:
                problems.append(
                    f"SHA256SUMS mismatch for {entry_name} — its bytes "
                    "changed after the sums were written."
                )
        unlisted = names - set(listed) - {"SHA256SUMS.txt"}
        if unlisted:
            problems.append(
                "entries in the ZIP that SHA256SUMS does not list: "
                + ", ".join(sorted(unlisted))
                + "."
            )
        # The SBOM must describe the lockfile that actually ships.
        try:
            sbom = json.loads(archive.read(SBOM_NAME).decode("utf-8"))
            sbom_set = {
                (str(pkg["name"]), str(pkg["versionInfo"]))
                for pkg in sbom.get("packages", [])
            }
        except Exception as exc:
            problems.append(f"the SBOM does not parse ({exc}).")
            sbom_set = set()
        if "gpa-backend/uv.lock" in names:
            shipped_lock = tomllib.loads(
                archive.read("gpa-backend/uv.lock").decode("utf-8")
            )
            lock_set = {
                (str(e["name"]), str(e["version"]))
                for e in shipped_lock.get("package", [])
            }
            if sbom_set != lock_set:
                problems.append(
                    "the SBOM's package set does not match the shipped "
                    "uv.lock — the bill of materials is not this "
                    "release's."
                )
        # The manifest's signing fingerprint must be the key that
        # actually verified — no swapping keys between papers.
        if public is not None:
            manifest = json.loads(
                archive.read("release-manifest.json").decode("utf-8")
            )
            signing = manifest.get("signing") or {}
            if signing.get("key_id") != key_fingerprint(public):
                problems.append(
                    "the manifest's signing key_id does not match the "
                    "public key that verified the signature."
                )
            # B-206: the MCP tool descriptions are manifest facts.
            # Recompute each description's hash and the digest over
            # the whole list (the registry's canonical form: compact
            # JSON, sorted keys) — a swapped description inside the
            # manifest fails here even before anyone compares the
            # manifest against a served server.
            mcp_tools = manifest.get("mcp_tools")
            if mcp_tools is not None:
                for entry in mcp_tools:
                    digest = hashlib.sha256(
                        str(entry.get("description", "")).encode("utf-8")
                    ).hexdigest()
                    if digest != entry.get("description_sha256"):
                        problems.append(
                            f"the manifest's MCP tool '{entry.get('name')}' "
                            "description does not match its recorded "
                            "SHA-256 — a description was swapped after "
                            "the manifest was written."
                        )
                canonical = json.dumps(
                    mcp_tools, sort_keys=True, separators=(",", ":")
                )
                whole = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                if whole != manifest.get("mcp_tools_digest"):
                    problems.append(
                        "the manifest's mcp_tools_digest does not match "
                        "its MCP tool list — the catalogue changed after "
                        "the digest was stamped."
                    )
    return problems


def _keygen(argv: list[str]) -> int:
    """Write a new release private key OUTSIDE the repo; print its public half."""
    if len(argv) != 1:
        print("usage: release_signing.py keygen <key-file-outside-repo>")
        return 2
    path = Path(argv[0]).expanduser()
    if path.resolve().is_relative_to(ROOT.resolve()):
        print("Refused: the private key must live outside the repository.")
        return 1
    if path.exists():
        print(f"Refused: {path} already exists — a key is never overwritten.")
        return 1
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        base64.b64encode(key.private_bytes_raw()).decode("ascii") + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    public = key.public_key()
    print(f"private key written to {path} (mode 0600, keep it secret)")
    print(
        "public key (base64): "
        + base64.b64encode(public.public_bytes_raw()).decode("ascii")
    )
    print(f"fingerprint: {key_fingerprint(public)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI: ``keygen <file>`` or ``verify <zip> [--pub <base64>]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["keygen"]:
        return _keygen(args[1:])
    if args[:1] == ["verify"] and len(args) >= 2:
        pub: str | None = None
        if "--pub" in args:
            pub = args[args.index("--pub") + 1]
        problems = verify_release(Path(args[1]), public_key_b64=pub)
        if problems:
            print("RELEASE VERIFICATION REFUSED:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"verified: {args[1]} is untampered and matches its SBOM.")
        return 0
    print("usage: release_signing.py keygen <file> | verify <zip> [--pub <b64>]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
