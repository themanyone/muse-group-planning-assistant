"""The encrypted storage driver (roadmap B-168).

Wraps any B-007 driver and seals every object at rest:

* each object gets a fresh random **per-file key**;
* the file key is **wrapped** (AES-256-GCM) under the
  deployment's master key (B-167's key hierarchy — env-held,
  never in the database) and rides in a small header inside
  the object itself;
* the object's bytes are AES-256-GCM under the file key,
  bound by authenticated data to the object's own content
  key, so a sealed object replayed under another name does
  not open.

The B-007 contract survives intact: keys remain the SHA-256
content addresses callers have always held, and identical
uploads still deduplicate to one object (a re-put replaces
the object with a fresh seal of the same bytes under the
same key, and every reader opens whichever seal is there).
One deliberate deviation from the roadmap's letter, recorded
in the memory map: file keys wrap under the MASTER key, not
the group key — the content-addressed store is shared across
groups by design (a file two groups uploaded is ONE object;
B-143's erasure law keeps it while either group lives), and
a group-wrapped key would make that object unreadable to
its other owners. The envelope substance — per-file keys,
wrapped, AES-256-GCM — is unchanged.

Objects written before this driver (no magic header) read
back as the legacy plaintext they are: turning the driver on
strands nothing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from gpa.internal import crypto
from gpa.storage.base import StorageBackend, validate_key

_MAGIC = b"GPAE1"
_FILE_KEY_AAD = b"gpa-file-key-v1"


def _object_aad(key: str) -> bytes:
    return f"gpa-object:{key}".encode()


class EncryptedStorage:
    """A B-007 driver that seals everything it stores."""

    def __init__(self, inner: StorageBackend, settings) -> None:
        self.inner = inner
        self._settings = settings

    def _master(self) -> bytes:
        from gpa.crypto import fields

        return fields.master_key(self._settings)

    def put_bytes(self, data: bytes, extension: str = "") -> str:
        ext = extension.lower().lstrip(".")[:8]
        key = hashlib.sha256(data).hexdigest() + (f".{ext}" if ext else "")
        file_key = os.urandom(32)
        wrapped = crypto.seal(self._master(), file_key, aad=_FILE_KEY_AAD)
        nonce = os.urandom(12)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        sealed = AESGCM(file_key).encrypt(nonce, data, _object_aad(key))
        header = json.dumps(
            {
                "wk": base64.b64encode(wrapped.encode()).decode(),
                "n": base64.b64encode(nonce).decode(),
            }
        ).encode()
        blob = _MAGIC + len(header).to_bytes(2, "big") + header + sealed
        # The blob must rest under the PLAINTEXT's content key
        # — not under its own hash, which is what the inner
        # driver's put_bytes would compute — so the write goes
        # through the inner driver's key-addressed put_at
        # (LocalStorage provides it; the pairing is deliberate:
        # this wrapper exists for the local driver family).
        put_at = getattr(self.inner, "put_at", None)
        if put_at is None:
            raise RuntimeError(
                "EncryptedStorage needs an inner driver with put_at "
                "(key-addressed writes); LocalStorage provides it."
            )
        put_at(key, blob)
        return key

    def get_bytes(self, key: str) -> bytes:
        validate_key(key)
        blob = self.inner.get_bytes(key)
        if not blob.startswith(_MAGIC):
            return blob  # a legacy object, written before the seal
        header_len = int.from_bytes(blob[5:7], "big")
        header = json.loads(blob[7 : 7 + header_len])
        sealed = blob[7 + header_len :]
        wrapped = base64.b64decode(header["wk"]).decode()
        file_key = crypto.open_sealed(self._master(), wrapped, aad=_FILE_KEY_AAD)
        nonce = base64.b64decode(header["n"])
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(file_key).decrypt(nonce, sealed, _object_aad(key))

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)


def build_storage(settings) -> StorageBackend:
    """The composition choice: the local driver, wrapped in
    the encrypted driver when the deployment asks
    (``GPA_ENCRYPT_STORAGE=1``)."""
    from gpa.storage.local import LocalStorage

    inner = LocalStorage(settings.storage_dir)
    if getattr(settings, "encrypt_storage", False):
        return EncryptedStorage(inner, settings)
    return inner


__all__ = ["EncryptedStorage", "build_storage"]
