"""Attendance (roadmap B-109) — aggregates only, by design.

An organiser marks who came to an event; the group plans with
the numbers ("last three club nights drew ~20"). The marks are
write-and-count ONLY:

**There is deliberately no per-person attendance read in this
API** — no list of who attended, no per-member history, no
league table. That is a product refusal recorded in the
roadmap itself, so it is never "helpfully" added: people come
to a community's events without building themselves a public
attendance record. The refusal is pinned by tests: the
per-person routes do not exist, and the aggregate responses
carry counts and event identities — never a person's.

The one response that names a person is the mark's own receipt
(the organiser just marked them; the receipt echoes the act),
and unmarking is likewise a write. Reads are counts, full stop.
"""

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.nonprofit import AttendanceMark
from gpa.services import audit
from gpa.services import events as event_service
from gpa.services.planning import member_label


def _published_event(
    db: Session,
    group: Group,
    event_id: str,
    *,
    viewer_role: str | None,
    viewer_user_id,
) -> Event:
    """The event being marked: visible to the caller, and
    published — attendance is a record of something that
    happened, and a draft has not."""
    event = event_service.get_visible_event(
        db, group, event_id, viewer_role, viewer_user_id=viewer_user_id
    )
    if event.status != "published":
        raise AppError(
            "validation_error",
            detail="Attendance is marked on a published event.",
        )
    return event


def mark_attendance(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    viewer_user_id,
    event_id: str,
    user_id: str,
) -> AttendanceMark:
    """Mark one member present at one event. Idempotent: the
    (event, person) pair is unique, so re-marking returns the
    existing record instead of stacking a second one."""
    event = _published_event(
        db, group, event_id, viewer_role=viewer_role, viewer_user_id=viewer_user_id
    )
    if member_label(db, group, user_id) is None:
        raise AppError(
            "validation_error",
            detail="Attendance is marked for members of this group.",
        )
    existing = (
        db.query(AttendanceMark)
        .filter(
            AttendanceMark.event_id == event.id,
            AttendanceMark.user_id == user_id,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    row = AttendanceMark(
        group_id=group.id, event_id=event.id, user_id=user_id,
        marked_by=actor_label,
    )
    db.add(row)
    db.flush()
    audit.record(
        db, actor=actor_label, action="attendance.marked",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"user_id": user_id},
    )
    db.commit()
    return row


def unmark_attendance(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    event_id: str,
    user_id: str,
) -> None:
    """Remove one mark (a correction, recorded like any act)."""
    event = event_service.get_event(db, group, event_id)
    row = (
        db.query(AttendanceMark)
        .filter(
            AttendanceMark.event_id == event.id,
            AttendanceMark.user_id == user_id,
        )
        .one_or_none()
    )
    if row is None:
        raise AppError("not_found", detail="That person is not marked at this event.")
    db.delete(row)
    audit.record(
        db, actor=actor_label, action="attendance.unmarked",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"user_id": user_id},
    )
    db.commit()


def event_headcount(db: Session, event: Event) -> int:
    """How many people are marked at one event. A count — the
    only per-event read this module offers."""
    return (
        db.query(AttendanceMark)
        .filter(AttendanceMark.event_id == event.id)
        .count()
    )


def attendance_summary(db: Session, group: Group, *, recent: int = 3) -> dict:
    """The planning view: the group's most recent ``recent``
    published events that have begun, each with its headcount,
    and the average across them — "last three club nights drew
    ~20", as data. Counts and event identities only."""
    now = now_utc()
    events = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.deleted_at.is_(None),
            Event.status == "published",
            Event.starts_at <= now,
        )
        .order_by(Event.starts_at.desc(), Event.id.desc())
        .limit(recent)
        .all()
    )
    rows = [
        {
            "event_id": event.id,
            "title": event.title,
            "starts_at": event.starts_at,
            "attendee_count": event_headcount(db, event),
        }
        for event in events
    ]
    average = (
        sum(row["attendee_count"] for row in rows) / len(rows) if rows else 0.0
    )
    return {
        "events_considered": len(rows),
        "average_attendance": average,
        "events": rows,
    }
