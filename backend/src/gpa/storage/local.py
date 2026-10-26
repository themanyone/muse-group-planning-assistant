"""Local-disk storage driver (roadmap B-007).

Content-addressed by SHA-256: the bytes name themselves, so the
same flyer uploaded twice is stored once, and a key can always be
re-verified against its content. Files are sharded two levels deep
(``ab/cd/<full hash>``) so no directory grows unwieldy, and writes
are atomic — a temporary file is renamed into place, so a crash
mid-write can never leave a half-file wearing a good key.
"""

import hashlib
import os
import tempfile
from pathlib import Path

from gpa.errors import AppError
from gpa.storage.base import validate_key


class LocalStorage:
    """StorageBackend on a local directory (see base.py)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        """Map a validated key to its sharded path inside the root.

        The final guard: the resolved path must still be inside the
        root. Belt and braces behind validate_key's strict pattern.
        """
        validate_key(key)
        digest, _, ext = key.partition(".")
        path = self.root / digest[:2] / digest[2:4] / (digest + (f".{ext}" if ext else ""))
        if self.root.resolve() not in path.resolve().parents:
            raise AppError("validation_error", detail="That is not a valid storage key.")
        return path

    def put_bytes(self, data: bytes, extension: str = "") -> str:
        """Store bytes atomically; return their key (idempotent)."""
        digest = hashlib.sha256(data).hexdigest()
        ext = extension.lower().lstrip(".")[:8]
        key = digest + (f".{ext}" if ext else "")
        path = self._path_for(key)
        if path.exists():
            return key  # Same content, same key — nothing to do.
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory, then rename:
        # rename within one filesystem is atomic, so readers only
        # ever see the whole file or nothing.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            # Never leave the temp file behind on a failed write.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return key

    def put_at(self, key: str, data: bytes) -> str:
        """Store bytes under an EXPLICIT key (B-168).

        The encrypted driver computes the content address of
        the PLAINTEXT itself, then needs the sealed blob filed
        under that key — a write put_bytes (which addresses by
        the bytes it is handed) cannot express. Same atomic
        write, same idempotence; the key is validated like
        every key."""
        validate_key(key)
        path = self._path_for(key)
        if path.exists():
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return key

    def get_bytes(self, key: str) -> bytes:
        """Fetch bytes by key; missing keys are a clean 404."""
        path = self._path_for(key)
        if not path.exists():
            raise AppError("not_found", detail="That file could not be found.")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        """True when bytes exist under ``key``."""
        return self._path_for(key).exists()

    def delete(self, key: str) -> None:
        """Remove bytes under ``key``; a missing key is not an error."""
        path = self._path_for(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
