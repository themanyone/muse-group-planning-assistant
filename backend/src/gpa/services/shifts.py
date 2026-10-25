"""Volunteer shifts (roadmap B-107).

B-088's role slots, generalised: a SHIFT is a set of role slots
that share a role name and a start/end — "two drivers, Saturday
09:00–12:00" is two slot rows carrying the same window, so a
claim is still one person's promise on one row, and releasing
is the same unclaim as ever (services/checklists.claim_slot is
the one claim door; shifts add the window, not a second truth).

The gap report is the point of the exercise: over a window it
names, per event, exactly which roles are still unclaimed —
"Saturday still needs a driver and a door person" — in words a
coordinator can read aloud, with the counts beside them.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.planning import RoleSlot
from gpa.services import audit
from gpa.services import events as event_service


def _event_for(
    db: Session,
    group: Group,
    event_id: str,
    *,
    viewer_role: str | None,
    viewer_user_id,
) -> Event:
    """The event a shift attaches to — visible to the caller,
    sub-group scope included (B-105), drafts behind the bright
    line as always."""
    return event_service.get_visible_event(
        db, group, event_id, viewer_role, viewer_user_id=viewer_user_id
    )


def open_shift(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    viewer_user_id,
    event_id: str,
    role_name: str,
    headcount: int = 1,
    starts_at: datetime,
    ends_at: datetime,
) -> dict:
    """Open a shift: ``headcount`` slots of ``role_name`` over
    one window on one event. Returns the shift's grouped view."""
    if not role_name or not role_name.strip():
        raise AppError("validation_error", detail="A shift needs its role name.")
    if headcount < 1:
        raise AppError("validation_error", detail="A shift needs at least one person.")
    if ends_at <= starts_at:
        raise AppError("validation_error", detail="A shift must end after it starts.")
    event = _event_for(
        db, group, event_id, viewer_role=viewer_role, viewer_user_id=viewer_user_id
    )
    rows = []
    for _ in range(headcount):
        slot = RoleSlot(
            group_id=group.id, event_id=event.id,
            role_name=role_name.strip()[:80],
            starts_at=starts_at, ends_at=ends_at,
            created_by=actor_label,
        )
        db.add(slot)
        rows.append(slot)
    db.flush()
    audit.record(
        db, actor=actor_label, action="shift.opened",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={
            "role_name": role_name.strip(), "headcount": headcount,
            "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(),
        },
    )
    db.commit()
    return _group_of(rows, event)


def _group_of(slots: list[RoleSlot], event: Event) -> dict:
    """One shift's grouped view from its slot rows."""
    first = slots[0]
    return {
        "event_id": event.id,
        "role_name": first.role_name,
        "starts_at": first.starts_at,
        "ends_at": first.ends_at,
        "headcount": len(slots),
        "claimed": sum(
            1 for slot in slots
            if slot.claimed_by_id is not None or slot.claimed_by_label
        ),
        "slots": slots,
    }


def shift_groups(db: Session, event: Event) -> list[dict]:
    """An event's shifts: its timed slots grouped by (role,
    window), in the order the shifts were opened. Timeless
    B-088 slots are not shifts and stay with the checklist
    view, where they always lived."""
    slots = (
        db.query(RoleSlot)
        .filter(RoleSlot.event_id == event.id, RoleSlot.starts_at.isnot(None))
        .order_by(RoleSlot.created_at, RoleSlot.id)
        .all()
    )
    groups: dict[tuple, list[RoleSlot]] = {}
    order: list[tuple] = []
    for slot in slots:
        key = (slot.role_name, slot.starts_at, slot.ends_at)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(slot)
    return [_group_of(groups[key], event) for key in order]


def _needs_phrase(missing: list[dict]) -> str:
    """The missing roles as a reading-aloud list: "a driver",
    "2 cooks", joined the way a person would say them."""
    parts = []
    for entry in missing:
        role = entry["role_name"]
        if entry["count"] == 1:
            parts.append(f"a {role}")
        else:
            plural = role if role.endswith("s") else role + "s"
            parts.append(f"{entry['count']} {plural}")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def gap_report(
    db: Session, group: Group, *, start: datetime, end: datetime
) -> list[dict]:
    """What is still unclaimed, per event, over [start, end).

    Published events only (a draft's gaps are its organiser's
    private business; a cancelled event needs nobody). An event
    with every slot claimed — or no slots at all — earns no
    line: the report is the missing, not the whole plan.
    """
    events = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.deleted_at.is_(None),
            Event.status == "published",
            Event.starts_at >= start,
            Event.starts_at < end,
        )
        .order_by(Event.starts_at, Event.id)
        .all()
    )
    report = []
    for event in events:
        slots = (
            db.query(RoleSlot)
            .filter(RoleSlot.event_id == event.id)
            .order_by(RoleSlot.created_at, RoleSlot.id)
            .all()
        )
        # Missing roles in the order they were first opened —
        # the summary reads in the coordinator's own order
        # ("a driver and a door person"), not the alphabet's.
        missing_by_role: dict[str, int] = {}
        for slot in slots:
            claimed = slot.claimed_by_id is not None or bool(slot.claimed_by_label)
            if not claimed:
                missing_by_role[slot.role_name] = missing_by_role.get(slot.role_name, 0) + 1
        if not missing_by_role:
            continue
        missing = [
            {"role_name": role, "count": count}
            for role, count in missing_by_role.items()
        ]
        tz = ZoneInfo(event.timezone or "UTC")
        day_name = event.starts_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%A")
        report.append(
            {
                "event_id": event.id,
                "event_title": event.title,
                "starts_at": event.starts_at,
                "missing": missing,
                "summary": f"{day_name} still needs {_needs_phrase(missing)}.",
            }
        )
    return report
