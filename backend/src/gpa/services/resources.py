"""Resources + bookings (roadmap B-108; lands B-057's other half).

The hall, the projector, the van: items a group owns or borrows,
each with a KEEPER (the person responsible for it) and bookings
per event. Phase 4's conflict engine deliberately stopped at
hosts, places, and travel — "no resource conflicts are computed
yet" — because the resource model did not exist. It exists now,
and double-booking is a **B-057 conflict flag**: surfaced in the
calendar's conflicts view and echoed on the booking that creates
it, never a block. Two committees may legitimately share the
van across a handover the calendar cannot judge; the flag makes
them SEE it, the humans decide.
"""

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.nonprofit import Resource, ResourceBooking
from gpa.services import audit
from gpa.services import events as event_service
from gpa.services.planning import member_label


def get_resource(db: Session, group: Group, resource_id: str) -> Resource:
    """One resource of this group, or 404."""
    row = db.get(Resource, resource_id)
    if row is None or row.group_id != group.id:
        raise AppError("not_found", detail="That resource could not be found.")
    return row


def _keeper(db: Session, group: Group, keeper_user_id, keeper_label):
    """Validate + normalise a keeper: a member id (whose words
    become the label when none were given), or plain words."""
    if keeper_user_id is not None:
        found = member_label(db, group, keeper_user_id)
        if found is None:
            raise AppError(
                "validation_error",
                detail="The keeper must be a member of this group.",
            )
        _user, words = found
        return keeper_user_id, (keeper_label or words)
    return None, keeper_label or ""


def create_resource(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    name: str,
    notes: str = "",
    keeper_user_id: str | None = None,
    keeper_label: str = "",
) -> Resource:
    """Register a bookable item. Names are unique per group —
    "the van" cannot be two different vans."""
    name = (name or "").strip()
    if not name:
        raise AppError("validation_error", detail="A resource needs a name.")
    clash = (
        db.query(Resource)
        .filter(Resource.group_id == group.id, Resource.name == name[:160])
        .one_or_none()
    )
    if clash is not None:
        raise AppError("conflict", detail="This group already has a resource with that name.")
    keeper_user_id, keeper_label = _keeper(db, group, keeper_user_id, keeper_label)
    row = Resource(
        group_id=group.id, name=name[:160], notes=notes,
        keeper_user_id=keeper_user_id, keeper_label=keeper_label,
        created_by=actor_label,
    )
    db.add(row)
    db.flush()
    audit.record(
        db, actor=actor_label, action="resource.created",
        group_id=group.id, entity_type="resource", entity_id=row.id,
        detail={"name": row.name, "keeper_label": row.keeper_label},
    )
    db.commit()
    return row


def list_resources(db: Session, group: Group) -> list[Resource]:
    """The group's items, name order."""
    return (
        db.query(Resource)
        .filter(Resource.group_id == group.id)
        .order_by(Resource.name, Resource.id)
        .all()
    )


def update_resource(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    resource_id: str,
    changes: dict,
) -> Resource:
    """Edit an item: name (still unique), notes, keeper."""
    row = get_resource(db, group, resource_id)
    if "name" in changes:
        name = str(changes["name"]).strip()
        if not name:
            raise AppError("validation_error", detail="A resource needs a name.")
        clash = (
            db.query(Resource)
            .filter(
                Resource.group_id == group.id,
                Resource.name == name[:160],
                Resource.id != row.id,
            )
            .one_or_none()
        )
        if clash is not None:
            raise AppError("conflict", detail="This group already has a resource with that name.")
        row.name = name[:160]
    if "notes" in changes:
        row.notes = str(changes["notes"])
    if "keeper_user_id" in changes or "keeper_label" in changes:
        keeper_id, keeper_words = _keeper(
            db, group,
            changes.get("keeper_user_id", row.keeper_user_id),
            changes.get("keeper_label", row.keeper_label),
        )
        row.keeper_user_id = keeper_id
        row.keeper_label = keeper_words
    audit.record(
        db, actor=actor_label, action="resource.updated",
        group_id=group.id, entity_type="resource", entity_id=row.id,
        detail={"fields": sorted(changes)},
    )
    db.commit()
    return row


def delete_resource(
    db: Session, *, actor_label: str, group: Group, resource_id: str
) -> None:
    """Remove an item; its bookings go with it (the cascade's
    job — a booking for a thing that no longer exists is noise,
    and the audit keeps the fact)."""
    row = get_resource(db, group, resource_id)
    name = row.name
    db.query(ResourceBooking).filter(
        ResourceBooking.resource_id == row.id
    ).delete()
    db.delete(row)
    audit.record(
        db, actor=actor_label, action="resource.deleted",
        group_id=group.id, entity_type="resource", entity_id=resource_id,
        detail={"name": name},
    )
    db.commit()


# ------------------------------------------------------------------
# Bookings
# ------------------------------------------------------------------


def get_booking(db: Session, group: Group, booking_id: str) -> ResourceBooking:
    """One booking of this group, or 404."""
    row = db.get(ResourceBooking, booking_id)
    if row is None or row.group_id != group.id:
        raise AppError("not_found", detail="That booking could not be found.")
    return row


def bookings_by_event(db: Session, group: Group) -> dict[str, dict[str, str]]:
    """{event_id: {resource_id: resource_name}} for the group —
    the shape the conflict engine reads (B-057/B-108)."""
    out: dict[str, dict[str, str]] = {}
    rows = (
        db.query(ResourceBooking, Resource)
        .join(Resource, Resource.id == ResourceBooking.resource_id)
        .filter(ResourceBooking.group_id == group.id)
        .all()
    )
    for booking, resource in rows:
        out.setdefault(booking.event_id, {})[resource.id] = resource.name
    return out


def conflicts_for_booking(
    db: Session, group: Group, booking: ResourceBooking
) -> list[dict]:
    """The double-bookings THIS booking takes part in, computed
    the B-057 way: another PUBLISHED, non-deleted event holding
    the same resource over a strictly overlapping window
    (back-to-back is a plan, not a clash)."""
    resource = get_resource(db, group, booking.resource_id)
    event = db.get(Event, booking.event_id)
    if event is None or event.status != "published":
        return []
    others = (
        db.query(ResourceBooking)
        .filter(
            ResourceBooking.resource_id == booking.resource_id,
            ResourceBooking.event_id != booking.event_id,
        )
        .all()
    )
    flags = []
    for other in others:
        other_event = db.get(Event, other.event_id)
        if (
            other_event is None
            or other_event.status != "published"
            or other_event.deleted_at is not None
        ):
            continue
        overlaps = (
            event.starts_at < other_event.ends_at
            and other_event.starts_at < event.ends_at
        )
        if overlaps:
            flags.append(
                {
                    "kind": "resource_overlap",
                    "resource_id": resource.id,
                    "resource_name": resource.name,
                    "other_event_id": other_event.id,
                    "other_event_title": other_event.title,
                    "message": (
                        f"{resource.name} is double-booked: "
                        f"'{event.title}' overlaps '{other_event.title}'."
                    ),
                }
            )
    return flags


def book_resource(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    viewer_user_id,
    event_id: str,
    resource_id: str,
) -> tuple[ResourceBooking, list[dict]]:
    """Book one resource for one event. Returns the booking
    AND the conflicts it creates — the booker sees the clash in
    the same response that makes it (flags, never blocks)."""
    event = event_service.get_visible_event(
        db, group, event_id, viewer_role, viewer_user_id=viewer_user_id
    )
    resource = get_resource(db, group, resource_id)
    if event.status == "cancelled":
        raise AppError(
            "validation_error",
            detail="A cancelled event holds no bookings.",
        )
    existing = (
        db.query(ResourceBooking)
        .filter(
            ResourceBooking.resource_id == resource.id,
            ResourceBooking.event_id == event.id,
        )
        .one_or_none()
    )
    if existing is not None:
        raise AppError(
            "conflict",
            detail=f"{resource.name} is already booked for this event.",
        )
    booking = ResourceBooking(
        group_id=group.id, resource_id=resource.id,
        event_id=event.id, booked_by=actor_label,
    )
    db.add(booking)
    db.flush()
    audit.record(
        db, actor=actor_label, action="resource.booked",
        group_id=group.id, entity_type="resource", entity_id=resource.id,
        detail={"event_id": event.id, "booking_id": booking.id},
    )
    db.commit()
    return booking, conflicts_for_booking(db, group, booking)


def unbook(
    db: Session, *, actor_label: str, group: Group, booking_id: str
) -> None:
    """Release one booking."""
    booking = get_booking(db, group, booking_id)
    db.delete(booking)
    audit.record(
        db, actor=actor_label, action="resource.unbooked",
        group_id=group.id, entity_type="resource", entity_id=booking.resource_id,
        detail={"event_id": booking.event_id, "booking_id": booking_id},
    )
    db.commit()


def list_bookings_for_event(
    db: Session, group: Group, event: Event
) -> list[tuple[ResourceBooking, Resource]]:
    """An event's bookings, with their resources, name order."""
    return (
        db.query(ResourceBooking, Resource)
        .join(Resource, Resource.id == ResourceBooking.resource_id)
        .filter(ResourceBooking.event_id == event.id)
        .order_by(Resource.name, ResourceBooking.id)
        .all()
    )
