"""Checklists + role slots per event (roadmap B-088), and the
merged supplies view — the "buy once" read.

An event's plan includes STUFF (bring/prepare lines, each with
a quantity) and JOBS (host, driver, cook…). Both are claimable
by one person; an unclaimed line is a visible gap, never a
silent assumption (B-086's gaps view counts them). The merged
supplies view folds the same-named lines across a window of
the group's events into one shopping truth: cups needed by
three events this week are one line saying so, with the events
that need them — so the group buys once.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.identity import User, now_utc
from gpa.models.planning import ChecklistItem, RoleSlot
from gpa.services import audit
from gpa.services import planning as planning_service


def _visible_event(db: Session, group: Group, event_id: str, role: str | None) -> Event:
    return planning_service.visible_event(db, group, event_id, role)


def get_item(db: Session, group: Group, item_id: str) -> ChecklistItem:
    """One checklist item of this group, or 404."""
    item = db.get(ChecklistItem, item_id)
    if item is None or item.group_id != group.id:
        raise AppError("not_found", detail="That checklist item could not be found.")
    return item


def get_slot(db: Session, group: Group, slot_id: str) -> RoleSlot:
    """One role slot of this group, or 404."""
    slot = db.get(RoleSlot, slot_id)
    if slot is None or slot.group_id != group.id:
        raise AppError("not_found", detail="That role slot could not be found.")
    return slot


def add_item(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    event_id: str,
    label: str,
    quantity: int = 1,
    section: str = "bring",
    note: str = "",
) -> ChecklistItem:
    """Add a bring/prepare line to an event's list (B-088)."""
    if section not in ("bring", "prepare"):
        raise AppError("validation_error", detail="A checklist line is 'bring' or 'prepare'.")
    if not label or not label.strip():
        raise AppError("validation_error", detail="A checklist line needs its words.")
    if quantity < 1:
        raise AppError("validation_error", detail="A quantity is at least one.")
    event = _visible_event(db, group, event_id, viewer_role)
    item = ChecklistItem(
        group_id=group.id, event_id=event.id, section=section,
        label=label.strip()[:240], quantity=quantity, note=note,
        created_by=actor_label,
    )
    db.add(item)
    db.flush()
    audit.record(
        db, actor=actor_label, action="checklist.added",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"item_id": item.id, "label": item.label},
    )
    db.commit()
    return item


def list_items(db: Session, group: Group, event: Event) -> list[ChecklistItem]:
    """An event's whole list: bring first, then prepare, each
    in the order lines were added."""
    return (
        db.query(ChecklistItem)
        .filter(ChecklistItem.event_id == event.id)
        .order_by(ChecklistItem.section, ChecklistItem.created_at, ChecklistItem.id)
        .all()
    )


def update_item(
    db: Session, *, actor_label: str, group: Group, item_id: str, changes: dict
) -> ChecklistItem:
    """Edit a line: its words, quantity, note, or done state."""
    item = get_item(db, group, item_id)
    if "label" in changes:
        if not str(changes["label"]).strip():
            raise AppError("validation_error", detail="A checklist line needs its words.")
        item.label = str(changes["label"]).strip()[:240]
    if "quantity" in changes:
        if int(changes["quantity"]) < 1:
            raise AppError("validation_error", detail="A quantity is at least one.")
        item.quantity = int(changes["quantity"])
    if "note" in changes:
        item.note = str(changes["note"])
    if "done" in changes:
        item.done = bool(changes["done"])
    audit.record(
        db, actor=actor_label, action="checklist.updated",
        group_id=group.id, entity_type="checklist_item", entity_id=item.id,
        detail={"fields": sorted(changes)},
    )
    db.commit()
    return item


def _claimer(db: Session, group: Group, user_id: str | None, seat_label: str) -> tuple[str | None, str]:
    """Resolve a claimer to (id, display words): a person's id +
    their name/email words; a seat claims in its own name."""
    if user_id is not None:
        found = planning_service.member_label(db, group, user_id)
        if found is None:
            raise AppError("forbidden", detail="Only a member of this group can claim.")
        _user, label = found
        return user_id, label
    return None, seat_label


def claim_item(
    db: Session,
    *,
    actor_label: str,
    user_id: str | None,
    group: Group,
    item_id: str,
    claim: bool,
) -> ChecklistItem:
    """Claim a line (or release it). Claiming a line someone
    else holds is refused — a claim is a promise, and two
    promises for one line is how things get forgotten."""
    item = get_item(db, group, item_id)
    if claim:
        held = item.claimed_by_id is not None or bool(item.claimed_by_label)
        if held and item.claimed_by_id != user_id:
            raise AppError("conflict", detail="That line is already claimed by someone else.")
        claimer_id, claimer_label = _claimer(db, group, user_id, actor_label)
        item.claimed_by_id = claimer_id
        item.claimed_by_label = claimer_label
        item.claimed_at = now_utc()
    else:
        item.claimed_by_id = None
        item.claimed_by_label = ""
        item.claimed_at = None
    audit.record(
        db, actor=actor_label,
        action="checklist.claimed" if claim else "checklist.unclaimed",
        group_id=group.id, entity_type="checklist_item", entity_id=item.id,
    )
    db.commit()
    return item


def add_role_slots(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    event_id: str,
    role_name: str,
    slots: int = 1,
) -> list[RoleSlot]:
    """Open ``slots`` copies of one named job on an event
    (B-088) — two drivers needed means two rows, so each claim
    is one person's."""
    if not role_name or not role_name.strip():
        raise AppError("validation_error", detail="A role slot needs its name.")
    if slots < 1:
        raise AppError("validation_error", detail="An event needs at least one slot.")
    event = _visible_event(db, group, event_id, viewer_role)
    rows = []
    for _ in range(slots):
        slot = RoleSlot(
            group_id=group.id, event_id=event.id,
            role_name=role_name.strip()[:80], created_by=actor_label,
        )
        db.add(slot)
        rows.append(slot)
    db.flush()
    audit.record(
        db, actor=actor_label, action="role.opened",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"role_name": role_name.strip(), "slots": slots},
    )
    db.commit()
    return rows


def list_slots(db: Session, group: Group, event: Event) -> list[RoleSlot]:
    """An event's role slots, in the order they were opened."""
    return (
        db.query(RoleSlot)
        .filter(RoleSlot.event_id == event.id)
        .order_by(RoleSlot.created_at, RoleSlot.id)
        .all()
    )


def claim_slot(
    db: Session,
    *,
    actor_label: str,
    user_id: str | None,
    group: Group,
    slot_id: str,
    claim: bool,
) -> RoleSlot:
    """Claim (or release) one role slot."""
    slot = get_slot(db, group, slot_id)
    if claim:
        held = slot.claimed_by_id is not None or bool(slot.claimed_by_label)
        if held and slot.claimed_by_id != user_id:
            raise AppError("conflict", detail="That role is already claimed by someone else.")
        claimer_id, claimer_label = _claimer(db, group, user_id, actor_label)
        slot.claimed_by_id = claimer_id
        slot.claimed_by_label = claimer_label
        slot.claimed_at = now_utc()
    else:
        slot.claimed_by_id = None
        slot.claimed_by_label = ""
        slot.claimed_at = None
    audit.record(
        db, actor=actor_label,
        action="role.claimed" if claim else "role.unclaimed",
        group_id=group.id, entity_type="role_slot", entity_id=slot.id,
    )
    db.commit()
    return slot


def _normalise_label(label: str) -> str:
    """The merge key for supplies: case- and whitespace-folded,
    so "Cups", "cups " and "CUPS" are the same shopping line —
    and genuinely different words stay different lines."""
    return " ".join(label.lower().split())


def merged_supplies(
    db: Session, group: Group, *, start: datetime, end: datetime
) -> list[dict]:
    """The "buy once" view (B-088): every checklist line of the
    group's non-cancelled, non-tombstoned events starting in
    [start, end), folded by normalised label: total quantity,
    and which events need how much. Sorted by label so the
    list is stable enough to shop from."""
    events = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.deleted_at.is_(None),
            Event.status != "cancelled",
            Event.starts_at >= start,
            Event.starts_at < end,
        )
        .order_by(Event.starts_at, Event.id)
        .all()
    )
    merged: dict[str, dict] = {}
    for event in events:
        for item in list_items(db, group, event):
            key = _normalise_label(item.label)
            entry = merged.setdefault(
                key,
                {"label": item.label, "total_quantity": 0, "events": []},
            )
            entry["total_quantity"] += item.quantity
            entry["events"].append(
                {
                    "event_id": event.id,
                    "event_title": event.title,
                    "quantity": item.quantity,
                    "claimed": item.claimed_by_id is not None
                    or bool(item.claimed_by_label),
                }
            )
    return [merged[key] for key in sorted(merged)]
