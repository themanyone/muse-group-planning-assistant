"""The storage interface (roadmap B-007).

What this file is for: the contract every storage driver honours.
A *key* is the content address of the bytes — the SHA-256 hex of the
content, optionally with a short file extension. Keys are the only
names callers ever hold; anything that is not exactly a key is
rejected, which is what makes path traversal impossible by design
rather than by vigilance.
"""

import re
from typing import Protocol, runtime_checkable

from gpa.errors import AppError

# A valid key: 64 lowercase hex chars + optional short extension.
# Anything else — "..", "/", absolute paths, backslashes — is not a
# key and never reaches a filesystem.
KEY_PATTERN = re.compile(r"^[0-9a-f]{64}(\.[a-z0-9]{1,8})?$")


def validate_key(key: str) -> str:
    """Return ``key`` if it is exactly a content address, else 422.

    This is the traversal guard: because a key can only be hex, no
    caller input can climb out of the storage root.
    """
    if not KEY_PATTERN.match(key or ""):
        raise AppError("validation_error", detail="That is not a valid storage key.")
    return key


@runtime_checkable
class StorageBackend(Protocol):
    """What every storage driver provides."""

    def put_bytes(self, data: bytes, extension: str = "") -> str:
        """Store bytes; return their content-addressed key."""
        ...

    def get_bytes(self, key: str) -> bytes:
        """Fetch bytes by key; a missing key raises not_found."""
        ...

    def exists(self, key: str) -> bool:
        """True when bytes exist under ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Remove bytes under ``key`` (missing keys are ignored)."""
        ...
