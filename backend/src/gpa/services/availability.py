"""Availability finder (roadmap B-090).

A group proposes moments; members mark can or can't against
each; the finder ranks the moments by how many can come. That
is the whole design, and its boundary is the point: a mark is
a boolean about a PROPOSED slot. Nobody's private calendar is
read, connected, inferred, or stored — there is nothing here
to leak, because there is nothing here but the marks.

The ranking is deterministic: most "can" first, then fewest
"can't", then earliest — so two reads of the same marks always
rank the same, and a golden test can pin the order exactly.
"""

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.planning import AvailabilityMark, AvailabilitySlot
from gpa.services import audit
from gpa.services import planning as planning_service


def get_slot(db: Session, group: Group, slot_id: str) -> AvailabilitySlot:
    """One proposed slot of this group, or 404."""
    slot = db.get(AvailabilitySlot, slot_id)
    if slot is None or slot.group_id != group.id:
        raise AppError("not_found", detail="That proposed time could not be found.")
    return slot


def propose_slot(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    starts_at,
    ends_at,
    label: str = "",
    event_id: str | None = None,
) -> AvailabilitySlot:
    """Propose a moment for the group to mark against (B-090)."""
    if ends_at <= starts_at:
        raise AppError("validation_error", detail="A proposed time must end after it starts.")
    if event_id is not None:
        planning_service.visible_event(db, group, event_id, viewer_role)
    slot = AvailabilitySlot(
        group_id=group.id, event_id=event_id, label=label.strip()[:240],
        starts_at=starts_at, ends_at=ends_at, created_by=actor_label,
    )
    db.add(slot)
    db.flush()
    audit.record(
        db, actor=actor_label, action="availability.proposed",
        group_id=group.id, entity_type="availability_slot", entity_id=slot.id,
        detail={"event_id": event_id},
    )
    db.commit()
    return slot


def mark_slot(
    db: Session,
    *,
    actor_label: str,
    user_id: str | None,
    seat_id: str | None,
    group: Group,
    slot_id: str,
    can_attend: bool,
) -> AvailabilityMark:
    """Mark can/can't against one slot (B-090): an upsert under
    the (slot, member) unique constraint — re-marking updates
    the member's one mark, it never stacks a second."""
    slot = get_slot(db, group, slot_id)
    mark = (
        db.query(AvailabilityMark)
        .filter(
            AvailabilityMark.slot_id == slot.id,
            AvailabilityMark.voter_label == actor_label,
        )
        .one_or_none()
    )
    if mark is None:
        mark = AvailabilityMark(
            slot_id=slot.id, voter_label=actor_label,
            user_id=user_id, seat_id=seat_id, can_attend=can_attend,
        )
        db.add(mark)
    else:
        mark.can_attend = can_attend
        mark.updated_at = now_utc()
    db.flush()
    audit.record(
        db, actor=actor_label, action="availability.marked",
        group_id=group.id, entity_type="availability_slot", entity_id=slot.id,
        detail={"can_attend": can_attend},
    )
    db.commit()
    return mark


def _member_words(db: Session, label: str) -> str:
    """Display words for a mark's voter label ("user:<id>" →
    their name/email; a seat keeps its label)."""
    if label.startswith("user:"):
        from gpa.models.identity import User

        user = db.get(User, label[5:])
        if user is not None:
            return user.name or user.email
    return label


def ranked_availability(db: Session, group: Group) -> list[dict]:
    """Every proposed slot with its marks, RANKED (B-090):
    can-count descending, can't-count ascending, start
    ascending. The response carries who said can and who said
    can't — the group's own marks about the group's own slots,
    nothing from anywhere else."""
    slots = (
        db.query(AvailabilitySlot)
        .filter(AvailabilitySlot.group_id == group.id)
        .all()
    )
    rows: list[dict] = []
    for slot in slots:
        marks = (
            db.query(AvailabilityMark)
            .filter(AvailabilityMark.slot_id == slot.id)
            .order_by(AvailabilityMark.created_at, AvailabilityMark.id)
            .all()
        )
        can = [mark for mark in marks if mark.can_attend]
        cant = [mark for mark in marks if not mark.can_attend]
        rows.append(
            {
                "slot": slot,
                "can_count": len(can),
                "cant_count": len(cant),
                "can_members": [_member_words(db, mark.voter_label) for mark in can],
                "cant_members": [_member_words(db, mark.voter_label) for mark in cant],
            }
        )
    rows.sort(
        key=lambda row: (
            -row["can_count"], row["cant_count"], row["slot"].starts_at, row["slot"].id
        )
    )
    return rows
