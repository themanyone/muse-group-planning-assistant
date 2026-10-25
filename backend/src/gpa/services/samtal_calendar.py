"""The Samtal calendar return path (roadmap B-124).

A group's published events belong on the calendar where the
group already lives — for this team, the calendar of its
Samtal hall. And the road runs both ways under one law:

- **Out**: published (and cancelled) events are pushed to
  the hall's calendar, keyed by the event's own id; the
  :class:`ExternalEventLink` row remembers the outside id
  and a hash of what was pushed, so a later sync re-pushes
  only what changed here.
- **In**: an edit made on the Samtal side — a moved time, a
  new place, a cancellation — NEVER writes to the event.
  It arrives as a **change candidate** (B-077) in the review
  queue, backed by a source chunk that states the edit in
  words, and a person approves or rejects it there. The
  approval law runs in both directions: the hall can
  propose, only the queue disposes.

The "calendar" of a Samtal source is the calendar of its
hall — Samtal halls carry their calendars, so the calendar
id is the hall id the source was registered with. All of it
speaks through services/samtal.py's client boundary: in
this environment that is the recorded-fixture double, and
the round-trip test runs against the double and says so.
Nothing here has ever touched the real village.
"""

import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Candidate, Group
from gpa.models.integrations import ExternalEventLink
from gpa.models.sources import Source
from gpa.services import audit
from gpa.services import sources as source_service

# Who the return path's candidates and sources are authored
# by — a system label, never a person's (the B-045 rule).
RETURN_AUTHOR = "system:samtal-calendar"


def _iso(instant: datetime) -> str:
    """A stored naive-UTC instant in the wire's ISO shape."""
    return instant.replace(tzinfo=timezone.utc).isoformat()


def event_payload(event: Event) -> dict:
    """The canonical calendar payload for one event — the
    exact dict whose hash the link row remembers."""
    return {
        "ref": event.id,
        "title": event.title,
        "starts_at": _iso(event.starts_at),
        "ends_at": _iso(event.ends_at),
        "place": event.place_name,
        "cancelled": event.status == "cancelled",
    }


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _client_or_refusal():
    from gpa.services import samtal as samtal_service

    try:
        client = samtal_service.get_client()
        client.ping()
    except samtal_service.SamtalUnavailable as err:
        raise AppError("validation_error", detail=str(err)) from err
    return client


def sync_calendar(
    db: Session, *, actor_label: str, group: Group, source: Source
) -> dict:
    """Run the return path both ways for one Samtal source.

    Returns ``{"pushed": n, "candidates_created": m}``. The
    whole sync is one transaction: pushes, link rows, return
    sources and their candidates exist together or not at
    all (commit-before-answer at the end).
    """
    if source.kind != "chat_live" or source.connector_kind != "samtal":
        raise AppError(
            "validation_error",
            detail="The calendar return path belongs to a Samtal live source.",
        )
    client = _client_or_refusal()
    calendar_id = source.external_id

    pushed = _push_events(db, group=group, client=client, calendar_id=calendar_id)
    created = _pull_edits(
        db, group=group, source=source, client=client, calendar_id=calendar_id
    )
    audit.record(
        db, actor=actor_label, action="samtal_calendar.synced",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"pushed": pushed, "candidates_created": created},
    )
    db.commit()
    return {"pushed": pushed, "candidates_created": created}


def _push_events(db: Session, *, group: Group, client, calendar_id: str) -> int:
    """Push every published/cancelled event whose outside
    copy is missing or stale. Drafts never travel (the
    B-049 bright line: draft reaches no outward surface)."""
    events = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.status.in_(("published", "cancelled")),
            Event.deleted_at.is_(None),
        )
        .all()
    )
    pushed = 0
    for event in events:
        payload = event_payload(event)
        digest = _payload_hash(payload)
        link = (
            db.query(ExternalEventLink)
            .filter(
                ExternalEventLink.event_id == event.id,
                ExternalEventLink.connector_kind == "samtal",
            )
            .one_or_none()
        )
        if link is not None and link.payload_hash == digest:
            continue  # The outside copy already says this.
        external_id = client.upsert_calendar_event(calendar_id, payload)
        if link is None:
            link = ExternalEventLink(
                group_id=group.id, event_id=event.id,
                connector_kind="samtal", external_id=external_id,
            )
            db.add(link)
        link.external_id = external_id
        link.payload_hash = digest
        pushed += 1
    db.flush()
    return pushed


def _pull_edits(
    db: Session, *, group: Group, source: Source, client, calendar_id: str
) -> int:
    """Draft a change candidate for every Samtal-side edit
    to an event this group published there. An outside event
    with no ``ref`` to one of ours is not ours to import —
    importing it would mint an event no person approved —
    so it is left standing in the hall, untouched."""
    created = 0
    for remote in client.list_calendar_events(calendar_id):
        ref = remote.get("ref") or ""
        event = db.get(Event, ref) if ref else None
        if event is None or event.group_id != group.id:
            continue
        if event.status == "draft" or event.deleted_at is not None:
            continue
        candidate = _draft_change(db, group=group, source=source,
                                  event=event, remote=remote)
        if candidate is not None:
            created += 1
    return created


def _draft_change(
    db: Session, *, group: Group, source: Source, event: Event, remote: dict
) -> Candidate | None:
    """Compare one outside copy with the event it mirrors;
    when they differ, build the change candidate (or None
    when they agree, or when this exact proposal already
    waits in the queue undecided)."""
    tz = ZoneInfo(event.timezone or group.home_timezone or "UTC")
    fields: dict = {}
    sentences: list[str] = []

    def _remote_instant(key: str) -> datetime | None:
        raw = remote.get(key)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    for key, field, label in (
        ("starts_at", "starts", "starts"),
        ("ends_at", "ends", "ends"),
    ):
        remote_instant = _remote_instant(key)
        current = event.starts_at if field == "starts" else event.ends_at
        if remote_instant is not None and remote_instant != current:
            local = remote_instant.replace(tzinfo=timezone.utc).astimezone(tz)
            sentence = (
                f"On the Samtal calendar, '{event.title}' now {label} at "
                f"{local.strftime('%Y-%m-%d %H:%M')} ({tz.key})."
            )
            fields[field] = {
                "value": local.isoformat(),
                "quote": sentence,
                "supported": True,
                "resolved_date": local.date().isoformat(),
                "resolved_time": local.time().isoformat(),
            }
            sentences.append(sentence)

    remote_place = str(remote.get("place") or "")
    if remote_place != event.place_name:
        sentence = (
            f"On the Samtal calendar, '{event.title}' now meets at "
            f"{remote_place or 'no stated place'}."
        )
        fields["place"] = {
            "value": remote_place, "quote": sentence, "supported": True,
        }
        sentences.append(sentence)

    remote_title = str(remote.get("title") or "")
    if remote_title and remote_title != event.title:
        # A retitle is not one of B-077's applicable fields —
        # it is recorded in the proposal's words so a person
        # sees it, and the queue's own edit door can apply it.
        sentences.append(
            f"On the Samtal calendar, the event is titled '{remote_title}'."
        )

    if remote.get("cancelled") and event.status != "cancelled":
        sentence = f"On the Samtal calendar, '{event.title}' was cancelled."
        fields["cancelled"] = {
            "value": True, "quote": sentence, "supported": True,
        }
        sentences.append(sentence)

    if not fields:
        return None  # The copies agree (or differ only in words).

    # The dedupe rule: the same proposal, still undecided,
    # is not drafted twice — a sync is safe to re-run.
    fingerprint = json.dumps(fields, sort_keys=True)
    pending = (
        db.query(Candidate)
        .filter(
            Candidate.group_id == group.id,
            Candidate.kind == "event_change",
            Candidate.created_by == RETURN_AUTHOR,
            Candidate.status.in_(("pending", "edited")),
        )
        .all()
    )
    for other in pending:
        try:
            other_draft = json.loads(other.draft_json or "{}")
        except json.JSONDecodeError:
            continue
        if other_draft.get("target_event_id") != event.id:
            continue
        if json.dumps(other_draft.get("fields", {}), sort_keys=True) == fingerprint:
            return None

    words = " ".join(sentences)
    backing = source_service.create_source(
        db, actor_label=RETURN_AUTHOR, group=group, kind="manual",
        title=f"Samtal calendar edit: {event.title}"[:240],
        granted_scope="calendar return path (B-124)",
        chunks=[{"text": words, "sender": "Samtal calendar"}],
    )
    # create_source committed; the candidate rides its own
    # commit at the end of the sync — re-fetch the chunk in
    # this session for the provenance row.
    chunk = source_service.list_chunks(db, backing)[0]
    draft = {
        "title": f"Change to {event.title}",
        "target_event_id": event.id,
        "target_title": event.title,
        "fields": fields,
    }
    candidate = Candidate(
        group_id=group.id,
        kind="event_change",
        title=f"Change to {event.title}"[:240],
        created_by=RETURN_AUTHOR,
        status="pending",
        source_id=backing.id,
        draft_json=json.dumps(draft),
        flags_json="[]",
    )
    db.add(candidate)
    db.flush()
    for field_name in fields:
        source_service.add_provenance(
            db, candidate_id=candidate.id, chunk=chunk,
            field=field_name, quote=fields[field_name]["quote"],
        )
    db.flush()
    return candidate
