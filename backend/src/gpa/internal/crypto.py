"""Internal communications cryptography (roadmap §6A.4, B-163).

The wire profile every internal call crosses a process
boundary in:

* **Envelope encryption — AES-256-GCM** with a fresh random
  nonce per message. The payload (the only part that can carry
  user data) is sealed; the header (operation, caller, scope,
  nonce, timestamp) rides as authenticated data, so tampering
  with either half fails the tag check.
* **Signing — Ed25519** over the canonical bytes of the whole
  wire message, so a receiver can prove WHO sent a call before
  it spends a decryption on it, and prove its own results on
  the way back. An **HMAC-SHA256 profile** is provided for
  deployments that standardise on shared secrets instead of
  key pairs; the two never mix on one channel — the profile
  is part of the channel's configuration (B-164 binds
  identities to one or the other).

Keys: the AES key is 32 bytes, from configuration (base64 in
``GPA_INTERNAL__KEY``) or — the documented development
fallback, the same posture as the storage sealer's — derived
from the app secret with a domain label. Production
deployments set the real key; B-169's key management wraps
these primitives in rotation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gpa.errors import AppError

# Wire-format version, carried in every framed message so a
# future profile change is a negotiation, not a flag day.
WIRE_VERSION = 1

# The domain label for the development key derivation: the
# internal key is never the app secret itself, and never the
# storage sealer's key either — one secret, three derivations,
# three separate blast radii.
_KEY_DOMAIN = b"gpa-internal-comms-key-v1"

_NONCE_BYTES = 12  # AES-GCM's standard nonce size.


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def load_internal_key(settings) -> bytes:
    """The 32-byte comms key for a settings object.

    ``internal_key_b64`` (env ``GPA_INTERNAL__KEY_B64``) when
    set — the production road. Otherwise derived from the app
    secret with the domain label above, so development and
    tests run encrypted too, with no extra setup."""
    configured = getattr(settings, "internal_key_b64", "") or ""
    if configured:
        try:
            key = _unb64(configured)
        except Exception as exc:  # a malformed key is a config error
            raise AppError(
                "internal_bad_key",
                detail="GPA_INTERNAL__KEY_B64 is not valid base64.",
            ) from exc
        if len(key) != 32:
            raise AppError(
                "internal_bad_key",
                detail="GPA_INTERNAL__KEY_B64 must decode to 32 bytes (AES-256).",
            )
        return key
    return hashlib.sha256(_KEY_DOMAIN + b":" + settings.secret_key.encode()).digest()


def generate_key_b64() -> str:
    """A fresh random comms key, base64 — for installers/tools
    to print into an environment file (never into the repo)."""
    return _b64(os.urandom(32))


def canonical_header_bytes(message: dict) -> bytes:
    """The signed/sealed-as-AAD view of a wire message: every
    field except the signature itself, canonical JSON (sorted
    keys, tight separators) so both ends compute the same
    bytes."""
    signed_view = {k: v for k, v in message.items() if k != "signature"}
    return json.dumps(signed_view, sort_keys=True, separators=(",", ":")).encode()


def seal(key: bytes, plaintext: bytes, *, aad: bytes) -> str:
    """AES-256-GCM seal: returns base64(nonce ‖ ciphertext)."""
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, plaintext, aad)
    return _b64(nonce + sealed)


def open_sealed(key: bytes, sealed_b64: str, *, aad: bytes) -> bytes:
    """Open a seal; any tamper — header or payload — raises
    ``internal_tampered``. Callers never see a cryptography
    exception type."""
    try:
        blob = _unb64(sealed_b64)
        nonce, sealed = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, sealed, aad)
    except (InvalidTag, ValueError) as exc:
        raise AppError(
            "internal_tampered",
            detail="An internal message failed its integrity check.",
        ) from exc


# ---------------------------------------------------------------------------
# Ed25519 identity keys (per-module identities land in B-164;
# these are the primitives it builds on).
# ---------------------------------------------------------------------------


def generate_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def signing_key_from_seed(seed_b64: str) -> Ed25519PrivateKey:
    """A signing key from a configured 32-byte seed (base64) —
    the deterministic road, so a module's identity survives
    restarts without a key file."""
    seed = _unb64(seed_b64)
    if len(seed) != 32:
        raise AppError(
            "internal_bad_key",
            detail="An Ed25519 seed must decode to 32 bytes.",
        )
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return _b64(raw)


def key_id_of(public_b64: str) -> str:
    """A public key's printable id (B-164): the first 16 hex
    characters of its SHA-256 — short enough for logs and
    audit lines, long enough that collisions are not a
    practical concern at a deployment's key count."""
    raw = base64.b64decode(public_b64.encode("ascii"))
    return hashlib.sha256(raw).hexdigest()[:16]


def seed_b64(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return _b64(raw)


def sign(private_key: Ed25519PrivateKey, data: bytes) -> str:
    return _b64(private_key.sign(data))


def verify(public_b64: str, signature_b64: str, data: bytes) -> None:
    """Verify an Ed25519 signature; failure raises
    ``internal_bad_signature`` (B-164's replay/identity layer
    builds its refusals on this code too)."""
    try:
        public = Ed25519PublicKey.from_public_bytes(_unb64(public_b64))
        public.verify(_unb64(signature_b64), data)
    except (InvalidSignature, ValueError) as exc:
        raise AppError(
            "internal_bad_signature",
            detail="An internal message's signature did not verify.",
        ) from exc


# ---------------------------------------------------------------------------
# HMAC profile (shared-secret channels).
# ---------------------------------------------------------------------------


def sign_hmac(secret: bytes, data: bytes) -> str:
    return _b64(hmac.new(secret, data, hashlib.sha256).digest())


def verify_hmac(secret: bytes, signature_b64: str, data: bytes) -> None:
    expected = hmac.new(secret, data, hashlib.sha256).digest()
    try:
        presented = _unb64(signature_b64)
    except ValueError as exc:
        raise AppError(
            "internal_bad_signature",
            detail="An internal message's signature did not verify.",
        ) from exc
    if not hmac.compare_digest(expected, presented):
        raise AppError(
            "internal_bad_signature",
            detail="An internal message's signature did not verify.",
        )


__all__ = [
    "WIRE_VERSION",
    "canonical_header_bytes",
    "generate_key_b64",
    "generate_signing_key",
    "key_id_of",
    "load_internal_key",
    "open_sealed",
    "public_key_b64",
    "seal",
    "seed_b64",
    "sign",
    "sign_hmac",
    "signing_key_from_seed",
    "verify",
    "verify_hmac",
]
