"""Envelope sealing for secrets at rest (roadmap B-127).

Integration configs carry other systems' tokens and keys.
They must exist in the database in a form the running app
can use, but a database file on its own — a backup, a leak,
a grep — must yield NOTHING usable. That is the envelope
this module provides:

    seal(plaintext) -> "v1:<nonce>:<ciphertext>:<tag>"
    open_sealed(sealed) -> plaintext, or ValueError

Construction, from the standard library alone (the D-46
discipline — the same reason the JWT is hand-rolled in
security.py): the master key is SHA-256 of the operator's
integration key (``GPA_INTEGRATION_KEY``, falling back to
the app secret — an environment value, never a database
value). The ciphertext is the plaintext XORed with an
HMAC-SHA256 keystream keyed by (master key, nonce, block
counter); the tag is an HMAC over (nonce, ciphertext) with
a domain-separated label, verified in constant time before
any decryption — encrypt-then-MAC, fail closed.

What this does and does not claim, plainly: it makes stored
secrets unreadable WITHOUT the environment key, which is
exactly the B-127 acceptance (a config-secrets grep over
the DB file finds no plaintext). It is not a KMS; a
deployment with one swaps this module's two functions
behind the same call sites, and the stored format's "v1"
prefix is the version marker that swap will read.
"""

import base64
import hashlib
import hmac
import secrets

from gpa.config import Settings

_VERSION = "v1"
_NONCE_BYTES = 16


def _master_key(settings: Settings) -> bytes:
    """The envelope key: SHA-256 of the operator's key
    material. Environment-only — settings read the
    environment; nothing here touches the database."""
    material = settings.integration_key or settings.secret_key
    return hashlib.sha256(material.encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 in counter mode, domain-labelled so these
    outputs can never be confused with the tag's."""
    blocks = []
    produced = 0
    counter = 0
    while produced < length:
        block = hmac.new(
            key, b"gpa-seal-stream:" + nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:length]


def _tag(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(
        key, b"gpa-seal-tag:" + nonce + ciphertext, hashlib.sha256
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def seal(settings: Settings, plaintext: str) -> str:
    """Seal one string. A fresh nonce per call: sealing the
    same secret twice yields different stored text."""
    key = _master_key(settings)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    data = plaintext.encode("utf-8")
    stream = _keystream(key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    return f"{_VERSION}:{_b64(nonce)}:{_b64(ciphertext)}:{_b64(_tag(key, nonce, ciphertext))}"


def open_sealed(settings: Settings, sealed: str) -> str:
    """Open one sealed string. Any tampering — a flipped
    character anywhere — fails the tag check and raises
    ValueError; a caller never receives partially-true text."""
    try:
        version, nonce_b64, ct_b64, tag_b64 = sealed.split(":")
        if version != _VERSION:
            raise ValueError("unknown seal version")
        nonce, ciphertext, tag = _unb64(nonce_b64), _unb64(ct_b64), _unb64(tag_b64)
    except ValueError:
        raise ValueError("That sealed value could not be read.") from None
    key = _master_key(settings)
    if not hmac.compare_digest(_tag(key, nonce, ciphertext), tag):
        raise ValueError("That sealed value could not be read.")
    stream = _keystream(key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream)).decode("utf-8")
