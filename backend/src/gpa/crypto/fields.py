"""The field-encryption service (roadmap B-167).

Writers seal through here; readers open through here; the
stored form is the only thing that ever touches disk. The
first adopters are the source chunks (message text + sender
names) and the provenance quotes (evidence excerpts of that
same text) — the rawest personal content the system holds.
The service itself is field-generic: any module can seal a
field for a group with one call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import os

from sqlalchemy.orm import Session

from gpa.config import Settings, get_settings
from gpa.internal import crypto as comms_crypto
from gpa.models.crypto import GroupKey

# The stored form's version prefix (Samtal-proven pattern):
# algorithms rotate by introducing v2 beside v1, never by
# reinterpreting v1.
FIELD_PREFIX = "enc:v1:"

_MASTER_DOMAIN = b"gpa-field-master-v1"
_INDEX_DOMAIN = b"gpa-blind-index-v1"

_injected_settings: Settings | None = None


def set_settings(settings: Settings) -> None:
    """Composition's injection: this process's settings."""
    global _injected_settings
    _injected_settings = settings


def _resolve(settings: Settings | None) -> Settings:
    return settings or _injected_settings or get_settings()


def master_key(settings: Settings | None = None) -> bytes:
    """The deployment's master key: configured base64
    (``GPA_MASTER_KEY_B64``) wins; otherwise derived from
    the app secret under the domain label (development
    posture). Never stored, never logged."""
    resolved = _resolve(settings)
    configured = getattr(resolved, "master_key_b64", "") or ""
    if configured:
        key = base64.b64decode(configured.encode("ascii"), validate=True)
        if len(key) != 32:
            from gpa.errors import AppError

            raise AppError(
                "internal_bad_key",
                detail="GPA_MASTER_KEY_B64 must decode to 32 bytes.",
            )
        return key
    return hashlib.sha256(_MASTER_DOMAIN + b":" + resolved.secret_key.encode()).digest()


def _wrap_aad(group_id: str) -> bytes:
    return f"gpa-group-key:{group_id}".encode()


def wrap_aad(group_id: str) -> bytes:
    """The public spelling (B-169's rotation re-wraps with
    the same authenticated data)."""
    return _wrap_aad(group_id)


def ensure_group_key(db: Session, group_id: str, settings: Settings | None = None) -> GroupKey:
    """The group's key row, created on first use: a random
    data key wrapped under the master key. Rides the caller's
    transaction (flush, no commit) like every service write."""
    row = db.query(GroupKey).filter(GroupKey.group_id == group_id).one_or_none()
    if row is not None:
        return row
    data_key = os.urandom(32)
    wrapped = comms_crypto.seal(master_key(settings), data_key, aad=_wrap_aad(group_id))
    row = GroupKey(group_id=group_id, wrapped_key=wrapped, version=1)
    db.add(row)
    db.flush()
    return row


def data_key(db: Session, group_id: str, settings: Settings | None = None) -> bytes:
    """The group's unwrapped data key, for sealing/opening."""
    row = ensure_group_key(db, group_id, settings)
    return comms_crypto.open_sealed(
        master_key(settings), row.wrapped_key, aad=_wrap_aad(group_id)
    )


def _existing_data_key(db: Session, group_id: str, settings: Settings | None) -> bytes:
    """The data key for OPENING: a group with no key row has
    nothing sealed, so a sealed value claiming its name is
    tampering — opening must never create key material."""
    from gpa.errors import AppError

    row = db.query(GroupKey).filter(GroupKey.group_id == group_id).one_or_none()
    if row is None:
        raise AppError(
            "internal_tampered",
            detail="A sealed field names a group that holds no key.",
        )
    return comms_crypto.open_sealed(
        master_key(settings), row.wrapped_key, aad=_wrap_aad(group_id)
    )


def _field_aad(group_id: str, field: str) -> bytes:
    return f"{group_id}:{field}".encode()


def seal_text(
    db: Session,
    group_id: str,
    field: str,
    plaintext: str,
    settings: Settings | None = None,
) -> str:
    """A field's stored form. Empty stays empty (an absent
    value has nothing to hide, and emptiness must stay
    distinguishable from a sealed empty string)."""
    if not plaintext:
        return plaintext
    if plaintext.startswith(FIELD_PREFIX):
        return plaintext  # already sealed (defensive: never double-seal)
    sealed = comms_crypto.seal(
        data_key(db, group_id, settings),
        plaintext.encode(),
        aad=_field_aad(group_id, field),
    )
    return FIELD_PREFIX + sealed


def open_text(
    db: Session,
    group_id: str,
    field: str,
    stored: str,
    settings: Settings | None = None,
) -> str:
    """Read a stored field: sealed form opens; legacy
    plaintext (no prefix) reads as itself."""
    if not stored or not stored.startswith(FIELD_PREFIX):
        return stored
    raw = comms_crypto.open_sealed(
        _existing_data_key(db, group_id, settings),
        stored[len(FIELD_PREFIX):],
        aad=_field_aad(group_id, field),
    )
    return raw.decode()


def blind_index(value: str, settings: Settings | None = None) -> str:
    """The keyed lookup token for a sealed field: HMAC-SHA256
    under an index key derived from the master with its own
    domain. Deterministic (that's the point — lookups work),
    one-way, and useless to anyone holding only the database."""
    master = master_key(settings)
    index_key = hmac.new(master, _INDEX_DOMAIN, hashlib.sha256).digest()
    return hmac.new(index_key, value.encode(), hashlib.sha256).hexdigest()


__all__ = [
    "FIELD_PREFIX",
    "blind_index",
    "data_key",
    "ensure_group_key",
    "master_key",
    "open_text",
    "seal_text",
    "set_settings",
]
