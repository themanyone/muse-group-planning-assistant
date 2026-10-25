"""Events: minting, direct entry, editing, cancellation (B-049 →
B-051).

An event is born exactly two ways (Doc 04 §6):

- **By approval** — ``mint_from_candidate`` is called by the
  review queue's approve path for event-kind candidates, in the
  approval's own transaction. Approval IS the publish decision,
  so a minted event is born ``published`` and the candidate's
  ``resulting_entity_*`` finally fills in (Phase 3 left it NULL
  on purpose, waiting for this module).
- **By direct organiser entry** — an organiser types the event
  in. That act records a *manual source* first: the entry is
  material too, with the organiser as its provenance, and the
  event links to it like any sourced event.

Editing is optimistic-concurrency PATCH (D-15): the client sends
the ``ETag`` it read as ``If-Match``; a stale match is a 409
``stale_version`` naming the current version — never a silent
overwrite of a co-organiser's work. **Every** field change writes
an ``EventChange`` row in the same transaction; material changes
(time, place, timezone, all-day) also set ``needs_reannounce``.
Events record changes and set that flag — sending the
announcement is reminders' work (a later phase); events never
send anything themselves.
"""

import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import (
    AnnouncementDraft,
    Comment,
    Event,
    EventChange,
    EventContradiction,
    EventSourceLink,
    Recurrence,
    Rsvp,
    Thread,
)
from gpa.models.groups import Candidate, Group, role_at_least
from gpa.models.sources import Provenance, SourceChunk
from gpa.services import audit
from gpa.services import sources as source_service

# Fields a PATCH may change. Anything else on the event (status,
# version, the cancellation record) moves only through its own
# named operation — publish, cancel — so no client can smuggle a
# state change past the change-history law.
EDITABLE_FIELDS: tuple[str, ...] = (
    "title", "description", "starts_at", "ends_at", "timezone",
    "all_day", "place_name", "place_address", "lat", "lng",
    "host_label", "host_id", "cost_text", "cost_cents",
    "bring_list", "access_notes", "event_type", "sub_group",
    "subgroup_id", "capacity",
)

# Changes the group must hear about again (B-050): when, where.
MATERIAL_FIELDS: frozenset[str] = frozenset(
    {"starts_at", "ends_at", "timezone", "all_day", "place_name", "place_address"}
)


def etag_of(event: Event) -> str:
    """The event's ETag: its version, in the wire form ``"v<n>"``."""
    return f'"v{event.version}"'


def _stringify(value) -> str:
    """A field value's change-history form: datetimes as ISO-8601
    UTC (the stored convention), booleans as words, None as empty."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def record_change(
    db: Session,
    *,
    event: Event,
    field: str,
    old_value,
    new_value,
    actor_label: str,
    reason: str = "",
) -> EventChange:
    """Append one change row inside the caller's transaction."""
    row = EventChange(
        event_id=event.id,
        field=field,
        old_value=_stringify(old_value),
        new_value=_stringify(new_value),
        changed_by=actor_label,
        reason=reason,
    )
    db.add(row)
    return row


def hard_delete_event(db: Session, event: Event) -> None:
    """Remove an event and everything it carries, for good.

    Only the tombstone purge calls this (B-080), after the
    30-day restore window has closed: children first (comments
    through their threads, then the event's own rows), the
    event last. Does not commit — the purge's commit carries
    the whole sweep.
    """
    thread_ids = [
        row.id
        for row in db.query(Thread)
        .filter(Thread.parent_type == "event", Thread.parent_id == event.id)
        .all()
    ]
    if thread_ids:
        db.query(Comment).filter(Comment.thread_id.in_(thread_ids)).delete(
            synchronize_session=False
        )
        db.query(Thread).filter(Thread.id.in_(thread_ids)).delete(
            synchronize_session=False
        )
    for model in (
        Rsvp, Recurrence, EventChange, EventSourceLink,
        EventContradiction, AnnouncementDraft,
    ):
        db.query(model).filter(model.event_id == event.id).delete(
            synchronize_session=False
        )
    db.delete(event)
    db.flush()


def get_event(
    db: Session, group: Group, event_id: str, *, include_deleted: bool = False
) -> Event:
    """One event of this group, or 404 (another group's event is
    indistinguishable from a missing one). A tombstoned event
    (B-080) is a 404 too, unless the caller is the tombstone
    service itself restoring or purging it."""
    event = db.get(Event, event_id)
    if event is None or event.group_id != group.id:
        raise AppError("not_found", detail="That event could not be found.")
    if event.deleted_at is not None and not include_deleted:
        raise AppError("not_found", detail="That event could not be found.")
    return event


def visible_to(event: Event, role: str | None) -> bool:
    """The bright line, as a predicate (B-049): drafts are visible
    to organisers and above only; everyone else must not be able
    to tell a draft exists. Published and cancelled are visible to
    every member."""
    if event.status == "draft":
        return role is not None and role_at_least(role, "organiser")
    return role is not None


# The "no viewer named" sentinel for the sub-group scope check
# (B-105): internal callers (jobs, minting, merges) read events
# without a viewer and skip the check; routers always name their
# viewer — a person id, or None for a seat — so a scoped event a
# caller may not see is a 404, exactly like a draft.
_SCOPE_UNSET = object()


def _assert_subgroup_visible(
    db: Session, group: Group, event: Event, *, viewer_user_id, role: str | None
) -> None:
    """The B-105 rule for one event: scoped + not enrolled +
    not organiser = the event does not exist for this viewer."""
    if event.subgroup_id is None:
        return
    from gpa.services import subgroups as subgroup_service

    visible_ids = subgroup_service.visible_subgroup_ids(
        db, group, user_id=viewer_user_id, role=role
    )
    if not subgroup_service.can_see_scoped(event.subgroup_id, visible_ids):
        raise AppError("not_found", detail="That event could not be found.")


def get_visible_event(
    db: Session,
    group: Group,
    event_id: str,
    role: str | None,
    *,
    viewer_user_id=_SCOPE_UNSET,
) -> Event:
    """``get_event`` plus the bright line: a draft asked for by a
    non-organiser is a 404, exactly as if it did not exist. When
    a viewer is named, the B-105 sub-group scope applies the
    same way: a scoped event outside the viewer's sub-groups is
    a 404 too."""
    event = get_event(db, group, event_id)
    if not visible_to(event, role):
        raise AppError("not_found", detail="That event could not be found.")
    if viewer_user_id is not _SCOPE_UNSET:
        _assert_subgroup_visible(
            db, group, event, viewer_user_id=viewer_user_id, role=role
        )
    return event


def list_events(
    db: Session,
    group: Group,
    *,
    role: str | None,
    status: str | None = None,
    viewer_user_id=_SCOPE_UNSET,
) -> list[Event]:
    """The group's events, soonest first. Drafts ride along only
    for organisers (the bright line again); a ``status`` filter
    narrows further but can never widen visibility. When a
    viewer is named, sub-group-scoped events outside the
    viewer's enrolments never reach the list (B-105)."""
    query = db.query(Event).filter(
        Event.group_id == group.id, Event.deleted_at.is_(None)
    )
    rows = query.order_by(Event.starts_at, Event.id).all()
    visible_ids = None
    if viewer_user_id is not _SCOPE_UNSET:
        from gpa.services import subgroups as subgroup_service

        visible_ids = subgroup_service.visible_subgroup_ids(
            db, group, user_id=viewer_user_id, role=role
        )
    out = []
    for event in rows:
        if not visible_to(event, role):
            continue
        if viewer_user_id is not _SCOPE_UNSET and not subgroup_service.can_see_scoped(
            event.subgroup_id, visible_ids
        ):
            continue
        if status and event.status != status:
            continue
        out.append(event)
    return out


# ------------------------------------------------------------------
# Minting (B-049)
# ------------------------------------------------------------------


def _draft_field(draft: dict, name: str):
    """A draft field's value: the top-level mirror first (the
    shape extraction writes), then the fields map's value."""
    if draft.get(name) is not None:
        return draft[name]
    fields = draft.get("fields", {})
    entry = fields.get(name, {}) if isinstance(fields, dict) else {}
    return entry.get("value") if isinstance(entry, dict) else None


def _draft_local_start(draft: dict) -> tuple[datetime | None, bool]:
    """The draft's start as a LOCAL naive datetime, plus whether
    it is date-only (→ an all-day event). Reads, in order: the
    ``starts_at`` mirror (date + resolved time), ``starts_date``,
    then the fields map's resolved date/time. (None, False) when
    the draft never resolved a start — minting refuses those."""
    mirror = draft.get("starts_at")
    if isinstance(mirror, str) and "T" in mirror:
        try:
            return datetime.fromisoformat(mirror), False
        except ValueError:
            pass
    date_text = draft.get("starts_date")
    fields = draft.get("fields", {})
    starts_entry = fields.get("starts", {}) if isinstance(fields, dict) else {}
    if not date_text and isinstance(starts_entry, dict):
        date_text = starts_entry.get("resolved_date")
    if not date_text:
        return None, False
    try:
        day = datetime.fromisoformat(str(date_text)).date()
    except ValueError:
        return None, False
    time_text = None
    if isinstance(starts_entry, dict):
        time_text = starts_entry.get("resolved_time")
    if time_text:
        try:
            return datetime.combine(day, time.fromisoformat(str(time_text))), False
        except ValueError:
            pass
    return datetime.combine(day, time(0, 0)), True


def _draft_local_end(draft: dict, start_local: datetime, all_day: bool) -> datetime:
    """The draft's end, or the standing default when the source
    named none: a day for all-day events, two hours for timed
    ones (recorded as a decision in the memory map — a source
    that states an end always wins over the default)."""
    mirror = draft.get("ends_at")
    if isinstance(mirror, str) and "T" in mirror:
        try:
            return datetime.fromisoformat(mirror)
        except ValueError:
            pass
    date_text = draft.get("ends_date")
    fields = draft.get("fields", {})
    ends_entry = fields.get("ends", {}) if isinstance(fields, dict) else {}
    if not date_text and isinstance(ends_entry, dict):
        date_text = ends_entry.get("resolved_date")
    if date_text:
        try:
            day = datetime.fromisoformat(str(date_text)).date()
        except ValueError:
            day = None
        if day is not None:
            time_text = ends_entry.get("resolved_time") if isinstance(ends_entry, dict) else None
            if time_text:
                try:
                    return datetime.combine(day, time.fromisoformat(str(time_text)))
                except ValueError:
                    pass
            return datetime.combine(day, time(0, 0)) + (timedelta(days=1) if all_day else timedelta(hours=2))
    return start_local + (timedelta(days=1) if all_day else timedelta(hours=2))


def _local_to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    """One local wall time → its UTC instant (naive, D-21)."""
    return local_naive.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def mint_from_candidate(
    db: Session, *, group: Group, candidate: Candidate, actor_label: str
) -> Event | None:
    """Mint the entity an approved candidate becomes (B-049).

    Event-kind candidates mint an Event, born published — the
    approval was the publish decision. Other kinds return None:
    their entities belong to later phases (tasks/decisions/
    questions, Phase 7), and approval for them stays a recorded
    decision only, exactly as Phase 3 stated. Called inside the
    approval's transaction; this function never commits.
    """
    if candidate.kind != "event":
        return None
    try:
        draft = json.loads(candidate.draft_json or "{}")
    except json.JSONDecodeError:
        draft = {}
    if not isinstance(draft, dict):
        draft = {}
    start_local, all_day = _draft_local_start(draft)
    if start_local is None:
        raise AppError(
            "validation_error",
            detail=(
                "That draft has no resolved start date, so it cannot "
                "become an event yet — edit the draft to give it a date first."
            ),
        )
    tz = ZoneInfo(group.home_timezone or "UTC")
    end_local = _draft_local_end(draft, start_local, all_day)
    event = Event(
        group_id=group.id,
        title=str(_draft_field(draft, "title") or candidate.title or "Untitled event")[:240],
        description=str(_draft_field(draft, "description") or ""),
        starts_at=_local_to_utc(start_local, tz),
        ends_at=_local_to_utc(end_local, tz),
        timezone=group.home_timezone or "UTC",
        all_day=all_day,
        place_name=str(_draft_field(draft, "place") or "")[:240],
        host_label=str(_draft_field(draft, "host") or "")[:160],
        cost_text=str(_draft_field(draft, "cost") or "")[:120],
        bring_list=str(_draft_field(draft, "bring") or ""),
        status="published",
        candidate_id=candidate.id,
        created_by=actor_label,
    )
    db.add(event)
    db.flush()  # the event id must exist before links point at it
    # The supporting sources ride along: one link per provenance
    # row (field + quote), so the published event can always
    # answer "where did this come from?" When the source was
    # deleted first, its provenance went with it (B-023) — the
    # candidate's tombstone link is then the whole record.
    provenance_rows = (
        db.query(Provenance).filter(Provenance.candidate_id == candidate.id).all()
    )
    linked_sources: set[str] = set()
    for row in provenance_rows:
        chunk = db.get(SourceChunk, row.chunk_id)
        if chunk is None:
            continue
        db.add(
            EventSourceLink(
                event_id=event.id, source_id=chunk.source_id,
                field=row.field, quote=row.quote,
            )
        )
        linked_sources.add(chunk.source_id)
    if candidate.source_id and candidate.source_id not in linked_sources:
        db.add(
            EventSourceLink(
                event_id=event.id, source_id=candidate.source_id, field="", quote=""
            )
        )
    # A flyer-born event carries its poster (Phase 5, B-063):
    # when the candidate's source is a flyer, the event's flyer
    # link is set HERE, at mint — Phase 4's gate set it by hand as
    # an honest stand-in; the vision pipeline makes it real.
    if candidate.source_id:
        from gpa.services import images as image_service

        flyer = image_service.get_flyer_for_source(db, candidate.source_id)
        if flyer is not None:
            event.flyer_id = flyer.id
    candidate.resulting_entity_type = "event"
    candidate.resulting_entity_id = event.id
    audit.record(
        db, actor=actor_label, action="event.minted",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"candidate_id": candidate.id},
    )
    return event


# ------------------------------------------------------------------
# Direct organiser entry (B-049)
# ------------------------------------------------------------------


def create_event(
    db: Session, *, actor_label: str, group: Group, data: dict
) -> Event:
    """Direct organiser entry: record the manual source, then the
    event it supports — the entry is material with a person as
    its provenance, so a hand-typed event answers "where did
    this come from?" exactly like an extracted one.

    ``data`` is the validated create payload; starts_at/ends_at
    arrive as UTC-naive datetimes (the schema layer owns the
    wire→UTC conversion)."""
    if data.get("status", "published") not in ("draft", "published"):
        raise AppError(
            "validation_error",
            detail="A new event's status is 'draft' or 'published'.",
        )
    if data["ends_at"] <= data["starts_at"]:
        raise AppError(
            "validation_error",
            detail="An event must end after it starts.",
        )
    starts_at: datetime = data["starts_at"]
    ends_at: datetime = data["ends_at"]
    # Sub-group scope (B-105): the named sub-group must be one of
    # this group's; the event then carries its id, and its name
    # becomes the display word unless the caller gave their own.
    subgroup = None
    if data.get("subgroup_id"):
        from gpa.services import subgroups as subgroup_service

        subgroup = subgroup_service.get_subgroup(db, group, data["subgroup_id"])
    tz = ZoneInfo(data.get("timezone") or group.home_timezone or "UTC")
    start_local = starts_at.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
    summary = (
        f"{data['title']} — {start_local.isoformat()} ({tz.key})"
        + (f", {data['place_name']}" if data.get("place_name") else "")
        + f". Entered directly by {actor_label}."
    )
    source = source_service.create_source(
        db,
        actor_label=actor_label,
        group=group,
        kind="manual",
        title=f"Direct entry: {data['title']}"[:240],
        granted_scope="direct organiser entry",
        chunks=[{"text": summary, "sender": actor_label}],
    )
    event = Event(
        group_id=group.id,
        title=data["title"],
        description=data.get("description", ""),
        starts_at=starts_at,
        ends_at=ends_at,
        timezone=data.get("timezone") or group.home_timezone or "UTC",
        all_day=bool(data.get("all_day", False)),
        place_name=data.get("place_name", ""),
        place_address=data.get("place_address", ""),
        lat=data.get("lat"),
        lng=data.get("lng"),
        host_label=data.get("host_label", ""),
        host_id=data.get("host_id"),
        cost_text=data.get("cost_text", ""),
        cost_cents=data.get("cost_cents"),
        bring_list=data.get("bring_list", ""),
        access_notes=data.get("access_notes", ""),
        event_type=data.get("event_type", ""),
        sub_group=data.get("sub_group", "") or (subgroup.name if subgroup else ""),
        subgroup_id=subgroup.id if subgroup else None,
        capacity=data.get("capacity"),
        status=data.get("status", "published"),
        created_by=actor_label,
    )
    db.add(event)
    db.flush()
    db.add(EventSourceLink(event_id=event.id, source_id=source.id, field="", quote=summary))
    audit.record(
        db, actor=actor_label, action="event.created",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"source_id": source.id, "status": event.status},
    )
    db.commit()
    return event


# ------------------------------------------------------------------
# Editing + change history (B-050), cancellation + clone (B-051)
# ------------------------------------------------------------------


def _parse_if_match(if_match: str | None) -> int:
    """The version an If-Match header names (``"v3"``, ``v3``, or a
    bare ``3`` are all the same claim). Missing or unreadable is a
    client error in plain words — an update without a version
    claim is exactly the lost-update D-15 exists to prevent."""
    if if_match is None or not str(if_match).strip():
        raise AppError(
            "validation_error",
            detail="Updates need an If-Match header carrying the version you read.",
        )
    text = str(if_match).strip().strip('"')
    if text.startswith("v"):
        text = text[1:]
    try:
        return int(text)
    except ValueError:
        raise AppError(
            "validation_error",
            detail="That If-Match header does not name a version.",
        )


def apply_changes(
    db: Session,
    *,
    event: Event,
    changes: dict,
    actor_label: str,
    reason: str = "",
) -> list[str]:
    """The one edit engine (B-050; shared since Phase 6 by
    merges, change candidates, and contradiction resolutions):
    apply already-validated values, writing one EventChange per
    field that actually changes, setting ``needs_reannounce``
    on material changes, and bumping the version once. Returns
    the fields that changed. Never commits — the caller's
    transaction owns the outcome, so an approval that applies
    a change and records its decision is one atomic act."""
    changed_fields: list[str] = []
    for field_name in EDITABLE_FIELDS:
        if field_name not in changes:
            continue
        new_value = changes[field_name]
        old_value = getattr(event, field_name)
        if new_value == old_value:
            continue
        record_change(
            db, event=event, field=field_name, old_value=old_value,
            new_value=new_value, actor_label=actor_label, reason=reason,
        )
        setattr(event, field_name, new_value)
        changed_fields.append(field_name)
    if changed_fields:
        if MATERIAL_FIELDS & set(changed_fields):
            event.needs_reannounce = True
        event.version += 1
    return changed_fields


def update_event(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    event_id: str,
    changes: dict,
    if_match: str | None,
    reason: str = "",
) -> Event:
    """Apply a PATCH under optimistic concurrency (B-050, D-15).

    Every field that actually changes writes an EventChange row;
    a material change sets needs_reannounce. A patch that changes
    nothing is a no-op success (no version bump, no history
    noise). Cancelled events refuse edits — a cancellation is
    reversed by cloning forward, not by quietly un-cancelling."""
    event = get_event(db, group, event_id)
    claimed = _parse_if_match(if_match)
    if claimed != event.version:
        raise AppError(
            "stale_version",
            detail=(
                f"This event has changed since you read it — it is now "
                f"at version {event.version}. Read it again and re-apply "
                f"your change to the current version."
            ),
        )
    if event.status == "cancelled":
        raise AppError("conflict", detail="A cancelled event cannot be edited.")
    # A scope change names a sub-group of THIS group or clears
    # the scope (B-105) — validated before the edit engine runs,
    # so a bad id refuses the whole patch, never half of it.
    subgroup = None
    if changes.get("subgroup_id"):
        from gpa.services import subgroups as subgroup_service

        subgroup = subgroup_service.get_subgroup(db, group, changes["subgroup_id"])
    changed_fields = apply_changes(
        db, event=event, changes=changes, actor_label=actor_label, reason=reason
    )
    if "subgroup_id" in changed_fields and "sub_group" not in changes:
        # The display word follows the scope unless the caller
        # set their own words in the same patch.
        event.sub_group = subgroup.name if subgroup is not None else ""
    if changed_fields:
        audit.record(
            db, actor=actor_label, action="event.updated",
            group_id=group.id, entity_type="event", entity_id=event.id,
            detail={"fields": changed_fields},
        )
        # A published event that changed reschedules its
        # reminders IN THIS TRANSACTION (B-096): the old queued
        # reminders are superseded, their jobs deleted, and the
        # new rows carry the new facts — no orphan reminder for
        # the old time can survive the commit.
        if event.status == "published":
            from gpa.services import reminders

            reminders.schedule_reminders(db, group=group, event=event)
    db.commit()
    return event


def list_changes(db: Session, group: Group, event_id: str) -> list[EventChange]:
    """One event's change feed: complete, in the order the
    changes happened (B-050's acceptance)."""
    event = get_event(db, group, event_id)
    return (
        db.query(EventChange)
        .filter(EventChange.event_id == event.id)
        .order_by(EventChange.created_at, EventChange.id)
        .all()
    )


def publish_event(
    db: Session, *, actor_label: str, group: Group, event_id: str
) -> Event:
    """Draft → published: the organiser's own version of the
    approval act for a directly-entered event."""
    event = get_event(db, group, event_id)
    if event.status == "published":
        raise AppError("conflict", detail="That event is already published.")
    if event.status == "cancelled":
        raise AppError("conflict", detail="A cancelled event cannot be published.")
    record_change(
        db, event=event, field="status", old_value=event.status,
        new_value="published", actor_label=actor_label,
    )
    event.status = "published"
    event.version += 1
    audit.record(
        db, actor=actor_label, action="event.published",
        group_id=group.id, entity_type="event", entity_id=event.id,
    )
    # Materialise the reminders (B-096) in the SAME transaction:
    # a publish and its reminder jobs exist together or not at all.
    from gpa.services import reminders

    reminders.schedule_reminders(db, group=group, event=event)
    db.commit()
    return event


def apply_cancellation(
    db: Session, *, event: Event, actor_label: str, reason: str = ""
) -> None:
    """Cancellation as a state change, without committing
    (B-051; shared with change candidates in Phase 6, which
    cancel inside the approval's transaction): the change row,
    the status, the reason, the re-announce flag, the version."""
    from gpa.models.identity import now_utc

    record_change(
        db, event=event, field="status", old_value=event.status,
        new_value="cancelled", actor_label=actor_label, reason=reason,
    )
    event.status = "cancelled"
    event.cancelled_reason = reason
    event.cancelled_at = now_utc()
    event.needs_reannounce = True
    event.version += 1
    # A cancelled event's reminders die with it (B-096): the
    # reschedule pass supersedes every outstanding reminder row
    # and deletes its queued job — nobody is reminded to come to
    # a thing that is not happening. (Telling people is the
    # caller's work: cancel_event alerts at once; a change
    # candidate's cancellation is told by its announcement.)
    group = db.get(Group, event.group_id)
    if group is not None:
        from gpa.services import reminders

        reminders.schedule_reminders(db, group=group, event=event)


def cancel_event(
    db: Session, *, actor_label: str, group: Group, event_id: str, reason: str = ""
) -> Event:
    """Cancel, with the reason in the canceller's words (B-051).

    Cancellation is a state, never a disappearance: the row, its
    change history, and its feed presence all stay — every
    surface that showed the event live now shows it *cancelled*.
    Since Phase 8 the cancelling organiser's act also TELLS the
    RSVP'd people at once, through the outbox: an urgent change
    alert (urgent because a person cancelled it, never because
    the system guessed) that quiet hours cannot hold."""
    event = get_event(db, group, event_id)
    if event.status == "cancelled":
        raise AppError("conflict", detail="That event is already cancelled.")
    was_published = event.status == "published"
    apply_cancellation(db, event=event, actor_label=actor_label, reason=reason)
    if was_published:
        from gpa.services import notifications as notify_service

        for recipient in notify_service.event_recipients(db, event.id):
            notify_service.create_notification(
                db,
                group=group,
                recipient_user=recipient["user"],
                recipient_label=recipient["label"],
                recipient_email=recipient["email"],
                kind="change.cancellation",
                subject=f"Cancelled: {event.title}",
                body_text=(
                    f"'{event.title}' has been cancelled. "
                    "There is nothing to come to — sorry for the change of plans."
                ),
                event_id=event.id,
                urgent=True,
            )
    audit.record(
        db, actor=actor_label, action="event.cancelled",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"reason": reason},
    )
    db.commit()
    return event


def clone_event(
    db: Session, *, actor_label: str, group: Group, event_id: str
) -> Event:
    """Clone last time's event (B-051): structure copies — title,
    times, place, lists, capacity, the recurrence rule — and
    people's answers never do: no RSVPs, no discussion, no change
    history. The clone is born a draft with fresh dates to set;
    the old series' exceptions do NOT copy (they name the old
    series' dates, not the new one's)."""
    source = get_event(db, group, event_id)
    clone = Event(
        group_id=group.id,
        title=source.title,
        description=source.description,
        starts_at=source.starts_at,
        ends_at=source.ends_at,
        timezone=source.timezone,
        all_day=source.all_day,
        place_name=source.place_name,
        place_address=source.place_address,
        lat=source.lat,
        lng=source.lng,
        host_label=source.host_label,
        host_id=source.host_id,
        cost_text=source.cost_text,
        cost_cents=source.cost_cents,
        bring_list=source.bring_list,
        access_notes=source.access_notes,
        event_type=source.event_type,
        sub_group=source.sub_group,
        capacity=source.capacity,
        flyer_id=source.flyer_id,
        status="draft",
        created_by=actor_label,
    )
    db.add(clone)
    db.flush()
    recurrence = (
        db.query(Recurrence).filter(Recurrence.event_id == source.id).one_or_none()
    )
    if recurrence is not None:
        db.add(Recurrence(event_id=clone.id, rrule=recurrence.rrule, exceptions_json="[]"))
    audit.record(
        db, actor=actor_label, action="event.cloned",
        group_id=group.id, entity_type="event", entity_id=clone.id,
        detail={"from_event_id": source.id},
    )
    db.commit()
    return clone


# ------------------------------------------------------------------
# Generated event image (Phase 5, B-067)
# ------------------------------------------------------------------


def generate_event_image(
    db: Session,
    *,
    storage,
    actor_label: str,
    group: Group,
    event_id: str,
    description: str = "",
):
    """Generate an image for an event that has none (B-067).

    The picture is made through the AI adapter (ai/client.py's
    ``generate_image`` — a driver boundary; the live driver is
    not wired to an image endpoint yet and refuses in plain
    words, which this service passes on as a 422 rather than
    dressing up). What keeps the feature honest is the record:
    the image's Flyer row carries ``generated=True`` as a COLUMN,
    so every projection shows the flag forever — a generated
    picture is never passed off as a photograph. Its provenance
    is a manual source naming the requesting organiser, exactly
    like direct entry (B-049): the act is recorded, by name.

    ``description`` is the organiser's own words for the picture
    (what it should show). When they are a genuine description
    they double as the image's alt text — a human description is
    complete the moment it is written (B-065); without them the
    flyer starts, honestly, as awaiting_description.
    """
    import hashlib
    import io

    from PIL import Image

    from gpa.ai import client as ai_client
    from gpa.ai.client import ModelError
    from gpa.models.core import FileAsset
    from gpa.models.sources import Flyer
    from gpa.services import images as image_service
    from gpa.services import sources as source_service
    from gpa.services import usage as usage_service
    from gpa.services.vision import _is_genuine_alt

    event = get_event(db, group, event_id)
    if event.flyer_id:
        raise AppError(
            "conflict",
            detail="That event already has an image — a generated one "
            "is only for events without a picture.",
        )
    when = event.starts_at.strftime("%A %d %B %Y")
    prompt = (
        f"A warm, printable poster illustration for a community event "
        f"called '{event.title}'"
        + (f" at {event.place_name}" if event.place_name else "")
        + f", on {when}."
        + (f" The organiser describes the picture they want: {description}"
           if description else "")
    )
    client = ai_client.get_client()
    try:
        result = client.generate_image(prompt=prompt)
    except ModelError as exc:
        raise AppError("validation_error", detail=str(exc)) from exc
    data = result.data or {}
    image_bytes = data.get("image_bytes")
    if not image_bytes:
        raise AppError(
            "validation_error",
            detail="The image driver returned no picture — nothing was stored.",
        )
    usage_service.record_usage(
        db, result=result, group_id=group.id, source_id=None,
        operation="vision:generate_image", prompt_name="generate_image",
        prompt_version=None,
    )
    storage_key = storage.put_bytes(image_bytes, "png")
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        width, height = image.size
    except Exception:
        raise AppError(
            "validation_error",
            detail="The image driver returned bytes that are not a picture.",
        ) from None
    thumb = image.copy()
    thumb.thumbnail((image_service.THUMBNAIL_DIMENSION, image_service.THUMBNAIL_DIMENSION))
    thumb_buffer = io.BytesIO()
    thumb.convert("RGB").save(thumb_buffer, "JPEG", quality=80)
    thumbnail_key = storage.put_bytes(thumb_buffer.getvalue(), "jpg")
    asset = FileAsset(
        group_id=group.id, storage_key=storage_key,
        filename=f"generated-{event.id[:8]}.png", mime="image/png",
        size=len(image_bytes), sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
    db.add(asset)
    db.flush()
    source = source_service.create_source(
        db, actor_label=actor_label, group=group, kind="manual",
        title=f"Generated image for {event.title}"[:240],
        granted_scope="generated image for an event, requested by an organiser",
        chunks=[], storage_key=storage_key, file_asset_id=asset.id,
    )
    genuine = _is_genuine_alt(description)
    flyer = Flyer(
        group_id=group.id, source_id=source.id, file_asset_id=asset.id,
        thumbnail_key=thumbnail_key, width=width, height=height,
        phash=image_service.perceptual_hash(image),
        generated=True,
        alt_text=(" ".join(description.split())[:500] if genuine
                  else "awaiting description"),
        alt_text_status="complete" if genuine else "awaiting_description",
    )
    db.add(flyer)
    db.flush()
    event.flyer_id = flyer.id
    record_change(
        db, event=event, field="flyer_id", old_value="",
        new_value=flyer.id, actor_label=actor_label,
        reason="generated image (B-067)",
    )
    audit.record(
        db, actor=actor_label, action="event.image_generated",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"flyer_id": flyer.id, "generated": True},
    )
    db.commit()
    return flyer
