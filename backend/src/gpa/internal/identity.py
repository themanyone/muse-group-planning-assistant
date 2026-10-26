"""Service identities (roadmap §6A.4, B-164).

Every module and worker holds its OWN identity: an Ed25519
key pair whose public half is known to the doors it calls,
named by the module it speaks for, with a printable **key
id** (a short fingerprint of the public key) that appears in
audit lines and refusal messages — so "who said this?" always
has a checkable answer, never just a claim in a header.

Least privilege is not re-invented here: an identity's scopes
ARE the registry's caller grants (B-162) — this module binds
names to keys; the registry binds names to scopes; the
transport binds a message to the key that signed it and
refuses any message whose envelope caller is not the signing
identity. Impersonation therefore fails at the math, not at
a policy someone remembered to check.

Where identities come from, in order:

1. **An identities directory** (production): one file pair
   per identity — ``<name>.seed`` (the Ed25519 seed, mode
   0600, never in the repo) and ``<name>.pub`` (base64 public
   key). Rotation overlap rides in ``<name>.pub.prev``: while
   it exists, BOTH public keys verify for that name.
2. **Derivation from the app secret** (development, the
   documented fallback posture used across this codebase):
   each known caller's seed is HMAC-derived from the secret
   with a domain label, so every module still holds a
   DISTINCT key and the whole machinery runs identically —
   only the key custody differs.

The known caller names are not duplicated here: they are the
registry's grant table, read live.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gpa.errors import AppError
from gpa.internal import crypto, registry


def key_id_of(public_b64: str) -> str:
    """The printable key id — see `crypto.key_id_of` (kept
    importable from here, identity's home)."""
    return crypto.key_id_of(public_b64)


# Domain label for the derived development identities: one
# secret, one derivation per purpose, separate blast radii —
# the same rule the comms key derivation follows (B-163).
_SEED_DOMAIN = b"gpa-module-identity-v1:"


@dataclass
class Identity:
    """One module's own identity: its name, its signing key,
    and the key id its messages are known by."""

    name: str
    private_key: Ed25519PrivateKey

    @property
    def public_b64(self) -> str:
        return crypto.public_key_b64(self.private_key)

    @property
    def key_id(self) -> str:
        return key_id_of(self.public_b64)

    def scopes(self) -> frozenset[str]:
        """This identity's least-privilege scopes — the
        registry's grants for its name (B-162/B-164 mirror of
        B-014's external scope law)."""
        return registry.grants_for(self.name)


@dataclass
class IdentityStore:
    """What a door knows: for each identity name, the public
    keys currently accepted for it (one, or two mid-rotation),
    and — for names this process itself speaks as — the own
    signing identity."""

    accepted: dict[str, list[str]] = field(default_factory=dict)
    own: dict[str, Identity] = field(default_factory=dict)

    def public_keys_for(self, name: str) -> list[str]:
        return list(self.accepted.get(name, []))

    def channel(
        self,
        *,
        key: bytes,
        as_name: str | None = None,
    ):
        """Build a transport Channel speaking as ``as_name``
        (a client) or verifying-only (a server: as_name None
        still signs RESULTS — a server speaks as its own
        identity, so servers pass their own name too)."""
        from gpa.internal.transport import Channel

        signing_key = None
        if as_name is not None:
            identity = self.own.get(as_name)
            if identity is None:
                raise AppError(
                    "internal_bad_key",
                    detail=f"No own identity held for '{as_name}'.",
                )
            signing_key = identity.private_key
        public_keys = {
            name: keys[0] if len(keys) == 1 else keys
            for name, keys in self.accepted.items()
        }
        return Channel(
            key=key,
            signer_id=as_name or "",
            signing_key=signing_key,
            public_keys=public_keys,
        )


def known_caller_names() -> list[str]:
    """Every identity name the system knows: the registry's
    granted callers, plus the composition identities."""
    names = set(registry.known_callers())
    names |= {"core", "worker", "arq-worker"}
    return sorted(names)


def derived_identity(name: str, secret_key: str) -> Identity:
    """The development identity for a name: an Ed25519 key
    HMAC-derived from the app secret. Distinct per name,
    stable across restarts, and worthless outside this
    deployment's secret — the honest dev posture."""
    seed = hmac.new(
        secret_key.encode(), _SEED_DOMAIN + name.encode(), hashlib.sha256
    ).digest()
    return Identity(
        name=name,
        private_key=Ed25519PrivateKey.from_private_bytes(seed),
    )


def load_store(settings) -> IdentityStore:
    """The identity store for a settings object.

    With ``internal_identities_dir`` set, identities are read
    from that directory (own seed + accepted publics, with
    rotation overlap). Without it, every known caller gets a
    derived identity and accepts its own public key — the
    full machinery, development custody."""
    directory = getattr(settings, "internal_identities_dir", "") or ""
    store = IdentityStore()
    if directory:
        root = Path(directory)
        for seed_file in sorted(root.glob("*.seed")):
            name = seed_file.stem
            private = crypto.signing_key_from_seed(
                seed_file.read_text(encoding="utf-8").strip()
            )
            store.own[name] = Identity(name=name, private_key=private)
        for pub_file in sorted(root.glob("*.pub")):
            name = pub_file.stem
            keys = [pub_file.read_text(encoding="utf-8").strip()]
            prev = root / f"{name}.pub.prev"
            if prev.exists():
                # Rotation overlap: the previous key still
                # verifies until the rotation completes.
                keys.append(prev.read_text(encoding="utf-8").strip())
            store.accepted[name] = keys
        # Own publics are always accepted for their own name
        # (a process must believe its own modules' doors).
        for name, identity in store.own.items():
            accepted = store.accepted.setdefault(name, [])
            if identity.public_b64 not in accepted:
                accepted.insert(0, identity.public_b64)
        return store
    for name in known_caller_names():
        identity = derived_identity(name, settings.secret_key)
        store.own[name] = identity
        store.accepted[name] = [identity.public_b64]
    return store


__all__ = [
    "Identity",
    "IdentityStore",
    "derived_identity",
    "key_id_of",
    "known_caller_names",
    "load_store",
]
