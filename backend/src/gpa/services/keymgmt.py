"""Key management (roadmap B-169).

The hierarchy (documented in KEY_MANAGEMENT.md):

    master key (env / keyring — never in the DB)
      └─ wraps ─▶ per-group data keys (group_keys, B-167)
                     └─ seal ─▶ fields, job payloads (via the
                                internal key's own derivation),
                                file objects (per-file keys
                                wrapped by the master, B-168)
    recovery codes (shown once, rested nowhere)
      └─ scrypt ─▶ escrow seals of the master key
                   (recovery_escrow)

This service owns the two operations that hierarchy needs:

* **Master rotation** — every group key is unwrapped under
  the old master and re-wrapped under the new one (version
  bumped, ``rotated_at`` stamped). Sealed data is never
  touched: rotation is a re-wrap, never a flag day. It runs
  as a job (``crypto.rotate_master``) whose handler reads
  the old key from ``GPA_MASTER_KEY_OLD_B64`` — an operator
  sets both keys, queues the job, and unsets the old one
  when it lands.
* **Owner recovery codes** — minting a set escrows the
  CURRENT master under each code and DELETES any previous
  set (minting kills the old set, so a lost set is revoked
  by replacing it). A code is spent by its first successful
  recovery. Recovery returns the master key; the operator
  installs it as the deployment's key and the data opens —
  losing the master never means losing the group's data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.crypto import fields
from gpa.errors import AppError
from gpa.internal import crypto
from gpa.models.crypto import GroupKey, RecoveryEscrow
from gpa.models.identity import now_utc
from gpa.services import jobs as job_service

ROTATE_JOB_KIND = "crypto.rotate_master"

# Recovery codes: ten characters over an unambiguous alphabet
# (no 0/O, 1/l/I) in two groups of five — an owner reads one
# off paper or a mail without squinting. ~50 bits per code;
# the set is the credential, and each code also dies on use.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_GROUPS = 2
_CODE_GROUP_LEN = 5

# scrypt parameters for the escrow derivation: interactive-
# login strength (n=2^14 is the OWASP floor for this use);
# the derived key opens exactly one seal.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def _new_code() -> str:
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_LEN))
        for _ in range(_CODE_GROUPS)
    ]
    return "-".join(groups)


def _normalise(code: str) -> str:
    return "".join(code.split()).replace("-", "").upper()


def _code_check(settings: Settings, code: str) -> str:
    """The row-finding check value for a code: HMAC under a
    key derived from the APP SECRET — deliberately NOT from
    the master key. Recovery exists for the day the master
    is lost; a check keyed by the master could never be
    computed on that day. A database thief holds neither the
    secret nor the code, so guesses verify nowhere."""
    check_key = hmac.new(
        settings.secret_key.encode(), b"gpa-recovery-check-v1", hashlib.sha256
    ).digest()
    return hmac.new(check_key, _normalise(code).encode(), hashlib.sha256).hexdigest()


def _escrow_key(code: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        _normalise(code).encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )


def mint_recovery_codes(
    db: Session, settings: Settings, *, count: int = 8, actor_label: str = "system"
) -> list[str]:
    """Mint a fresh set of recovery codes; return them ONCE.

    The previous set (spent or not) is deleted first — the
    new set is the only set. Each code escrows the current
    master key under its own scrypt seal. The caller shows
    the codes to the owner (screen, one email) and keeps no
    copy: they exist nowhere after this return."""
    from gpa.services import audit

    db.query(RecoveryEscrow).delete(synchronize_session=False)
    master = fields.master_key(settings)
    codes: list[str] = []
    for _ in range(count):
        code = _new_code()
        salt = secrets.token_bytes(16)
        sealed = crypto.seal(
            _escrow_key(code, salt), master, aad=b"gpa-recovery-escrow-v1"
        )
        db.add(
            RecoveryEscrow(
                code_check=_code_check(settings, code),
                code_hint=_normalise(code)[-2:],
                salt=base64.b64encode(salt).decode(),
                sealed_master=sealed,
            )
        )
        codes.append(code)
    audit.record(
        db, actor=actor_label, action="recovery.codes_minted",
        entity_type="recovery_escrow", entity_id="",
        detail={"count": count},
    )
    db.commit()
    return codes


def recover_master_key(db: Session, settings: Settings, code: str) -> bytes:
    """Spend one recovery code; return the escrowed master.

    A wrong code and a spent code read identically
    (``recovery_code_invalid``) — the door does not teach
    guessing. Success stamps the row used and commits: the
    code is dead before the key it freed is ever used."""
    row = (
        db.query(RecoveryEscrow)
        .filter(RecoveryEscrow.code_check == _code_check(settings, code))
        .one_or_none()
    )
    if row is None or row.used_at is not None:
        raise AppError(
            "recovery_code_invalid",
            detail="That recovery code is not valid.",
        )
    salt = base64.b64decode(row.salt.encode())
    try:
        master = crypto.open_sealed(
            _escrow_key(code, salt), row.sealed_master, aad=b"gpa-recovery-escrow-v1"
        )
    except AppError as exc:
        raise AppError(
            "recovery_code_invalid",
            detail="That recovery code is not valid.",
        ) from exc
    row.used_at = now_utc()
    db.commit()
    return master


def rotate_master_key(
    db: Session, settings: Settings, *, old_master: bytes, actor_label: str = "system"
) -> dict:
    """Re-wrap every group key from the old master to the
    settings' (new) master. Returns the counts. A row that
    does not open under the old master stops the rotation
    BEFORE any row is rewritten (the whole rotation is one
    transaction — it lands whole or not at all)."""
    from gpa.services import audit

    new_master = fields.master_key(settings)
    rows = db.query(GroupKey).all()
    unwrapped: list[tuple[GroupKey, bytes]] = []
    for row in rows:
        data_key = crypto.open_sealed(
            old_master, row.wrapped_key, aad=fields.wrap_aad(row.group_id)
        )
        unwrapped.append((row, data_key))
    for row, data_key in unwrapped:
        row.wrapped_key = crypto.seal(
            new_master, data_key, aad=fields.wrap_aad(row.group_id)
        )
        row.version += 1
        row.rotated_at = now_utc()
    audit.record(
        db, actor=actor_label, action="crypto.master_rotated",
        entity_type="group_keys", entity_id="",
        detail={"groups": len(unwrapped)},
    )
    db.commit()
    return {"groups": len(unwrapped)}


def _handle_rotate(db: Session, payload: dict) -> None:
    """The rotation job: old key from the operator's env
    (``GPA_MASTER_KEY_OLD_B64``), new key = the settings'
    master. Missing old key = a plain failure the operator
    reads in the job's error, never a silent skip."""
    from gpa.config import get_settings

    settings = get_settings()
    old_b64 = getattr(settings, "master_key_old_b64", "") or ""
    if not old_b64:
        raise RuntimeError(
            "GPA_MASTER_KEY_OLD_B64 is not set — rotation needs the old master key."
        )
    rotate_master_key(db, settings, old_master=base64.b64decode(old_b64))


def register_handlers() -> None:
    job_service.register_handler(ROTATE_JOB_KIND, _handle_rotate)


register_handlers()


__all__ = [
    "ROTATE_JOB_KIND",
    "mint_recovery_codes",
    "recover_master_key",
    "register_handlers",
    "rotate_master_key",
]
