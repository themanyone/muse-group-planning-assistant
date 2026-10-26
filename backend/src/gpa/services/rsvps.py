"""RSVPs (roadmap B-061).

One answer per person per event: coming / can't / maybe, plus
guests and a note. Capacity turns answers into arithmetic the
SERVICE owns: when the coming-set overflows the room, the
overflow waits — in arrival order, as data (``waitlist_position``)
— and when a seat frees, the next in line is promoted and told.

Since Phase 8 the telling is the notification outbox
(services/notifications.py): a promotion writes a real
Notification row (kind ``rsvp.promoted``) whose send job the
outbox owns — the ``notify`` handler lives there now. This
module's Phase 4 audit-stub handler and its bare job enqueue
were superseded by that move; the job payload still carries
``to``/``kind``/``rsvp_id`` for anyone reading the queue.
Answer changes also re-materialise the event's reminders
(B-096): a new "coming" starts being reminded, a withdrawn
one stops.
"""

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.events import RSVP_STATUSES, Event, Rsvp
from gpa.models.groups import Group
from gpa.internal.client import call_internal
from gpa.services import audit
from gpa.services import events as event_service
from gpa.services import reminders as reminder_service

NOTIFY_JOB_KIND = "notify"


def _coming_rows(db: Session, event: Event) -> list[Rsvp]:
    """The event's confirmed-or-waiting answers, in arrival
    order — the order promotion honours."""
    return (
        db.query(Rsvp)
        .filter(Rsvp.event_id == event.id, Rsvp.status == "coming")
        .order_by(Rsvp.created_at, Rsvp.id)
        .all()
    )


def _rebalance(db: Session, event: Event, *, actor_label: str) -> list[Rsvp]:
    """Recompute the waitlist after any change to the coming-set.

    Walks the coming answers in arrival order, seating each
    party (1 + guests) while capacity lasts. The line is
    STRICT: a party that does not fit blocks it — a freed seat
    is offered to the next in line (the roadmap's words), and
    nobody jumps the queue because their party is smaller.
    Returns the rows this rebalance PROMOTED (they were waiting,
    now they are seated) — the caller notifies them. With no
    stated capacity, nobody ever waits."""
    rows = _coming_rows(db, event)
    if event.capacity is None:
        for row in rows:
            row.waitlisted = False
            row.waitlist_position = None
        return []
    seated = 0
    waiting = 0
    promoted: list[Rsvp] = []
    blocked = False
    for row in rows:
        party = 1 + max(0, row.guests)
        if not blocked and seated + party <= event.capacity:
            if row.waitlisted:
                promoted.append(row)
            row.waitlisted = False
            row.waitlist_position = None
            seated += party
        else:
            blocked = True
            waiting += 1
            row.waitlisted = True
            row.waitlist_position = waiting
    return promoted


def _notify_promotions(
    db: Session, settings: Settings, *, group: Group, event: Event,
    promoted: list[Rsvp], actor_label: str,
) -> None:
    """One notification per promotion (B-061's acceptance): the
    freed seat OFFERED to the next in line is a row in the
    notification outbox with its own send job — durable, and
    never a side-effect lost with the request. (Phase 8
    superseded this module's old bare-job enqueue: the payload
    keeps ``to``/``kind``/``rsvp_id``, and gains the
    ``notification_id`` the outbox handler delivers.)"""
    for row in promoted:
        audit.record(
            db, actor=actor_label, action="rsvp.promoted",
            group_id=group.id, entity_type="rsvp", entity_id=row.id,
            detail={"event_id": event.id, "to": row.principal_label},
        )
        call_internal(
            db, caller="events", operation="notify.rsvp_promotion",
            payload={
                "group_id": group.id, "event_id": event.id, "rsvp_id": row.id,
            },
        )
    if promoted:
        db.commit()


def set_rsvp(
    db: Session,
    settings: Settings,
    *,
    principal_label: str,
    user_id: str | None,
    seat_id: str | None,
    group: Group,
    event_id: str,
    status: str,
    guests: int = 0,
    note: str = "",
) -> Rsvp:
    """Create or update the caller's answer (the upsert is the
    (event, principal) uniqueness on the model). Answering a
    cancelled event is refused in plain words; answering a draft
    is a 404 — it does not exist for this purpose."""
    if status not in RSVP_STATUSES:
        raise AppError(
            "validation_error",
            detail="An RSVP is 'coming', 'cant', or 'maybe'.",
        )
    if guests < 0:
        raise AppError("validation_error", detail="Guests cannot be negative.")
    event = event_service.get_event(db, group, event_id)
    if event.status == "draft":
        raise AppError("not_found", detail="That event could not be found.")
    if event.status == "cancelled":
        raise AppError("conflict", detail="That event is cancelled.")
    row = (
        db.query(Rsvp)
        .filter(Rsvp.event_id == event.id, Rsvp.principal_label == principal_label)
        .one_or_none()
    )
    if row is None:
        row = Rsvp(
            event_id=event.id, principal_label=principal_label,
            user_id=user_id, seat_id=seat_id,
        )
        db.add(row)
    row.status = status
    row.guests = guests
    row.note = note
    db.flush()
    promoted = _rebalance(db, event, actor_label=principal_label)
    audit.record(
        db, actor=principal_label, action="rsvp.set",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"status": status, "guests": guests},
    )
    # The answer set changed, so the reminder set does too
    # (B-096) — in this same transaction.
    reminder_service.schedule_reminders(db, group=group, event=event)
    db.commit()
    _notify_promotions(
        db, settings, group=group, event=event,
        promoted=[r for r in promoted if r.principal_label != principal_label],
        actor_label=principal_label,
    )
    return row


def remove_rsvp(
    db: Session,
    settings: Settings,
    *,
    principal_label: str,
    group: Group,
    event_id: str,
) -> None:
    """Withdraw the caller's answer entirely. A freed seat runs
    the same promotion road as a change to 'cant'."""
    event = event_service.get_event(db, group, event_id)
    row = (
        db.query(Rsvp)
        .filter(Rsvp.event_id == event.id, Rsvp.principal_label == principal_label)
        .one_or_none()
    )
    if row is None:
        raise AppError("not_found", detail="You have not answered that event.")
    db.delete(row)
    db.flush()
    promoted = _rebalance(db, event, actor_label=principal_label)
    audit.record(
        db, actor=principal_label, action="rsvp.removed",
        group_id=group.id, entity_type="event", entity_id=event.id,
    )
    reminder_service.schedule_reminders(db, group=group, event=event)
    db.commit()
    _notify_promotions(
        db, settings, group=group, event=event, promoted=promoted,
        actor_label=principal_label,
    )


def rebalance_and_notify(
    db: Session,
    settings: Settings,
    *,
    group: Group,
    event: Event,
    actor_label: str,
) -> list[Rsvp]:
    """Re-run the waitlist after something OUTSIDE the RSVP
    routes changed the arithmetic — an organiser raising the
    event's capacity (the events router calls this when a PATCH
    touches capacity). Promotions notify exactly as if a seat
    had freed by an answer change."""
    promoted = _rebalance(db, event, actor_label=actor_label)
    db.commit()
    _notify_promotions(
        db, settings, group=group, event=event, promoted=promoted,
        actor_label=actor_label,
    )
    return promoted


def list_rsvps(db: Session, group: Group, event_id: str) -> list[Rsvp]:
    """Every answer to one event, arrival order."""
    event = event_service.get_event(db, group, event_id)
    return (
        db.query(Rsvp)
        .filter(Rsvp.event_id == event.id)
        .order_by(Rsvp.created_at, Rsvp.id)
        .all()
    )


def summary(db: Session, event: Event) -> dict:
    """The counts a calendar surface shows: seats taken by the
    confirmed, parties waiting, and the maybes/cant's."""
    rows = db.query(Rsvp).filter(Rsvp.event_id == event.id).all()
    coming = [r for r in rows if r.status == "coming"]
    confirmed = [r for r in coming if not r.waitlisted]
    return {
        "capacity": event.capacity,
        "coming_parties": len(coming),
        "confirmed_seats": sum(1 + max(0, r.guests) for r in confirmed),
        "waitlisted_parties": len([r for r in coming if r.waitlisted]),
        "maybe": len([r for r in rows if r.status == "maybe"]),
        "cant": len([r for r in rows if r.status == "cant"]),
    }

