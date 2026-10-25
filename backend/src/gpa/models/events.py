"""Event tables (roadmap Phase 4, B-049 → B-061).

An *event* is the group's shared truth about a moment in time —
the thing approval mints and every calendar surface reads. The
tables here are Doc 06 §6's events area:

- ``Event`` — the moment itself: title, description, start/end
  stored in **UTC** with the event's **IANA timezone** beside them
  (D-21: a datetime without a timezone is a bug), place, host,
  cost, bring list, access notes, and its bright-line ``status``:
  ``draft`` reaches **no** feed, digest, or integration — only
  ``published`` (and ``cancelled``, emitted *as* cancelled) travel.
- ``EventSourceLink`` — event × source, with the quote: the
  de-duplication record that keeps every surviving field
  answerable to the material it came from (Doc 06 §6).
- ``EventChange`` — one row per field change: field, old, new,
  who, when, why. Change history is complete and ordered (B-050);
  reminders read it later — events record, they never send.
- ``Recurrence`` — the series rule (an RRULE string) plus its
  per-occurrence exceptions as JSON. Occurrences are *computed*
  at read time, never stored (Doc 06 §6).
- ``CalendarFeed`` — a group's secret feed record (B-053):
  the token is stored **hashed** only; revocation is a timestamp,
  instant and total.
- ``Thread`` / ``Comment`` — discussion under an event (B-060).
  The parent is polymorphic (``parent_type`` / ``parent_id``) so
  Phase 7 generalises the same machinery to tasks, decisions,
  and polls without a schema change (Doc 04 §6).
- ``Rsvp`` — one answer per person per event (B-061): status
  (coming / cant / maybe), guests, note, and the waitlist state
  capacity forced on it.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc

# The event status ladder. Draft is a private working state;
# published is the bright line every outward surface checks;
# cancelled is terminal but VISIBLE — a cancelled event is
# announced as cancelled everywhere it was announced as live.
EVENT_STATUSES: tuple[str, ...] = ("draft", "published", "cancelled")

# RSVP answers (B-061). "cant" is stored in the roadmap's own
# spelling (Doc 06 §6) so the API and the data agree.
RSVP_STATUSES: tuple[str, ...] = ("coming", "cant", "maybe")


class Event(Base):
    """One moment the group has agreed is real (or is drafting)."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # The moment itself. Stored naive-UTC like every datetime in
    # this codebase (D-21), with ``timezone`` naming the IANA zone
    # the wall-clock belongs to — recurrence expansion and every
    # rendering convert through it, never through the server's.
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    # Where. Name is the human words ("the hall"); address is the
    # findable form; lat/lng exist when a geocode or a person has
    # pinned the place — the tonight view's "near-first" ordering
    # and travel judgements read exactly these two columns.
    place_name: Mapped[str] = mapped_column(String(240), default="")
    place_address: Mapped[str] = mapped_column(String(400), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Who hosts. ``host_label`` is the words the group uses ("Ana",
    # or the candidate draft's host field verbatim); ``host_id``
    # is the audit label ("user:<id>" / "seat:<id>") when a known
    # principal is the host. Both are member-only in projections.
    host_label: Mapped[str] = mapped_column(String(160), default="")
    host_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Money, two honest forms: the draft's own words ("£6 on the
    # door") and, when someone states it exactly, integer cents.
    cost_text: Mapped[str] = mapped_column(String(120), default="")
    cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bring_list: Mapped[str] = mapped_column(Text, default="")
    # Member-only practical notes (door codes, gate instructions).
    # NEVER in the public projection (B-059's leak suite guards it).
    access_notes: Mapped[str] = mapped_column(Text, default="")
    # Classification for calendar filters (B-055): a free ``type``
    # word ("social", "meeting") and the sub-group it belongs to
    # ("choir"), both the group's own vocabulary.
    event_type: Mapped[str] = mapped_column(String(48), default="")
    sub_group: Mapped[str] = mapped_column(String(80), default="")
    # The named sub-group this event is scoped to (Phase 9,
    # B-105): a real SubGroup row behind the vocabulary word
    # above — scoping is enforced against SubGroupMembership on
    # the member-facing surfaces. NULL = group-wide. Deleting a
    # sub-group that still scopes events is refused by the
    # service, so this reference never dangles.
    subgroup_id: Mapped[str | None] = mapped_column(
        ForeignKey("subgroups.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    # Set by material changes (B-050); cleared only by a person
    # re-announcing (later phases). Events never send anything
    # themselves — this flag is the whole of their part.
    needs_reannounce: Mapped[bool] = mapped_column(Boolean, default=False)
    # Room for people (B-061). NULL = no stated capacity.
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The flyer this event came from, when it has one (Phase 5
    # fills this on flyer-born events). Plain string, like the
    # candidate's source link: a deleted flyer must not take its
    # event down with it. The tonight view (B-056) reads the
    # flyer's image + alt text through this id.
    flyer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # The candidate whose approval minted this event (B-049);
    # NULL for direct organiser entry, which links its manual
    # source through EventSourceLink instead.
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    # Optimistic-concurrency version (D-15): every successful
    # PATCH increments it; the ETag is "v<version>" and a stale
    # If-Match is a 409, never a silent overwrite.
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Cancellation record (B-051): the reason in the canceller's
    # words and when it happened. The row stays; the status says
    # it all.
    cancelled_reason: Mapped[str] = mapped_column(Text, default="")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The tombstone stamp (Phase 6, B-080): a deleted event keeps
    # its row for 30 days, hidden from every surface, so an
    # organiser's undelete restores it whole — links, history,
    # RSVPs and all. After the window a purge removes the row
    # for good (services/tombstones.py owns both ends).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class EventSourceLink(Base):
    """One supporting source of one event, with its quote (B-049).

    Written at mint time from the candidate's provenance (and, for
    direct entry, from the manual source the entry records). The
    ``source_id`` is a plain string on purpose — the B-023
    tombstone law: deleting a source never deletes the event it
    supports; the link keeps the quote it carried.
    """

    __tablename__ = "event_source_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    # Which field this quote supports ("title", "starts", …);
    # empty for a whole-event link (the manual-entry source).
    field: Mapped[str] = mapped_column(String(64), default="")
    quote: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class EventContradiction(Base):
    """A disagreement an event carries until a person resolves
    it (Phase 6, B-079).

    Two sources said different things about one field — 7pm
    versus 8pm — and neither superseded the other. The event
    keeps the value it has and carries this row: the field, and
    every competing value with the sources that said it
    (``values_json``: ``[{value, source_ids, current}]``, values
    in the change-history string form). Silence is the only
    forbidden outcome — a disagreement is displayed, never
    averaged away. Resolution is a person's act, recorded with
    who chose, when, and the value chosen.
    """

    __tablename__ = "event_contradictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    # The EVENT field in dispute ("starts_at", "place_name", …).
    field: Mapped[str] = mapped_column(String(64))
    values_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The chosen value (change-history string form) + any note.
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AnnouncementDraft(Base):
    """A drafted re-announcement (Phase 6, B-078).

    For a materially changed event: what changed, old → new, in
    plain words a group member can read cold. Drafting only —
    approving and sending belong to Phase 10's channels; this
    row is the artefact those phases approve. ``changes_json``
    lists the EventChange ids the draft was built from, so what
    it announced is auditable against the history it read.
    """

    __tablename__ = "announcement_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    changes_json: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    # The sending record (Phase 8, B-097): an organiser approves
    # the draft and the change alerts go out; only then are
    # these set, and the event's needs_reannounce flag clears —
    # the group HAS been told. Until then a draft is just paper.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class EventChange(Base):
    """One field's change on one event (B-050).

    Values are stored in their plain string form (datetimes as
    ISO-8601 UTC) so the change feed is renderable by any client
    without field-type knowledge. Rows are append-only and read
    back in (created_at, id) order — the feed is the event's
    memory of itself.
    """

    __tablename__ = "event_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    changed_by: Mapped[str] = mapped_column(String(80))  # audit label
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Recurrence(Base):
    """A series' rule and its exceptions (B-052).

    One row per recurring event. ``rrule`` is the stored rule
    (RFC 5545 subset — services/recurrence.py owns the grammar);
    ``exceptions_json`` is a list of per-occurrence overrides:
    ``{"original_start": <local ISO>, "moved_to": <local ISO>|null,
    "cancelled": bool}`` — "this week's meeting moved to Thursday"
    is data here, never an edit to the rule.
    """

    __tablename__ = "recurrences"
    __table_args__ = (UniqueConstraint("event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    rrule: Mapped[str] = mapped_column(Text, default="")
    exceptions_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class CalendarFeed(Base):
    """A group's secret calendar-feed token (B-053).

    One row per issued feed. The raw token is shown to the issuer
    exactly once and stored only as a hash (the B-014 discipline);
    ``token_prefix`` is the safe-to-show fragment. ``filters_json``
    narrows what the feed carries (statuses, a place word) — the
    filter is part of the issued feed, so a leaked URL leaks only
    what it was scoped to. Revocation sets ``revoked_at`` and is
    instant and total: the feed route checks it on every fetch.
    """

    __tablename__ = "calendar_feeds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), default="")
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Thread(Base):
    """A discussion thread under something (B-060).

    Polymorphic on purpose (Doc 04 §6): ``parent_type`` names the
    owning module's thing ("event" in this phase) and
    ``parent_id`` its row. Phase 7 points the same tables at
    tasks, decisions, and polls.
    """

    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    parent_type: Mapped[str] = mapped_column(String(32), default="event")
    parent_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Comment(Base):
    """One message in a thread (B-060).

    Authorship is a label plus the principal ids behind it — a
    seat's words are the seat's (B-018), and edit/delete rights
    compare against these ids, never against display names.
    Moderation is state, not erasure: a hidden comment keeps its
    body for organisers and the audit trail, and shows as absent
    to everyone else; an author's own delete is a tombstone
    (``deleted_at`` set, body cleared) so a thread's shape — and
    any reply's context — survives the deletion.
    """

    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    author_label: Mapped[str] = mapped_column(String(80))
    author_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_seat_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hidden_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Rsvp(Base):
    """One person's answer to one event (B-061).

    Unique per (event, principal): re-answering updates the row,
    it never stacks a second one. ``waitlisted`` is capacity's
    verdict, recomputed by the service whenever the coming-set
    changes; ``waitlist_position`` is the queue order (arrival
    order among the waitlisted), kept explicit so promotion order
    is data, not a sort the reader must trust.
    """

    __tablename__ = "rsvps"
    __table_args__ = (UniqueConstraint("event_id", "principal_label"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    # The answering principal, as an audit label — people and
    # seats answer in their own names (D-35).
    principal_label: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seat_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="maybe")
    guests: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    waitlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    waitlist_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )
