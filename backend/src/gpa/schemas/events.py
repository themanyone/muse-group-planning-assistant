"""Event / calendar schemas (roadmap Phase 4).

The shapes here are the frontend's calendar contract. Two
disciplines run through the file:

- **Projections are built here, server-side** (B-059): one event,
  three levels — ``public`` (what an unauthenticated public page
  may see), ``member`` (the group's own view), ``organiser``
  (plus the working state: version, needs_reannounce, the
  minting candidate). Member-only fields (access notes, host,
  cost, bring lists) simply do not exist in a public projection —
  absence, not redaction, is the leak-proofing.
- **Datetimes on the wire are ISO-8601 with their offset**:
  stored UTC instants render with a ``Z``; occurrence times
  render in the event's own timezone with its true offset, so
  the frontend does zero date maths (B-055).
"""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # annotations only
    from gpa.models.events import Event, EventChange, Rsvp


def iso_utc(value: datetime | None) -> str | None:
    """A stored UTC-naive datetime → its wire form (``…Z``)."""
    if value is None:
        return None
    return value.isoformat() + "Z"


def to_utc_naive(value: datetime) -> datetime:
    """A wire datetime → the stored convention (UTC, naive).

    Aware values convert through their offset; naive values are
    read as UTC already (the API's documented convention)."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# ------------------------------------------------------------------
# Requests
# ------------------------------------------------------------------


class EventCreateRequest(BaseModel):
    """Direct organiser entry (B-049). Times are ISO-8601; the
    router converts them to stored UTC before the service runs."""

    title: str = Field(min_length=1, max_length=240)
    description: str = ""
    starts_at: datetime
    ends_at: datetime
    timezone: str | None = None
    all_day: bool = False
    place_name: str = ""
    place_address: str = ""
    lat: float | None = None
    lng: float | None = None
    host_label: str = ""
    host_id: str | None = None
    cost_text: str = ""
    cost_cents: int | None = None
    bring_list: str = ""
    access_notes: str = ""
    event_type: str = ""
    sub_group: str = ""
    # The named sub-group this event is scoped to (B-105); the
    # service stamps the display word from its name when no
    # explicit sub_group words were given.
    subgroup_id: str | None = None
    capacity: int | None = Field(default=None, ge=1)
    status: str = "published"  # draft | published (service validates)


class EventPatchRequest(BaseModel):
    """An event edit (B-050). Every field optional; the router
    passes only the fields actually sent (exclude_unset) — an
    absent field is untouched, an explicit value is a change."""

    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: str | None = None
    all_day: bool | None = None
    place_name: str | None = None
    place_address: str | None = None
    lat: float | None = None
    lng: float | None = None
    host_label: str | None = None
    host_id: str | None = None
    cost_text: str | None = None
    cost_cents: int | None = None
    bring_list: str | None = None
    access_notes: str | None = None
    event_type: str | None = None
    sub_group: str | None = None
    subgroup_id: str | None = None
    capacity: int | None = Field(default=None, ge=1)
    reason: str = ""  # carried into every change row of this patch


class CancelRequest(BaseModel):
    """Cancellation carries the reason in the canceller's words."""

    reason: str = ""


class ResolveContradictionRequest(BaseModel):
    """Settle a contradiction (B-079): the chosen value must be
    one the sources actually said (the service checks); ``note``
    rides into the change history as the reason."""

    value: str = Field(min_length=1)
    note: str = ""


class AnnouncementChangeView(BaseModel):
    """One line of a re-announce draft (B-078), in the humanised
    words the draft itself carries."""

    field: str
    old: str
    new: str
    change_id: str


class AnnouncementDraftResponse(BaseModel):
    """A drafted re-announcement (B-078): the body names old →
    new for every material change it covers. ``sent_at`` /
    ``sent_by`` record the approval that sent it as change
    alerts (Phase 8, B-097); null until then."""

    id: str
    event_id: str
    group_id: str
    body: str
    changes: list[AnnouncementChangeView]
    created_by: str
    created_at: datetime
    sent_at: datetime | None = None
    sent_by: str | None = None


def announcement_draft_view(draft) -> AnnouncementDraftResponse:
    try:
        changes = json.loads(draft.changes_json or "[]")
    except json.JSONDecodeError:
        changes = []
    return AnnouncementDraftResponse(
        id=draft.id,
        event_id=draft.event_id,
        group_id=draft.group_id,
        body=draft.body,
        changes=[AnnouncementChangeView(**row) for row in changes],
        created_by=draft.created_by,
        created_at=draft.created_at,
        sent_at=draft.sent_at,
        sent_by=draft.sent_by,
    )


class RecurrenceRequest(BaseModel):
    """Set/replace a series rule (B-052). ``exceptions`` replaces
    the whole exception list; omit it to keep the existing ones."""

    rrule: str = Field(min_length=1)
    exceptions: list[dict] | None = None


class ExceptionRequest(BaseModel):
    """One per-occurrence override: this happening moved, or is
    cancelled where it stood. Times are LOCAL ISO in the event's
    timezone (the wall clock the group thinks in)."""

    original_start: str = Field(min_length=1)
    moved_to: str | None = None
    cancelled: bool = False


# ------------------------------------------------------------------
# Responses
# ------------------------------------------------------------------


class FlyerView(BaseModel):
    """A flyer's public face on an event: the picture and its
    description state. An undescribed flyer says so — the alt
    text law (B-065) makes the defect visible, never silent. A
    generated image says so too (B-067): ``generated`` rides
    every projection because it is a column on the flyer, not a
    label a projection could forget."""

    flyer_id: str
    thumbnail_key: str = ""
    alt_text: str = ""
    alt_text_status: str = ""
    generated: bool = False


class ContradictionValueView(BaseModel):
    """One side of a contradiction (B-079): the value (in the
    change-history string form), the sources that said it, and
    whether it is the value the event currently carries."""

    value: str
    source_ids: list[str] = Field(default_factory=list)
    current: bool = False


class ContradictionResponse(BaseModel):
    """A disagreement an event carries (B-079) — visible until
    a person resolves it; resolved rows keep who chose and
    what they chose."""

    id: str
    event_id: str
    field: str
    values: list[ContradictionValueView] = Field(default_factory=list)
    status: str
    resolved_by: str | None = None
    resolution: str = ""
    created_at: str


class EventSourceLinkResponse(BaseModel):
    """One supporting source of an event, with its quote
    (B-076's answerable provenance): "where did this field
    come from?" is this list."""

    source_id: str
    field: str = ""
    quote: str = ""


class EventResponse(BaseModel):
    """One event at one projection level (B-059). Fields above a
    level's line are simply absent (None / default) below it."""

    id: str
    group_id: str
    title: str
    description: str = ""
    starts_at: str
    ends_at: str
    timezone: str
    all_day: bool = False
    place_name: str = ""
    place_address: str = ""
    status: str
    flyer: FlyerView | None = None
    # member level and up
    host_label: str | None = None
    cost_text: str | None = None
    cost_cents: int | None = None
    bring_list: str | None = None
    access_notes: str | None = None
    event_type: str | None = None
    sub_group: str | None = None
    subgroup_id: str | None = None
    capacity: int | None = None
    lat: float | None = None
    lng: float | None = None
    rsvp_summary: dict | None = None
    # organiser level
    etag: str | None = None
    needs_reannounce: bool | None = None
    created_by: str | None = None
    candidate_id: str | None = None
    cancelled_reason: str | None = None
    links: dict | None = None
    # Open contradictions this event carries (B-079), attached
    # by the detail route at organiser level — a disagreement
    # is part of the event's face until a person resolves it.
    contradictions: list[ContradictionResponse] | None = None


class EventChangeResponse(BaseModel):
    """One row of an event's change history (B-050)."""

    id: str
    event_id: str
    field: str
    old_value: str
    new_value: str
    changed_by: str
    reason: str = ""
    created_at: str


class RecurrenceResponse(BaseModel):
    """A series rule as stored, with its exceptions (B-052)."""

    event_id: str
    rrule: str
    exceptions: list[dict] = Field(default_factory=list)


class OccurrenceResponse(BaseModel):
    """One concrete happening (B-052/B-055): the event's identity
    fields plus THIS occurrence's times — rendered in the event's
    timezone with true offsets, UTC alongside. A frontend never
    expands a rule itself."""

    event_id: str
    title: str
    status: str  # the event's status; cancelled occurrence says so too
    occurrence_start: str  # local ISO with offset
    occurrence_end: str
    starts_at: str  # UTC ISO (Z)
    ends_at: str
    timezone: str
    all_day: bool = False
    place_name: str = ""
    is_exception: bool = False
    occurrence_cancelled: bool = False
    needs_reannounce: bool = False
    flyer: FlyerView | None = None


def project_event(
    event: "Event",
    level: str,
    *,
    flyer: FlyerView | None = None,
    rsvp_summary: dict | None = None,
    links: dict | None = None,
) -> EventResponse:
    """Build one event's response at a projection level (B-059).

    The level decides which fields EXIST in the response; the
    caller decides the level from the caller's role — public for
    the unauthenticated public routes, member for the group's
    own reads, organiser for organiser+ callers."""
    base = dict(
        id=event.id,
        group_id=event.group_id,
        title=event.title,
        description=event.description,
        starts_at=iso_utc(event.starts_at),
        ends_at=iso_utc(event.ends_at),
        timezone=event.timezone,
        all_day=event.all_day,
        place_name=event.place_name,
        place_address=event.place_address,
        status=event.status,
        flyer=flyer,
    )
    if level == "public":
        return EventResponse(**base)
    base.update(
        host_label=event.host_label,
        cost_text=event.cost_text,
        cost_cents=event.cost_cents,
        bring_list=event.bring_list,
        access_notes=event.access_notes,
        event_type=event.event_type,
        sub_group=event.sub_group,
        subgroup_id=event.subgroup_id,
        capacity=event.capacity,
        lat=event.lat,
        lng=event.lng,
        rsvp_summary=rsvp_summary,
    )
    if level == "member":
        return EventResponse(**base)
    from gpa.models.events import etag_of

    base.update(
        etag=etag_of(event),
        needs_reannounce=event.needs_reannounce,
        created_by=event.created_by,
        candidate_id=event.candidate_id,
        cancelled_reason=event.cancelled_reason or None,
        links=links,
    )
    return EventResponse(**base)


def occurrence_view(event: "Event", occ, *, flyer: FlyerView | None = None) -> OccurrenceResponse:
    """One expanded occurrence → its wire shape (B-052/B-055).

    Local times render in the EVENT's timezone with the offset
    that was true on that date (EDT vs EST is the zone's answer,
    computed per occurrence); UTC rides alongside with its Z."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(event.timezone)
    return OccurrenceResponse(
        event_id=event.id,
        title=event.title,
        status=event.status,
        occurrence_start=occ.start_local.replace(tzinfo=tz).isoformat(),
        occurrence_end=occ.end_local.replace(tzinfo=tz).isoformat(),
        starts_at=iso_utc(occ.starts_at),
        ends_at=iso_utc(occ.ends_at),
        timezone=event.timezone,
        all_day=event.all_day,
        place_name=event.place_name,
        is_exception=occ.is_exception,
        occurrence_cancelled=occ.cancelled,
        needs_reannounce=event.needs_reannounce,
        flyer=flyer,
    )


class CommentCreateRequest(BaseModel):
    """Post to an event's thread (B-060)."""

    body: str = Field(min_length=1, max_length=4000)


class CommentEditRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class CommentResponse(BaseModel):
    """One comment as a viewer at some level sees it: hidden and
    deleted are organiser-visible flags; to members a hidden or
    deleted comment is simply absent from the list (the service
    filters it before this shape is built).

    The parent is named honestly since B-084 generalised the
    thread system: ``parent_type`` + ``parent_id`` always;
    ``event_id`` remains, populated for event parents (the
    B-060 shape), null for task/decision/poll parents."""

    id: str
    thread_id: str
    event_id: str | None = None
    parent_type: str = "event"
    parent_id: str = ""
    author_label: str
    body: str
    edited_at: str | None = None
    hidden: bool = False
    deleted: bool = False
    created_at: str


def comment_view(comment, thread) -> CommentResponse:
    """One comment row → its wire shape."""
    return CommentResponse(
        id=comment.id,
        thread_id=comment.thread_id,
        event_id=thread.parent_id if thread.parent_type == "event" else None,
        parent_type=thread.parent_type,
        parent_id=thread.parent_id,
        author_label=comment.author_label,
        body=comment.body,
        edited_at=iso_utc(comment.edited_at),
        hidden=comment.hidden_at is not None,
        deleted=comment.deleted_at is not None,
        created_at=iso_utc(comment.created_at),
    )


class RsvpRequest(BaseModel):
    """The caller's own answer (B-061): an upsert — answering
    again updates the same row, it never stacks a second one."""

    status: str  # coming | cant | maybe (service validates)
    guests: int = Field(default=0, ge=0)
    note: str = ""


class RsvpResponse(BaseModel):
    id: str
    event_id: str
    principal_label: str
    status: str
    guests: int
    note: str = ""
    waitlisted: bool = False
    waitlist_position: int | None = None
    updated_at: str


class RsvpListResponse(BaseModel):
    """Every answer plus the summary counts (B-061)."""

    rsvps: list[RsvpResponse]
    summary: dict


def rsvp_view(row: "Rsvp") -> RsvpResponse:
    """One RSVP row → its wire shape."""
    return RsvpResponse(
        id=row.id,
        event_id=row.event_id,
        principal_label=row.principal_label,
        status=row.status,
        guests=row.guests,
        note=row.note,
        waitlisted=row.waitlisted,
        waitlist_position=row.waitlist_position,
        updated_at=iso_utc(row.updated_at),
    )


def contradiction_view(row) -> ContradictionResponse:
    """One contradiction row → its wire shape (B-079)."""
    import json

    try:
        values = json.loads(row.values_json or "[]")
    except json.JSONDecodeError:
        values = []
    return ContradictionResponse(
        id=row.id,
        event_id=row.event_id,
        field=row.field,
        values=[
            ContradictionValueView(
                value=str(entry.get("value", "")),
                source_ids=[str(s) for s in entry.get("source_ids", [])],
                current=bool(entry.get("current")),
            )
            for entry in values
            if isinstance(entry, dict)
        ],
        status=row.status,
        resolved_by=row.resolved_by,
        resolution=row.resolution,
        created_at=iso_utc(row.created_at),
    )


def source_link_view(link) -> EventSourceLinkResponse:
    """One event-source link → its wire shape (B-076)."""
    return EventSourceLinkResponse(
        source_id=link.source_id, field=link.field, quote=link.quote
    )


def change_row(change: "EventChange") -> EventChangeResponse:
    """One change-history row → its wire shape."""
    return EventChangeResponse(
        id=change.id,
        event_id=change.event_id,
        field=change.field,
        old_value=change.old_value,
        new_value=change.new_value,
        changed_by=change.changed_by,
        reason=change.reason,
        created_at=iso_utc(change.created_at),
    )
