"""Identity tables (roadmap Phase 1, B-011 → B-014, B-020).

Who a person is, and every credential they can hold. Security notes
that shape these tables:

- Passwords exist only as Argon2id hashes (``password_hash``).
- One-time tokens (magic links, password resets, email changes) and
  API keys and refresh tokens exist only as SHA-256 hashes — the raw
  values are shown once, at issue, and never stored.
- Refresh tokens belong to a *family*: rotating issues a new token
  in the same family; presenting an old one revokes them all
  (B-013's theft rule).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base


def new_id() -> str:
    """Fresh UUID4 as text — every table's primary-key default."""
    return str(uuid.uuid4())


def now_utc() -> datetime:
    """Current UTC time, naive — SQLite stores naive datetimes and
    this codebase treats every stored datetime as UTC (D-21)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """A person with an account. (AI participants are AgentSeats.)"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    # Nullable: an account created via a magic link may never set one.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    locale: Mapped[str] = mapped_column(String(16), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    # Advanced account security (B-174). The TOTP secret is stored
    # SEALED (services/sealing.py, keyed from the environment) —
    # never plaintext, never hashed (verification must read it).
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enabled: Mapped[int] = mapped_column(Integer, default=0)
    # The last TOTP time-step a code was accepted for. A code is
    # single-use per step: replaying the same code inside its own
    # 30-second window is refused by comparing against this.
    mfa_last_step: Mapped[int] = mapped_column(Integer, default=-1)
    # Progressive lockout (B-174): consecutive failed password
    # sign-ins, and the moment the current lock lifts.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OneTimeToken(Base):
    """A hashed, expiring, single-use token (B-012, B-020).

    ``purpose`` says what consuming it does: ``magic_link`` signs the
    user in, ``password_reset`` lets them set a new password,
    ``email_change`` confirms a new address (kept in ``new_email``).
    """

    __tablename__ = "one_time_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    new_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class RefreshToken(Base):
    """One link in a rotating refresh-token family (B-013)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The token that replaced this one at rotation, when it rotates —
    # the chain that makes reuse of an old token provable.
    replaced_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    # Which device/browser this session was born on (B-174's
    # session list): the caller's own words from their request
    # headers, kept so the person can recognise — and revoke —
    # each signed-in device. Rotation copies them forward: one
    # family is one device.
    user_agent: Mapped[str] = mapped_column(String(240), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")


class UserDevice(Base):
    """A device/browser a person has signed in from (B-174).

    Login-anomaly bookkeeping: a successful sign-in from a pair
    (IP, user-agent) this table has never seen — when the person
    already HAS known devices — earns them a notice through the
    outbox, so a stranger signing in as them does not pass
    quietly. First sightings are recorded here either way.
    """

    __tablename__ = "user_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(240), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ApiKey(Base):
    """A hashed API key with scopes (B-014), for a user or a seat.

    Scopes are a comma-joined list drawn from read/draft/publish/
    admin, least first. Exactly one owner: ``user_id`` for a person's
    key, ``seat_id`` for an agent seat's key.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    seat_id: Mapped[str | None] = mapped_column(ForeignKey("agent_seats.id"), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[str] = mapped_column(String(120), default="read")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    def scope_set(self) -> set[str]:
        """The key's scopes as a set, for permission checks."""
        return {s for s in self.scopes.split(",") if s}
