"""Notification tables (roadmap Phase 8, B-095 → B-103).

A plan nobody hears about is a diary; these tables are how the
backend speaks — on schedule, on the member's chosen channel,
and never as spam. The shape, in one paragraph per table:

- ``Notification`` — ONE row per message per recipient. The row
  is created by the outbox rule (B-095): the request that wants
  someone told writes the row and a ``notify`` job, and NEVER
  sends inline — a mail outage can therefore never fail an
  RSVP. The row doubles as the recipient's in-app inbox item
  (B-101): which channels it travels by (``send_email`` /
  ``in_app``) is decided ONCE, at creation, by the member's
  preferences (B-100), so inbox and email can never disagree
  about what was sent.
- ``NotificationPreference`` — one switch: this member, this
  group, this channel, this kind-category, on or off. Absence
  of a row means the default: on.
- ``MemberNotificationSettings`` — the member's quiet hours in
  one group (B-100): a reminder whose moment falls inside them
  is HELD (``status="held"``, ``send_after`` = the quiet end),
  never dropped. Urgent rows (an organiser-marked cancellation)
  are never held.
- ``EventInvite`` — an invitation to one event (B-098): to a
  member (a notification) or to an outside email (this row's
  token link). The token is stored HASHED only (the B-014
  discipline) and shown once; tampering is a hash mismatch and
  finds nothing.
- ``Digest`` — one composed weekly digest (B-099): both
  renderings stored, so what was sent is what can be re-read.
- ``MailEvent`` — a provider's word about an address (B-102):
  bounces and complaints recorded by the webhook stub, so a
  failing address is VISIBLE, never silently retried forever.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc

# The channels a notification can travel by (B-095). Email and
# the in-app inbox exist since Phase 8; Messenger joins in
# Phase 12 (B-134) as one more value here — the preference and
# policy machinery was already channel-shaped, so the new
# channel inherits opt-in and quiet-hours rules unchanged.
CHANNELS: tuple[str, ...] = ("email", "in_app", "messenger")

# Notification kinds (the row's exact kind) and the preference
# CATEGORIES they answer to (B-100 names invites / reminders /
# changes / digest; the RSVP promotion kind files under "rsvp").
# Category = the kind's first dotted segment.
KIND_CATEGORIES: tuple[str, ...] = ("invite", "reminder", "change", "digest", "rsvp")

# The outbox lifecycle. queued → (held, when quiet hours defer
# it) → sent; failed while the send job retries; dead when the
# job gives up (B-102 makes dead rows an admin-visible list,
# never a silent end); superseded when a newer plan replaced it
# before it went (a moved event's old reminders, B-096).
NOTIFICATION_STATUSES: tuple[str, ...] = (
    "queued", "held", "sent", "failed", "dead", "superseded",
)


def category_of(kind: str) -> str:
    """A kind's preference category: its first dotted segment."""
    return kind.split(".", 1)[0]


class Notification(Base):
    """One message to one recipient, with its delivery truth."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("groups.id"), nullable=True, index=True
    )
    # The event the message is about, when it is about one.
    # Plain string (the tombstone discipline): deleting an event
    # must not take its notification history down with it.
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # The recipient. A person: ``recipient_user_id`` set and the
    # label "user:<id>". An outside guest (an invite answerer):
    # no user id, label "guest:<email>", the address below.
    recipient_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    recipient_label: Mapped[str] = mapped_column(String(320))
    recipient_email: Mapped[str] = mapped_column(String(320), default="")
    # The exact kind ("reminder", "change", "invite", "digest",
    # "rsvp.promoted"); its category drives preferences.
    kind: Mapped[str] = mapped_column(String(32), index=True)
    # Channels, decided at creation by preference (B-100).
    send_email: Mapped[bool] = mapped_column(Boolean, default=True)
    in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    # The Messenger channel (Phase 12, B-134): set at creation
    # only when the member opted in (channel switch on) AND
    # linked a Messenger identity for this group — there is no
    # road to a person the group cannot name over there.
    # ``messenger_status`` records what the channel did at
    # delivery: "" (not attempted / not yet), "sent",
    # "driver_unavailable" (B-125's access was never granted;
    # the other channels carried the load), "failed" (the
    # driver refused), or "unlinked" (the link was removed
    # between creation and delivery).
    send_messenger: Mapped[bool] = mapped_column(Boolean, default=False)
    messenger_status: Mapped[str] = mapped_column(String(32), default="")
    subject: Mapped[str] = mapped_column(String(240), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    # B-103: when the group's AI-wording tier rewrote the body,
    # the TEMPLATE's words are kept here verbatim — the facts
    # were template-guaranteed, and both versions are stored.
    # ``wording_source`` says which words ``body_text`` carries:
    # "template" (the normal case), "ai" (the rewrite passed the
    # fact check), or "template_fallback" (it did not, so the
    # template's words went out instead).
    template_body: Mapped[str] = mapped_column(Text, default="")
    wording_source: Mapped[str] = mapped_column(String(24), default="template")
    # Urgent is an ORGANISER's mark (a cancellation they send),
    # never the system's guess (B-100): urgent rows skip quiet
    # hours entirely.
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    # The earliest moment this may go out: for a reminder, the
    # reminder moment itself; for a held row, the quiet end.
    send_after: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # The notify job carrying this row (B-095's outbox): kept so
    # a superseding plan can cancel exactly the right job.
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # For reminders: which rule made this row ("days_before:7",
    # "morning_of") — the supersede sweep finds an event's old
    # reminders by (event_id, kind="reminder").
    reminder_rule: Mapped[str] = mapped_column(String(32), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The inbox's unread state (B-101): null until the person
    # marks the item read.
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class NotificationPreference(Base):
    """One switch (B-100): member × group × channel × category."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id", "channel", "kind_category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # "email" | "in_app" | "messenger"
    kind_category: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class MemberNotificationSettings(Base):
    """A member's quiet hours in one group (B-100).

    ``quiet_start`` / ``quiet_end`` are "HH:MM" wall-clock words
    in ``timezone`` (which defaults, at read time, to the user's
    own timezone, then the group's). An overnight window
    (22:00 → 07:00) is the normal shape; start == end means no
    quiet hours. NULL rows/fields = no quiet hours at all.
    """

    __tablename__ = "member_notification_settings"
    __table_args__ = (UniqueConstraint("user_id", "group_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    quiet_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    quiet_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class EventInvite(Base):
    """An invitation to one event (B-098).

    ``email`` is set for outside invitees; ``user_id`` for member
    invitations (which ALSO travel as a notification — this row
    is their shared record). The raw token is shown exactly
    once, in the create response and the emailed link; only its
    hash lives here.
    """

    __tablename__ = "event_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), default="")
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), default="")
    invited_by: Mapped[str] = mapped_column(String(80))  # audit label
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    # The RSVP the token's answer created, once answered.
    rsvp_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Digest(Base):
    """One composed weekly digest for one group (B-099).

    Both renderings are stored on the row: what was sent is
    exactly what an organiser can re-read later, and the
    frontend's digest preview reads the same row.
    """

    __tablename__ = "digests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    week_start: Mapped[datetime] = mapped_column(DateTime)
    week_end: Mapped[datetime] = mapped_column(DateTime)
    subject: Mapped[str] = mapped_column(String(240), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MailEvent(Base):
    """A mail provider's word about an address (B-102).

    Written by the webhook receiver (stubbed until the real
    provider lands in Phase 12): a bounce or a complaint naming
    an address, linked to the notification it concerned when
    the provider said which one it was.
    """

    __tablename__ = "mail_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(48), default="")
    email: Mapped[str] = mapped_column(String(320), index=True)
    event_type: Mapped[str] = mapped_column(String(24))  # "bounce" | "complaint"
    notification_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
