"""Re-announce drafts (roadmap B-078).

When an event changes materially, the group deserves to hear
what changed — in words that name the old value and the new
one, never a bare "the event was updated". This service drafts
that announcement from the event's own change history
(B-050's EventChange rows): every material change since the
last draft (or since the event began, when there is no draft
yet), rendered in the event's timezone, oldest first.

Drafting does NOT clear ``needs_reannounce`` — the flag says
"the group has not been told", and a draft in a drawer tells
nobody. APPROVAL does (Phase 8, B-097): an organiser approves
the draft below and the change alerts go out through the
notification outbox to exactly the event's RSVP'd people.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import AnnouncementDraft, Event, EventChange
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.internal.client import call_internal
from gpa.services import audit
from gpa.services import events as event_service

# The fields whose change is material — the same set B-050's
# edit engine marks needs_reannounce for, plus status (a
# cancellation IS the announcement).
ANNOUNCE_FIELDS = event_service.MATERIAL_FIELDS | {"status"}

_LABELS = {
    "title": "Title",
    "starts_at": "Start",
    "ends_at": "End",
    "place_name": "Place",
    "place_address": "Address",
    "host_label": "Host",
    "cost_text": "Cost",
    "status": "Status",
}


def _humanise(field: str, raw: str, tz: ZoneInfo) -> str:
    """One stored change-history value in words a person reads:
    instants become local dates with weekday and time; an
    empty value says so plainly ("(none)") rather than leaving
    a hole in the sentence."""
    if raw == "":
        return "(none)"
    if field in ("starts_at", "ends_at"):
        try:
            instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        local = instant.astimezone(tz)
        return local.strftime("%A %d %B %Y, %I:%M %p")
    return raw


def _changes_since_last_draft(db: Session, event: Event) -> list[EventChange]:
    """The material changes a new draft must cover: all of them
    after the newest existing draft, or all of them when no
    draft exists yet."""
    last = (
        db.query(AnnouncementDraft)
        .filter(AnnouncementDraft.event_id == event.id)
        .order_by(AnnouncementDraft.created_at.desc(), AnnouncementDraft.id.desc())
        .first()
    )
    query = db.query(EventChange).filter(
        EventChange.event_id == event.id,
        EventChange.field.in_(sorted(ANNOUNCE_FIELDS)),
    )
    if last is not None:
        query = query.filter(EventChange.created_at > last.created_at)
    return query.order_by(EventChange.created_at, EventChange.id).all()


def draft_reannouncement(
    db: Session, *, actor_label: str, group: Group, event_id: str
) -> AnnouncementDraft:
    """Draft the announcement for an event's material changes.

    Refuses (422, plain words) when there is nothing to
    announce — a draft that says nothing would only train
    organisers to ignore drafts.
    """
    event = event_service.get_event(db, group, event_id)
    rows = _changes_since_last_draft(db, event)
    if not rows:
        raise AppError(
            "validation_error",
            detail=(
                "Nothing has changed since the last announcement "
                "draft, so there is nothing to announce."
            ),
        )
    tz = ZoneInfo(event.timezone or group.home_timezone or "UTC")
    lines = [f"Changes to {event.title}:"]
    change_view = []
    for row in rows:
        label = _LABELS.get(row.field, row.field)
        old = _humanise(row.field, row.old_value, tz)
        new = _humanise(row.field, row.new_value, tz)
        lines.append(f"{label}: {old} → {new}")
        change_view.append(
            {"field": row.field, "old": old, "new": new, "change_id": row.id}
        )
    draft = AnnouncementDraft(
        event_id=event.id,
        group_id=group.id,
        body="\n".join(lines),
        changes_json=json.dumps(change_view),
        created_by=actor_label,
    )
    db.add(draft)
    db.flush()
    audit.record(
        db, actor=actor_label, action="event.reannounce_drafted",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"draft_id": draft.id, "changes": len(change_view)},
    )
    db.commit()
    db.refresh(draft)
    return draft


def list_drafts(db: Session, group: Group, event_id: str) -> list[AnnouncementDraft]:
    event = event_service.get_event(db, group, event_id)
    return (
        db.query(AnnouncementDraft)
        .filter(AnnouncementDraft.event_id == event.id)
        .order_by(AnnouncementDraft.created_at, AnnouncementDraft.id)
        .all()
    )


def get_draft(db: Session, group: Group, draft_id: str) -> AnnouncementDraft:
    draft = db.get(AnnouncementDraft, draft_id)
    if draft is None or draft.group_id != group.id:
        raise AppError(
            "not_found", detail="That announcement draft could not be found."
        )
    return draft


def approve_announcement(
    db: Session, *, actor_label: str, group: Group, draft: AnnouncementDraft
) -> AnnouncementDraft:
    """Approve a draft and SEND the change alerts (B-097).

    The alert goes to exactly the event's RSVP'd people (coming
    and maybe) — the organiser's audience, nobody else — through
    the outbox, so a mail outage delays the telling but never
    blocks the approval. A member's +guests are reached through
    the member; an invite-token guest is reached at the address
    they answered from. Only after the alerts are written is
    ``needs_reannounce`` cleared: approval is the act that tells
    the group. Approval is once-only — a sent draft cannot be
    sent again; a new change drafts a new announcement.
    """
    if draft.sent_at is not None:
        raise AppError("conflict", detail="That announcement has already been sent.")
    event = db.get(Event, draft.event_id)
    # Recipients, preferences, and quiet hours are the
    # notifications module's work — asked for through the
    # internal API (B-162), in this same transaction. The
    # result's created ids are the audit's recipient count:
    # the people actually told (a muted member is not a
    # recipient of a telling that never happened).
    data = call_internal(
        db, caller="events", operation="notify.request",
        payload={
            "group_id": group.id,
            "audience": {"event_id": draft.event_id},
            "kind": "change.announcement",
            "subject": f"Update: {event.title}",
            "body_text": draft.body,
            "event_id": event.id,
        },
    )
    draft.sent_at = now_utc()
    draft.sent_by = actor_label
    if event is not None:
        event.needs_reannounce = False
    audit.record(
        db, actor=actor_label, action="announcement.approved",
        group_id=group.id, entity_type="announcement_draft", entity_id=draft.id,
        detail={"event_id": draft.event_id, "recipients": len(data["created_ids"])},
    )
    db.commit()
    return draft
