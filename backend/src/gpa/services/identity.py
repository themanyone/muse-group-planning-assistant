"""Identity services (roadmap B-011 → B-014, B-020).

Accounts, passwords, magic links, sessions, recovery, and API keys.
Every function here owns its transaction and commits before it
returns (B-003). Credentials are hashed at rest — see security.py
and models/identity.py for the rules.
"""

import re
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.identity import ApiKey, OneTimeToken, RefreshToken, User, now_utc
from gpa.security import (
    hash_password,
    hash_secret,
    issue_access_token,
    new_api_key,
    new_token,
    verify_password,
)
from gpa.services import account_security, audit, mail

# Deliberately small email shape check (see schemas/identity.py):
# something @ something . something, no spaces.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The only scopes an API key may carry (B-014), least first.
ALL_SCOPES: tuple[str, ...] = ("read", "draft", "publish", "admin")


def normalize_email(email: str) -> str:
    """Lowercase + trim an email, or fail with a validation error."""
    cleaned = (email or "").strip().lower()
    if not _EMAIL_RE.match(cleaned):
        raise AppError("validation_error", detail="That email address does not look right.")
    return cleaned


def get_user(db: Session, user_id: str) -> User:
    """Fetch a user by id or raise not_found."""
    user = db.get(User, user_id)
    if user is None:
        raise AppError("not_found", detail="That user could not be found.")
    return user


def get_user_by_email(db: Session, email: str) -> User | None:
    """Fetch a user by (already normalised) email, or None."""
    return db.query(User).filter(User.email == email).one_or_none()


# --- Accounts (B-011) -----------------------------------------------


def register_user(
    db: Session,
    *,
    email: str,
    password: str,
    name: str = "",
    timezone: str = "UTC",
    locale: str = "en",
) -> User:
    """Create an account with an Argon2id-hashed password.

    If the email belongs to an invited, passwordless account (created
    by a group invite), registering claims it: the password and
    profile are set on that same account, so invites keep working.
    """
    email = normalize_email(email)
    account_security.validate_password_policy(password, email=email)
    existing = get_user_by_email(db, email)
    if existing is not None and existing.password_hash is not None:
        raise AppError("email_taken")
    if existing is not None:
        user = existing
        user.password_hash = hash_password(password)
        user.name = name or user.name
        user.timezone = timezone
        user.locale = locale
    else:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            timezone=timezone,
            locale=locale,
        )
        db.add(user)
        db.flush()
    audit.record(db, actor=f"user:{user.id}", action="user.registered", entity_type="user", entity_id=user.id)
    db.commit()
    return user


def update_profile(
    db: Session, user: User, *, name=None, timezone=None, locale=None
) -> User:
    """Update the editable profile fields (only those provided)."""
    if name is not None:
        user.name = name
    if timezone is not None:
        user.timezone = timezone
    if locale is not None:
        user.locale = locale
    db.commit()
    return user


def authenticate(db: Session, *, email: str, password: str) -> User:
    """Check email + password. One vague error for every failure —
    the response must not reveal whether the account exists.

    B-174 rides on this door: a locked account is refused before
    the password is even checked (in its own plain words), a
    failure is counted toward the progressive lockout, and a
    success clears the count."""
    user = get_user_by_email(db, normalize_email(email))
    if user is None:
        raise AppError("invalid_credentials")
    account_security.assert_not_locked(user)
    if user.password_hash is None or not verify_password(user.password_hash, password):
        account_security.note_login_failure(db, user)
        raise AppError("invalid_credentials")
    account_security.note_login_success(db, user)
    return user


# --- Sessions (B-013) -------------------------------------------------


def issue_session(
    db: Session,
    settings: Settings,
    user: User,
    *,
    ip_address: str = "",
    user_agent: str = "",
) -> tuple[str, str]:
    """Start a NEW refresh-token family; return (access, refresh).

    The refresh token itself is returned raw exactly once; only its
    hash is stored. The caller's device words (B-174) ride on the
    row — the session list's naming — and the device is recorded
    for the anomaly notice in the same commit.
    """
    raw_refresh = new_token(48)
    row = RefreshToken(
        user_id=user.id,
        family_id=str(uuid.uuid4()),
        token_hash=hash_secret(raw_refresh),
        expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
        user_agent=(user_agent or "")[:240],
        ip_address=(ip_address or "")[:64],
    )
    db.add(row)
    db.flush()
    account_security.record_device(
        db, settings, user, ip_address=ip_address, user_agent=user_agent
    )
    db.commit()
    access = issue_access_token(
        settings.secret_key, user.id, settings.access_token_minutes, family_id=row.family_id
    )
    return access, raw_refresh


def rotate_refresh(db: Session, settings: Settings, raw_refresh: str) -> tuple[str, str, User]:
    """Exchange a refresh token for a fresh (access, refresh) pair.

    Rotation rule (B-013): the presented token is retired and a new
    one issued in the same family. Presenting a token that was
    already rotated or revoked means someone is replaying a stolen
    token — the ENTIRE family is revoked and the call fails.
    """
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_secret(raw_refresh))
        .one_or_none()
    )
    if row is None:
        raise AppError("token_invalid")
    if row.revoked_at is not None or row.replaced_by_id is not None:
        # Reuse of an old token: kill the whole family, fail closed.
        db.query(RefreshToken).filter(
            RefreshToken.family_id == row.family_id,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": now_utc()})
        db.commit()
        raise AppError("token_reused", detail="That session token was already used; for safety, every session in its family has been signed out.")
    if row.expires_at < now_utc():
        raise AppError("token_expired")
    user = get_user(db, row.user_id)
    new_raw = new_token(48)
    new_row = RefreshToken(
        user_id=user.id,
        family_id=row.family_id,
        token_hash=hash_secret(new_raw),
        expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
        # One family is one device (B-174): the device words
        # carry forward across rotation.
        user_agent=row.user_agent or "",
        ip_address=row.ip_address or "",
    )
    db.add(new_row)
    db.flush()
    row.replaced_by_id = new_row.id
    db.commit()
    access = issue_access_token(
        settings.secret_key, user.id, settings.access_token_minutes, family_id=row.family_id
    )
    return access, new_raw, user


def logout(db: Session, raw_refresh: str) -> None:
    """Sign out: revoke the presented token's whole family."""
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_secret(raw_refresh))
        .one_or_none()
    )
    if row is not None:
        db.query(RefreshToken).filter(
            RefreshToken.family_id == row.family_id,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": now_utc()})
        db.commit()


def revoke_all_sessions(db: Session, user_id: str) -> None:
    """Revoke every refresh token a user holds (password change,
    email change — B-020: old sessions must die)."""
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    ).update({"revoked_at": now_utc()})
    db.commit()


# --- One-time tokens: magic links, resets, email change (B-012/B-020) --


def request_one_time_token(
    db: Session,
    settings: Settings,
    user: User,
    *,
    purpose: str,
    new_email: str | None = None,
) -> str:
    """Create a hashed one-time token and "email" it via the outbox.

    Returns the raw token (callers in dev/tests read it from the
    outbox file the mail driver wrote; production mail renders it
    into a link). The database keeps only the hash.
    """
    raw = new_token(32)
    row = OneTimeToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=hash_secret(raw),
        new_email=new_email,
        expires_at=now_utc() + timedelta(minutes=settings.magic_link_minutes),
    )
    db.add(row)
    db.commit()
    subjects = {
        "magic_link": "Your Group Planning Assistant sign-in link",
        "password_reset": "Reset your Group Planning Assistant password",
        "email_change": "Confirm your new email address",
    }
    recipient = new_email or user.email
    mail.send_mail(
        settings,
        to=recipient,
        subject=subjects.get(purpose, "Your Group Planning Assistant link"),
        body=(
            f"Hello,\n\nUse this one-time token within "
            f"{settings.magic_link_minutes} minutes:\n\n{raw}\n\n"
            "If you did not ask for this, you can ignore this message."
        ),
        token=raw,
        purpose=purpose,
    )
    return raw


def request_magic_link(db: Session, settings: Settings, *, email: str) -> None:
    """Ask for a sign-in link. Answers the same whether or not the
    account exists — the endpoint must not enumerate accounts. An
    unknown email simply produces no mail."""
    user = get_user_by_email(db, normalize_email(email))
    if user is not None:
        request_one_time_token(db, settings, user, purpose="magic_link")


def _consume(db: Session, raw_token: str, purpose: str) -> OneTimeToken:
    """Validate + burn a one-time token, failing closed:
    unknown → invalid, already used → reused, late → expired."""
    row = (
        db.query(OneTimeToken)
        .filter(
            OneTimeToken.token_hash == hash_secret(raw_token),
            OneTimeToken.purpose == purpose,
        )
        .one_or_none()
    )
    if row is None:
        raise AppError("token_invalid")
    if row.used_at is not None:
        raise AppError("token_reused")
    if row.expires_at < now_utc():
        raise AppError("token_expired")
    row.used_at = now_utc()
    db.flush()
    return row


def consume_magic_link(db: Session, settings: Settings, raw_token: str) -> tuple[str, str, User]:
    """Use a magic-link token; return a fresh session + the user."""
    row = _consume(db, raw_token, "magic_link")
    user = get_user(db, row.user_id)
    db.commit()
    access, refresh = issue_session(db, settings, user)
    return access, refresh, user


def request_password_reset(db: Session, settings: Settings, *, email: str) -> None:
    """Ask for a password reset (same no-enumeration rule as magic
    links: silence for unknown emails)."""
    user = get_user_by_email(db, normalize_email(email))
    if user is not None:
        request_one_time_token(db, settings, user, purpose="password_reset")


def consume_password_reset(db: Session, raw_token: str, new_password: str) -> User:
    """Set a new password from a reset token, then revoke every
    existing session — a reset is also a sign-out-everywhere."""
    row = _consume(db, raw_token, "password_reset")
    user = get_user(db, row.user_id)
    account_security.validate_password_policy(new_password, email=user.email)
    user.password_hash = hash_password(new_password)
    db.commit()
    revoke_all_sessions(db, user.id)
    return user


def change_password(db: Session, user: User, *, current: str, new: str) -> None:
    """Change password knowing the current one; sessions revoked."""
    if user.password_hash is None or not verify_password(user.password_hash, current):
        raise AppError("invalid_credentials", detail="The current password did not match.")
    account_security.validate_password_policy(new, email=user.email)
    user.password_hash = hash_password(new)
    db.commit()
    revoke_all_sessions(db, user.id)


def request_email_change(db: Session, settings: Settings, user: User, *, new_email: str) -> None:
    """Start an email change: the confirmation token goes to the
    NEW address — proving control of it is the whole point."""
    new_email = normalize_email(new_email)
    existing = get_user_by_email(db, new_email)
    if existing is not None and existing.id != user.id:
        raise AppError("email_taken")
    request_one_time_token(db, settings, user, purpose="email_change", new_email=new_email)


def consume_email_change(db: Session, raw_token: str) -> User:
    """Complete an email change from its token; sessions revoked."""
    row = _consume(db, raw_token, "email_change")
    user = get_user(db, row.user_id)
    if row.new_email:
        user.email = row.new_email
    db.commit()
    revoke_all_sessions(db, user.id)
    return user


# --- MFA challenge (B-174) ---------------------------------------------
# A password that checks out for an MFA account does NOT yet earn
# a session: it earns a five-minute, single-use challenge. The
# session is minted only when a fresh TOTP code answers it.

MFA_CHALLENGE_MINUTES = 5


def begin_mfa_challenge(db: Session, user: User) -> str:
    """Open the post-password MFA challenge; return its raw token
    (hashed at rest like every one-time token, shown once here)."""
    raw = new_token(32)
    row = OneTimeToken(
        user_id=user.id,
        purpose="mfa_challenge",
        token_hash=hash_secret(raw),
        expires_at=now_utc() + timedelta(minutes=MFA_CHALLENGE_MINUTES),
    )
    db.add(row)
    db.commit()
    return raw


def complete_mfa_challenge(
    db: Session,
    settings: Settings,
    raw_token: str,
    code: str,
    *,
    ip_address: str = "",
    user_agent: str = "",
) -> tuple[str, str, User]:
    """Answer an MFA challenge with a TOTP code; return a session.

    The challenge is checked WITHOUT burning it first: a mistyped
    code may be retried inside its five minutes (the auth rate
    limiter guards the door). A correct code burns the challenge,
    spends its TOTP step (no replay), clears the login-failure
    count, and mints the session — all in commits that land
    before the answer (B-003)."""
    row = (
        db.query(OneTimeToken)
        .filter(
            OneTimeToken.token_hash == hash_secret(raw_token),
            OneTimeToken.purpose == "mfa_challenge",
        )
        .one_or_none()
    )
    if row is None:
        raise AppError("token_invalid")
    if row.used_at is not None:
        raise AppError("token_reused")
    if row.expires_at < now_utc():
        raise AppError("token_expired")
    user = get_user(db, row.user_id)
    step = account_security.verify_user_code(settings, user, code)
    row.used_at = now_utc()
    user.mfa_last_step = step
    account_security.note_login_success(db, user)
    db.commit()
    access, refresh = issue_session(
        db, settings, user, ip_address=ip_address, user_agent=user_agent
    )
    return access, refresh, user


# --- API keys (B-014) ---------------------------------------------------


def validate_scopes(scopes: list[str]) -> list[str]:
    """Check requested scopes against the known set, keep ladder
    order, de-duplicated. Unknown scope → validation error."""
    unknown = [s for s in scopes if s not in ALL_SCOPES]
    if unknown:
        raise AppError("validation_error", detail=f"Unknown scope(s): {', '.join(unknown)}")
    return [s for s in ALL_SCOPES if s in set(scopes)]


def create_api_key(
    db: Session,
    *,
    label: str = "",
    scopes: list[str],
    user_id: str | None = None,
    seat_id: str | None = None,
) -> tuple[ApiKey, str]:
    """Mint an API key; return (row, full_key). The full key is
    shown exactly once — only its hash and prefix are stored."""
    ordered = validate_scopes(scopes)
    full, prefix, key_hash = new_api_key()
    row = ApiKey(
        user_id=user_id,
        seat_id=seat_id,
        label=label,
        prefix=prefix,
        key_hash=key_hash,
        scopes=",".join(ordered),
    )
    db.add(row)
    db.commit()
    return row, full


def list_api_keys(db: Session, *, user_id: str) -> list[ApiKey]:
    """A user's keys, newest first (hashes never leave the DB)."""
    return (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )


def revoke_api_key(db: Session, *, user_id: str, key_id: str) -> None:
    """Revoke one of the user's own keys; others' keys are 404."""
    row = db.get(ApiKey, key_id)
    if row is None or row.user_id != user_id:
        raise AppError("not_found", detail="That API key could not be found.")
    row.revoked_at = now_utc()
    db.commit()
