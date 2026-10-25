"""Integration tables (roadmap Phase 11, B-123 → B-128).

Phase 11 connects the assistant to where the group already
talks and where its plans must travel back to. The tables here
are the durable records of those connections:

- ``ExternalAccountLink`` — the identity bridge (B-123): a
  speaker in an outside system (a Samtal hall, a Messenger
  group) is a member of THIS group only because a person made
  a link row saying so. The link is keyed by the speaker's id
  in the outside system — NEVER by their display name. Two
  people can share a name; a name is evidence, not identity.
  A speaker with no link row is a named guest, full stop.
- ``LiveSyncState`` — one live source's intake cursor (B-123):
  where the poller stopped, when it last ran, and the last
  error in plain words. The cursor is data, so a restart never
  re-ingests or skips a message.
- ``Integration`` — the general integration record (B-127):
  one row per connected outside system per group, its config
  sealed (envelope-encrypted; services/sealing.py) — a
  database file on its own yields no usable credential — and
  its health: last check, last success, last error in plain
  words an admin can act on.
- ``WebhookEndpoint`` + ``WebhookReceipt`` — the generic
  inbound webhook door (B-126): the endpoint's URL token is
  stored hashed (the B-014 discipline); every accepted
  delivery's signature is receipted, so a replayed delivery
  is recognisable and refused.
- ``ExternalEventLink`` — an event's copy in an outside
  calendar (B-124): the outside id plus a hash of what was
  pushed, so a later sync can tell "our copy is current" from
  "we changed since" without asking the outside system what
  it thinks we said.
- ``OutboundWebhook`` + ``OutboundDelivery`` — the outbound
  door (B-128): registered endpoints subscribed to named
  group events, and one row per delivery attempt record —
  signed, retried by the job chassis, and inspectable: a
  delivery that failed says so, in words, forever.
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

# The connector kinds a live source or an account link may
# name. "samtal" is this team's own chat village (Phase 11's
# first bridge); "messenger" is the platform the roadmap names
# for B-125, whose live access was never granted — the
# registration record exists, the sync door answers with the
# fallback (services/messenger.py tells that story plainly).
CONNECTOR_KINDS: tuple[str, ...] = ("samtal", "messenger")

# The integration kinds the B-127 framework registers. Phase
# 12's slices (Facebook Events, Google Calendar) register
# under these names; until their slices land, their health
# check honestly reports "not connected yet".
INTEGRATION_KINDS: tuple[str, ...] = (
    "samtal",
    "facebook_events",
    "google_calendar",
    "custom",
)

# The outbound event kinds a webhook may subscribe to (B-128).
OUTBOUND_EVENT_KINDS: tuple[str, ...] = (
    "event.published",
    "event.changed",
    "event.cancelled",
    "candidate.pending",
)


class ExternalAccountLink(Base):
    """One outside identity, linked to one member — by a person.

    The link is the ONLY road from an outside speaker to a
    group member. It is created by an organiser naming the
    outside id and the member; nothing in this codebase ever
    creates one by matching names, and the B-123 test suite
    proves a same-named unlinked speaker stays a guest.
    """

    __tablename__ = "external_account_links"
    __table_args__ = (
        UniqueConstraint("group_id", "connector_kind", "external_user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    connector_kind: Mapped[str] = mapped_column(String(48), index=True)
    # The speaker's id in the outside system — the key. The
    # display name rides along as evidence (it is what the
    # outside system called them when the link was made), and
    # is never matched against anything.
    external_user_id: Mapped[str] = mapped_column(String(240))
    external_name: Mapped[str] = mapped_column(String(160), default="")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class LiveSyncState(Base):
    """One live source's intake state (B-123).

    ``cursor`` is the connector's own position marker (for
    Samtal: the id of the last message taken). ``last_error``
    is plain words — a sync that failed says why, here, where
    an organiser can read it, instead of failing silently.
    """

    __tablename__ = "live_sync_states"
    __table_args__ = (UniqueConstraint("source_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    cursor: Mapped[str] = mapped_column(String(240), default="")
    messages_seen: Mapped[int] = mapped_column(Integer, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class Integration(Base):
    """One group's connection to one outside system (B-127).

    ``config_sealed`` is the config JSON after envelope
    encryption (services/sealing.py) — tokens and keys inside
    it are never plaintext in the database. ``status`` is the
    health verdict in one word (unconfigured | ok | error);
    ``last_error`` is the plain-words sentence an admin acts
    on, never a stack trace.
    """

    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    config_sealed: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="unconfigured")
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class WebhookEndpoint(Base):
    """One generic inbound webhook door (B-126).

    Bound to ONE source: deliveries append their messages to
    it as chunks. The URL token is shown once and stored
    hashed (B-014 discipline). The HMAC signing secret is NOT
    stored at all — it is derived from the server secret and
    this row's id (services/webhooks_in.py), so the database
    holds nothing a signature could be forged with.
    """

    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), default="")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class WebhookReceipt(Base):
    """One accepted inbound delivery's signature (B-126).

    The replay guard: a signature is accepted exactly once
    per endpoint. A second arrival of the same signed payload
    finds its receipt and is refused — the window check alone
    cannot see a replay inside the window; this row can.
    """

    __tablename__ = "webhook_receipts"
    __table_args__ = (UniqueConstraint("endpoint_id", "signature"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True
    )
    signature: Mapped[str] = mapped_column(String(128), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ExternalEventLink(Base):
    """One event's copy in one outside calendar (B-124).

    ``payload_hash`` is the hash of the canonical payload last
    pushed; a sync compares the event's current payload hash
    against it to decide whether the outside copy is stale.
    The outside system's own edits never write through this
    row — they arrive as change candidates (B-077's road).
    """

    __tablename__ = "external_event_links"
    __table_args__ = (UniqueConstraint("event_id", "connector_kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    connector_kind: Mapped[str] = mapped_column(String(48), default="samtal")
    external_id: Mapped[str] = mapped_column(String(240), default="")
    # The outside copy's own address, when the outside system
    # has one (a Facebook event's URL, B-131): where a person
    # can go and LOOK at the copy. Samtal's hall calendar has
    # no per-event URL, so its rows leave this empty.
    external_url: Mapped[str] = mapped_column(String(1000), default="")
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class OutboundWebhook(Base):
    """One registered outbound endpoint (B-128).

    ``events_json`` is the subscribed subset of
    OUTBOUND_EVENT_KINDS. ``secret_sealed`` is the delivery
    signing secret, envelope-sealed (services/sealing.py);
    the plaintext is shown once at creation and never stored.
    """

    __tablename__ = "outbound_webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    secret_sealed: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class OutboundDelivery(Base):
    """One outbound delivery, inspectable forever (B-128).

    Written in the same transaction as the act that caused it
    (the outbox discipline, B-095); a ``webhook_deliver`` job
    carries it out. ``attempts`` counts actual POSTs;
    ``last_error`` is plain words; ``status`` walks
    queued → sent | failed → (retry) … → dead with its job.
    """

    __tablename__ = "outbound_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    webhook_id: Mapped[str] = mapped_column(
        ForeignKey("outbound_webhooks.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_kind: Mapped[str] = mapped_column(String(48), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
