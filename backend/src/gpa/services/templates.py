"""Templates (roadmap B-091).

A template is a plan's SHAPE with the dates left relative:
an event outline, the tasks around it (their due moments as
day-offsets from the event's start), its checklist lines, and
its role slots. Instantiating a template with a start moment
builds the real thing through the same doors direct entry
uses — the event through the events service (which records
its manual source), the tasks/checklist/roles through the
planning services — so a template-born plan is
indistinguishable in provenance from a hand-built one.

The global starter set (meeting, club night, trip, fundraiser,
class, match, celebration) ships as seed data: rows with
``group_id`` NULL, loaded idempotently by natural key (name)
from the seed chassis — every group can read them; no group
can edit them.
"""

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.planning import ChecklistItem, RoleSlot, Task, Template
from gpa.services import audit
from gpa.services import events as event_service
from gpa.services import planning as planning_service

# The starter set. Each payload follows the grammar in
# models/planning.py's Template docstring. These are the
# group's own words for common shapes — plain, editable
# starting points, not prescriptions.
STARTER_TEMPLATES: list[dict] = [
    {
        "name": "meeting",
        "description": "A working meeting with an agenda task and a chair.",
        "payload": {
            "event": {
                "title": "Meeting", "description": "",
                "duration_minutes": 60, "all_day": False,
                "place_name": "", "event_type": "meeting", "bring_list": "",
                "cost_text": "",
            },
            "tasks": [
                {"title": "Circulate the agenda", "notes": "", "due_offset_days": -2},
                {"title": "Book the room", "notes": "", "due_offset_days": -5},
            ],
            "checklist": [{"label": "Notes from last time", "quantity": 1, "section": "prepare"}],
            "roles": [{"role_name": "chair", "slots": 1}, {"role_name": "note-taker", "slots": 1}],
        },
    },
    {
        "name": "club night",
        "description": "An evening social: doors, music, and a door rota.",
        "payload": {
            "event": {
                "title": "Club night", "description": "",
                "duration_minutes": 240, "all_day": False,
                "place_name": "", "event_type": "social", "bring_list": "",
                "cost_text": "",
            },
            "tasks": [
                {"title": "Book the hall", "notes": "", "due_offset_days": -14},
                {"title": "Sort the playlist", "notes": "", "due_offset_days": -3},
                {"title": "Staff the door rota", "notes": "", "due_offset_days": -2},
            ],
            "checklist": [
                {"label": "Projector", "quantity": 1, "section": "bring"},
                {"label": "Cups", "quantity": 24, "section": "bring"},
            ],
            "roles": [{"role_name": "host", "slots": 1}, {"role_name": "door", "slots": 2}],
        },
    },
    {
        "name": "trip",
        "description": "A group trip: transport, beds, and a kitty.",
        "payload": {
            "event": {
                "title": "Trip", "description": "",
                "duration_minutes": 2880, "all_day": False,
                "place_name": "", "event_type": "trip", "bring_list": "",
                "cost_text": "",
            },
            "tasks": [
                {"title": "Book transport", "notes": "", "due_offset_days": -21},
                {"title": "Book accommodation", "notes": "", "due_offset_days": -21},
                {"title": "Agree the kitty", "notes": "", "due_offset_days": -7},
            ],
            "checklist": [{"label": "First aid kit", "quantity": 1, "section": "bring"}],
            "roles": [{"role_name": "driver", "slots": 2}, {"role_name": "navigator", "slots": 1}],
        },
    },
    {
        "name": "fundraiser",
        "description": "A fundraiser with tickets, a float, and a raffle.",
        "payload": {
            "event": {
                "title": "Fundraiser", "description": "",
                "duration_minutes": 300, "all_day": False,
                "place_name": "", "event_type": "fundraiser", "bring_list": "",
                "cost_text": "",
            },
            "tasks": [
                {"title": "Set the ticket price", "notes": "", "due_offset_days": -14},
                {"title": "Print the raffle tickets", "notes": "", "due_offset_days": -5},
                {"title": "Arrange the cash float", "notes": "", "due_offset_days": -1},
            ],
            "checklist": [
                {"label": "Card reader", "quantity": 1, "section": "bring"},
                {"label": "Collection buckets", "quantity": 4, "section": "bring"},
            ],
            "roles": [{"role_name": "host", "slots": 1}, {"role_name": "treasurer", "slots": 1}],
        },
    },
    {
        "name": "class",
        "description": "A taught session with materials and a register.",
        "payload": {
            "event": {
                "title": "Class", "description": "",
                "duration_minutes": 120, "all_day": False,
                "place_name": "", "event_type": "class", "bring_list": "",
                "cost_text": "",
            },
            "tasks": [
                {"title": "Prepare the materials", "notes": "", "due_offset_days": -4},
                {"title": "Print the handouts", "notes": "", "due_offset_days": -2},
            ],
            "checklist": [{"label": "Register sheet", "quantity": 1, "section": "prepare"}],
            "roles": [{"role_name": "teacher", "slots": 1}, {"role_name": "assistant", "slots": 1}],
        },
    },
    {
        "name": "match",
        "description": "A fixture: pitch, kit, and someone to keep score.",
        "payload": {
            "event": {
                "title": "Match", "description": "",
                "duration_minutes": 150, "all_day": False,
                "place_name": "", "event_type": "sport", "bring_list": "",
                "cost_text": "",
            },
            "tasks": [
                {"title": "Confirm the pitch", "notes": "", "due_offset_days": -6},
                {"title": "Wash the kit", "notes": "", "due_offset_days": -1},
            ],
            "checklist": [
                {"label": "Match ball", "quantity": 2, "section": "bring"},
                {"label": "Water bottles", "quantity": 12, "section": "bring"},
            ],
            "roles": [{"role_name": "captain", "slots": 1}, {"role_name": "referee", "slots": 1}],
        },
    },
    {
        "name": "celebration",
        "description": "A party: food, drink, music, and a cake moment.",
        "payload": {
            "event": {
                "title": "Celebration", "description": "",
                "duration_minutes": 300, "all_day": False,
                "place_name": "", "event_type": "social", "bring_list": "",
                "cost_text": "",
            },
            "tasks": [
                {"title": "Order the cake", "notes": "", "due_offset_days": -5},
                {"title": "Send the invitations", "notes": "", "due_offset_days": -14},
                {"title": "Plan the playlist", "notes": "", "due_offset_days": -3},
            ],
            "checklist": [
                {"label": "Candles", "quantity": 1, "section": "bring"},
                {"label": "Plates", "quantity": 24, "section": "bring"},
            ],
            "roles": [{"role_name": "host", "slots": 1}, {"role_name": "cook", "slots": 2}],
        },
    },
]


def seed_starter_templates(db: Session) -> dict:
    """Seed the global starter set, idempotently by name
    (B-091): a name already present is left exactly as it is —
    seeding never overwrites a row. Called by the seed chassis
    (services/seed.py) so every seeded database carries them."""
    created = 0
    present = 0
    for spec in STARTER_TEMPLATES:
        existing = (
            db.query(Template)
            .filter(Template.group_id.is_(None), Template.name == spec["name"])
            .one_or_none()
        )
        if existing is None:
            db.add(
                Template(
                    group_id=None,
                    name=spec["name"],
                    description=spec["description"],
                    payload_json=json.dumps(spec["payload"]),
                    created_by="system:seed",
                )
            )
            created += 1
        else:
            present += 1
    db.flush()
    return {"created": created, "present": present}


def parse_payload(template: Template) -> dict:
    """A template's payload as a dict ({} when unreadable)."""
    try:
        payload = json.loads(template.payload_json or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def list_templates(db: Session, group: Group) -> list[Template]:
    """The templates a group can use: the global starters plus
    the group's own, starters first, then by name."""
    rows = (
        db.query(Template)
        .filter((Template.group_id.is_(None)) | (Template.group_id == group.id))
        .all()
    )
    rows.sort(key=lambda t: (t.group_id is not None, t.name))
    return rows


def get_template(db: Session, group: Group, template_id: str) -> Template:
    """One usable template, or 404 — another group's own
    template is indistinguishable from a missing one."""
    template = db.get(Template, template_id)
    if template is None or (
        template.group_id is not None and template.group_id != group.id
    ):
        raise AppError("not_found", detail="That template could not be found.")
    return template


def create_template(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    name: str,
    description: str,
    payload: dict,
) -> Template:
    """Save a plan shape as one of the group's own templates."""
    if not name or not name.strip():
        raise AppError("validation_error", detail="A template needs a name.")
    if not isinstance(payload, dict) or "event" not in payload:
        raise AppError(
            "validation_error",
            detail="A template payload needs at least its event shape.",
        )
    existing = (
        db.query(Template)
        .filter(Template.group_id == group.id, Template.name == name.strip())
        .one_or_none()
    )
    if existing is not None:
        raise AppError("conflict", detail="This group already has a template by that name.")
    template = Template(
        group_id=group.id, name=name.strip()[:120], description=description,
        payload_json=json.dumps(payload), created_by=actor_label,
    )
    db.add(template)
    db.flush()
    audit.record(
        db, actor=actor_label, action="template.created",
        group_id=group.id, entity_type="template", entity_id=template.id,
        detail={"name": template.name},
    )
    db.commit()
    return template


def template_from_event(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    event_id: str,
    name: str,
) -> Template:
    """Snapshot an existing event + its plan as a template
    (B-091): the event's shape, its tasks with due moments
    re-expressed as day-offsets from the event's start, its
    checklist lines (unclaimed — a template carries shapes, not
    people's promises), and its role names with slot counts."""
    event = planning_service.visible_event(db, group, event_id, viewer_role)
    duration = event.ends_at - event.starts_at
    payload: dict = {
        "event": {
            "title": event.title,
            "description": event.description,
            "duration_minutes": int(duration.total_seconds() // 60),
            "all_day": event.all_day,
            "place_name": event.place_name,
            "event_type": event.event_type,
            "bring_list": event.bring_list,
            "cost_text": event.cost_text,
        },
        "tasks": [],
        "checklist": [],
        "roles": [],
    }
    for task in planning_service.list_tasks(db, group, event_id=event.id):
        offset = None
        if task.due_at is not None:
            offset = (task.due_at.date() - event.starts_at.date()).days
        payload["tasks"].append(
            {"title": task.title, "notes": task.notes, "due_offset_days": offset}
        )
    from gpa.services import checklists as checklist_service

    for item in checklist_service.list_items(db, group, event):
        payload["checklist"].append(
            {"label": item.label, "quantity": item.quantity, "section": item.section}
        )
    role_counts: dict[str, int] = {}
    for slot in checklist_service.list_slots(db, group, event):
        role_counts[slot.role_name] = role_counts.get(slot.role_name, 0) + 1
    payload["roles"] = [
        {"role_name": role_name, "slots": count}
        for role_name, count in role_counts.items()
    ]
    return create_template(
        db, actor_label=actor_label, group=group, name=name,
        description=f"Saved from {event.title}", payload=payload,
    )


def instantiate_template(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    template_id: str,
    starts_at: datetime,
    title: str | None = None,
) -> dict:
    """Build a real plan from a template at a new start moment
    (B-091): the event through direct entry (manual source and
    all — that door commits the event, as it does for a typed
    entry), then its tasks (due = start + offset, unclaimed —
    instantiation never assigns people either), checklist
    lines, and role slots in one commit of their own."""
    template = get_template(db, group, template_id)
    payload = parse_payload(template)
    shape = payload.get("event", {})
    duration = timedelta(minutes=int(shape.get("duration_minutes") or 120))
    event = event_service.create_event(
        db,
        actor_label=actor_label,
        group=group,
        data={
            "title": (title or shape.get("title") or template.name)[:240],
            "description": shape.get("description", ""),
            "starts_at": starts_at,
            "ends_at": starts_at + duration,
            "timezone": group.home_timezone or "UTC",
            "all_day": bool(shape.get("all_day", False)),
            "place_name": shape.get("place_name", ""),
            "cost_text": shape.get("cost_text", ""),
            "bring_list": shape.get("bring_list", ""),
            "event_type": shape.get("event_type", ""),
            "status": "published",
        },
    )
    tasks: list[Task] = []
    for spec in payload.get("tasks", []):
        offset = spec.get("due_offset_days")
        due_at = starts_at + timedelta(days=offset) if offset is not None else None
        task = Task(
            group_id=group.id, event_id=event.id,
            title=str(spec.get("title") or "")[:240],
            notes=str(spec.get("notes") or ""),
            due_at=due_at, status="open", created_by=actor_label,
        )
        db.add(task)
        tasks.append(task)
    items: list[ChecklistItem] = []
    for spec in payload.get("checklist", []):
        item = ChecklistItem(
            group_id=group.id, event_id=event.id,
            section=spec.get("section") if spec.get("section") in ("bring", "prepare") else "bring",
            label=str(spec.get("label") or "")[:240],
            quantity=int(spec.get("quantity") or 1),
            created_by=actor_label,
        )
        db.add(item)
        items.append(item)
    slots: list[RoleSlot] = []
    for spec in payload.get("roles", []):
        for _ in range(int(spec.get("slots") or 1)):
            slot = RoleSlot(
                group_id=group.id, event_id=event.id,
                role_name=str(spec.get("role_name") or "")[:80],
                created_by=actor_label,
            )
            db.add(slot)
            slots.append(slot)
    db.flush()
    audit.record(
        db, actor=actor_label, action="template.instantiated",
        group_id=group.id, entity_type="template", entity_id=template.id,
        detail={"event_id": event.id, "tasks": len(tasks)},
    )
    db.commit()
    return {"template": template, "event": event, "tasks": tasks, "checklist": items, "roles": slots}
