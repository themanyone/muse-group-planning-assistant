"""File storage behind one interface (roadmap B-007, D-17).

Callers ask for bytes by *key* and never learn a filesystem path —
so the local driver today can become an S3-compatible driver later
without any caller changing.

The registry below mirrors the AI client's (ai/client.py): job
handlers receive only (db, payload), so the process-wide storage
backend lives here for them — the vision and transcription
handlers (Phase 5) read stored bytes through it. ``create_app``
and the worker entry point both call ``set_storage`` at startup;
``get_storage`` builds the settings-named default on first use.
"""

from gpa.storage.base import StorageBackend
from gpa.storage.local import LocalStorage

__all__ = ["StorageBackend", "LocalStorage", "set_storage", "get_storage"]

_storage: StorageBackend | None = None


def set_storage(storage: StorageBackend | None) -> None:
    """Install the process-wide backend (or clear it with None)."""
    global _storage
    _storage = storage


def get_storage() -> StorageBackend:
    """The process-wide backend, building the local default (from
    environment settings) on first use."""
    global _storage
    if _storage is None:
        from gpa.config import get_settings

        _storage = LocalStorage(get_settings().storage_dir)
    return _storage
