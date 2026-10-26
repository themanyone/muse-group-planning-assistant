"""Advanced account security (roadmap B-174).

What this module is for: the second layer over Phase 1's
accounts — everything that protects a signed-in person AFTER
the password exists:

- **TOTP MFA** (RFC 6238, hand-rolled on the standard library
  like the JWT in security.py — D-46): an authenticator app is
  a second factor. Owners and admins are OFFERED it loudly
  (``offer_required`` on the status — the roadmap's
  mandatory-offer), members may take it up freely. Enrolment
  is two steps: setup mints a secret (stored SEALED via
  services/sealing.py — the database alone yields nothing),
  confirm proves the person's app can read it, and only then
  does ``mfa_enabled`` flip. A code is single-use per 30-second
  step: the accepted step is remembered and a replay inside
  its own window is refused.
- **Password policy**: length (12+) plus a LOCAL breached /
  common-password list. The roadmap allows the k-anonymity API
  or a local list; the local list is the honest offline choice
  — no password (or fragment of one) ever leaves this machine
  to be checked.
- **Progressive lockout**: consecutive failed password
  sign-ins lock the account for 1 minute, then 2, 4, 8 …
  capped at an hour. Success resets the count. (The per-IP
  rate limiter of B-021 still guards the door overall; this
  guards the ACCOUNT, from any number of addresses.)
- **Session list + per-device revoke**: a session is a
  refresh-token FAMILY (B-013). The list names each family's
  device words and birth, and revoking one family signs out
  exactly that device — the others keep working.
- **Login anomaly**: signing in from a never-seen (IP,
  user-agent) pair, when the person already has known devices,
  writes a notice to the outbox and an audit row.

WebAuthn / passkeys are the roadmap's named post-window
stretch on this same session chassis; they are NOT built
here and nothing in this module pretends otherwise.
"""

import base64
import hashlib
import hmac
import math
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.groups import Membership
from gpa.models.identity import RefreshToken, User, UserDevice, now_utc
from gpa.services import audit, mail, sealing

# --- TOTP (RFC 6238) -------------------------------------------------

TOTP_PERIOD = 30
TOTP_DIGITS = 6


def new_totp_secret() -> str:
    """A fresh base32 TOTP secret (160 bits, the RFC's size)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret_b32: str, *, at: float | None = None, step: int | None = None) -> str:
    """The 6-digit code for one time step (or for *now*).

    HMAC-SHA1 over the 8-byte step counter, dynamic truncation
    per RFC 4226 §5.3, reduced mod 10^6 and zero-padded — the
    exact arithmetic every authenticator app performs.
    """
    if step is None:
        step = int((time.time() if at is None else at) // TOTP_PERIOD)
    padding = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32 + padding)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def match_totp_step(secret_b32: str, code: str, *, at: float | None = None) -> int | None:
    """Return the time step *code* belongs to (±1 step of clock
    slack), or None when it matches no nearby step. Returning
    the step — not a bool — is what lets the caller enforce
    single-use per step."""
    code = (code or "").strip()
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    now_step = int((time.time() if at is None else at) // TOTP_PERIOD)
    for candidate in (now_step - 1, now_step, now_step + 1):
        if hmac.compare_digest(totp_code(secret_b32, step=candidate), code):
            return candidate
    return None


def otpauth_uri(secret_b32: str, email: str) -> str:
    """The enrolment URI authenticator apps read from a QR code."""
    label = quote(f"GPA:{email}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}"
        f"&issuer=GPA&digits={TOTP_DIGITS}&period={TOTP_PERIOD}"
    )


def _open_user_secret(settings: Settings, user: User) -> str:
    """The person's TOTP secret in the clear, for verification
    only — it leaves this module as a code comparison, never
    as a value anyone is shown (setup is the one showing)."""
    if not user.totp_secret:
        raise AppError("mfa_code_invalid", detail="Two-factor authentication is not set up on this account.")
    try:
        return sealing.open_sealed(settings, user.totp_secret)
    except ValueError:
        raise AppError("mfa_code_invalid", detail="That authentication code did not match.") from None


def verify_user_code(settings: Settings, user: User, code: str) -> int:
    """Verify a presented code for this user; return its step.

    Refuses a code whose step was already spent — the same
    six digits presented twice inside their window are a
    replay, not a sign-in (the TOTP half of B-013's law)."""
    secret = _open_user_secret(settings, user)
    step = match_totp_step(secret, code)
    if step is None or step <= (user.mfa_last_step if user.mfa_last_step is not None else -1):
        raise AppError("mfa_code_invalid", detail="That authentication code did not match.")
    return step


# --- MFA enrolment ---------------------------------------------------


def mfa_offer_required(db: Session, user: User) -> bool:
    """True when the person owns or administers any group — the
    people the roadmap says must be OFFERED MFA (mandatory-
    offer; taking it up stays their act)."""
    rows = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.status == "active",
            Membership.role.in_(("owner", "admin")),
        )
        .count()
    )
    return rows > 0


def mfa_status(db: Session, user: User) -> dict:
    """The account's MFA posture, in plain fields."""
    return {
        "enabled": bool(user.mfa_enabled),
        "setup_pending": bool(user.totp_secret) and not user.mfa_enabled,
        "offer_required": mfa_offer_required(db, user),
    }


def start_mfa_setup(db: Session, settings: Settings, user: User) -> dict:
    """Mint a fresh TOTP secret for enrolment. The plaintext is
    returned ONCE, in this answer, for the person's
    authenticator app; the database keeps only the sealed form,
    and MFA does not protect anything until confirm proves the
    app can read the secret."""
    if user.mfa_enabled:
        raise AppError("conflict", detail="Two-factor authentication is already on. Turn it off first if you want to set it up again.")
    secret = new_totp_secret()
    user.totp_secret = sealing.seal(settings, secret)
    audit.record(db, actor=f"user:{user.id}", action="user.mfa_setup_started", entity_type="user", entity_id=user.id)
    db.commit()
    return {"secret": secret, "otpauth_uri": otpauth_uri(secret, user.email), "enabled": False}


def confirm_mfa(db: Session, settings: Settings, user: User, code: str) -> dict:
    """Finish enrolment: a correct code from the new secret
    flips MFA on. The confirming code's step is NOT spent —
    enrolment happens inside an already-signed-in session, and
    the person may sign in again inside the same 30 seconds;
    the single-use rule guards sign-in challenges, where the
    replay threat lives."""
    if user.mfa_enabled:
        raise AppError("conflict", detail="Two-factor authentication is already on.")
    secret = _open_user_secret(settings, user)
    if match_totp_step(secret, code) is None:
        raise AppError("mfa_code_invalid", detail="That authentication code did not match.")
    user.mfa_enabled = 1
    audit.record(db, actor=f"user:{user.id}", action="user.mfa_enabled", entity_type="user", entity_id=user.id)
    db.commit()
    return mfa_status(db, user)


def disable_mfa(db: Session, settings: Settings, user: User, code: str) -> dict:
    """Turn MFA off, proven by a current code. Every existing
    session is revoked by the caller-visible consequence too:
    turning off a second factor is exactly the moment old
    sessions should not ride on."""
    if not user.mfa_enabled:
        raise AppError("conflict", detail="Two-factor authentication is not on for this account.")
    step = verify_user_code(settings, user, code)
    user.mfa_last_step = step
    user.mfa_enabled = 0
    user.totp_secret = None
    audit.record(db, actor=f"user:{user.id}", action="user.mfa_disabled", entity_type="user", entity_id=user.id)
    db.commit()
    from gpa.services.identity import revoke_all_sessions

    revoke_all_sessions(db, user.id)
    return mfa_status(db, user)


# --- Password policy ---------------------------------------------------

PASSWORD_MIN_LENGTH = 12

# A local breached/common list (the roadmap's offline option):
# passwords that appear in every public breach corpus and must
# never protect an account here, however long they are.
COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "password", "password1", "password12", "password123", "password1234",
        "123456789012", "1234567890", "qwerty123456", "qwertyuiop12",
        "letmein12345", "iloveyou1234", "admin1234567", "welcome12345",
        "changeme1234", "football1234", "baseball1234", "dragon123456",
        "monkey123456", "sunshine1234", "princess1234", "superman1234",
        "trustno11234", "correct-horse",
    }
)


def validate_password_policy(password: str, *, email: str = "") -> None:
    """Refuse a password that must not protect an account, in
    plain words: too short, on the common/breached list, or
    just the account's own email name dressed up."""
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise AppError(
            "password_too_weak",
            detail=f"Passwords need at least {PASSWORD_MIN_LENGTH} characters here.",
        )
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        raise AppError(
            "password_too_weak",
            detail="That password is on the list of passwords leaked in public breaches. Please choose a different one.",
        )
    local = (email or "").split("@", 1)[0].strip().lower()
    if local and lowered == local:
        raise AppError(
            "password_too_weak",
            detail="Your password cannot just be the name part of your email address.",
        )


# --- Progressive lockout ----------------------------------------------

LOCKOUT_THRESHOLD = 5
LOCKOUT_BASE_SECONDS = 60
LOCKOUT_MAX_SECONDS = 3600


def assert_not_locked(user: User) -> None:
    """Refuse a password sign-in while the account is locked,
    naming how long is left in plain words."""
    if user.locked_until is not None and user.locked_until > now_utc():
        minutes = max(1, math.ceil((user.locked_until - now_utc()).total_seconds() / 60))
        raise AppError(
            "account_locked",
            detail=(
                "Too many failed sign-in attempts. This account is locked "
                f"for about {minutes} more minute{'s' if minutes != 1 else ''} — "
                "then you can try again."
            ),
        )


def note_login_failure(db: Session, user: User) -> None:
    """Count one failed password sign-in; at the threshold the
    lock starts, and every further failure doubles it (capped).
    Commits — the count must survive the refused request."""
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    attempts = user.failed_login_attempts
    if attempts >= LOCKOUT_THRESHOLD:
        doublings = attempts - LOCKOUT_THRESHOLD
        seconds = min(LOCKOUT_MAX_SECONDS, LOCKOUT_BASE_SECONDS * (2**doublings))
        user.locked_until = now_utc() + timedelta(seconds=seconds)
    audit.record(
        db, actor=f"user:{user.id}", action="user.login_failed",
        entity_type="user", entity_id=user.id,
        detail={"attempts": attempts, "locked": bool(user.locked_until and user.locked_until > now_utc())},
    )
    db.commit()


def note_login_success(db: Session, user: User) -> None:
    """A good sign-in clears the failure count and any lock.
    (No commit: the session issue that follows commits.)"""
    if user.failed_login_attempts or user.locked_until is not None:
        user.failed_login_attempts = 0
        user.locked_until = None
        db.flush()


# --- Devices + anomaly notices ------------------------------------------


def record_device(db: Session, settings: Settings, user: User, *, ip_address: str, user_agent: str) -> None:
    """Remember this sign-in device; a NEW device for a person
    who already has known ones earns an outbox notice + audit
    row (the anomaly signal). Flushes only — the caller's
    session commit carries it."""
    ip_address = (ip_address or "")[:64]
    user_agent = (user_agent or "")[:240]
    if not ip_address and not user_agent:
        return
    known = db.query(UserDevice).filter(UserDevice.user_id == user.id).all()
    match = next(
        (d for d in known if d.ip_address == ip_address and d.user_agent == user_agent), None
    )
    if match is not None:
        match.last_seen_at = now_utc()
        db.flush()
        return
    db.add(UserDevice(user_id=user.id, ip_address=ip_address, user_agent=user_agent))
    db.flush()
    if known:
        audit.record(
            db, actor=f"user:{user.id}", action="user.login_new_device",
            entity_type="user", entity_id=user.id,
            detail={"ip_address": ip_address, "user_agent": user_agent},
        )
        mail.send_mail(
            settings,
            to=user.email,
            subject="A new device signed in to your Group Planning Assistant account",
            body=(
                "Hello,\n\nA new device just signed in to your account.\n\n"
                f"Address: {ip_address or 'unknown'}\n"
                f"Browser/app: {user_agent or 'unknown'}\n\n"
                "If this was you, you can ignore this message. If it was not, "
                "change your password straight away — that signs out every device."
            ),
            purpose="login_new_device",
        )


# --- Session list + revoke ----------------------------------------------


def _family_status(rows: list[RefreshToken]) -> str:
    """One family's state from its token rows: a live link
    (unrevoked, unreplaced, unexpired) makes it active."""
    now = now_utc()
    for row in rows:
        if row.revoked_at is None and row.replaced_by_id is None and row.expires_at > now:
            return "active"
    if any(row.revoked_at is not None for row in rows):
        return "revoked"
    return "expired"


def list_sessions(db: Session, user: User, *, current_family_id: str = "") -> list[dict]:
    """Every session family the person holds, newest first —
    the per-device list B-174 promises. Device words come from
    the family's most recent token row."""
    rows = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id)
        .order_by(RefreshToken.created_at.desc())
        .all()
    )
    families: dict[str, list[RefreshToken]] = {}
    order: list[str] = []
    for row in rows:
        if row.family_id not in families:
            families[row.family_id] = []
            order.append(row.family_id)
        families[row.family_id].append(row)
    sessions = []
    for family_id in order:
        members = families[family_id]
        latest = members[0]  # rows arrived newest-first
        sessions.append(
            {
                "family_id": family_id,
                "created_at": min(r.created_at for r in members).isoformat(),
                "last_seen_at": latest.created_at.isoformat(),
                "expires_at": max(r.expires_at for r in members).isoformat(),
                "user_agent": latest.user_agent or "",
                "ip_address": latest.ip_address or "",
                "status": _family_status(members),
                "current": bool(current_family_id) and family_id == current_family_id,
            }
        )
    return sessions


def revoke_session(db: Session, user: User, family_id: str) -> None:
    """Sign out ONE device: revoke its whole refresh family.
    Another person's family is a 404 — the list only ever
    shows the caller's own, and so does the revoke."""
    rows = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id, RefreshToken.family_id == family_id)
        .all()
    )
    if not rows:
        raise AppError("not_found", detail="That session could not be found.")
    for row in rows:
        if row.revoked_at is None:
            row.revoked_at = now_utc()
    audit.record(
        db, actor=f"user:{user.id}", action="user.session_revoked",
        entity_type="user", entity_id=user.id, detail={"family_id": family_id},
    )
    db.commit()
