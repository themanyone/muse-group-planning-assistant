"""Sub-groups + chapters (roadmap B-105), and THE scoping rule.

A group can hold named sub-groups — the choir inside the club,
the kitchen crew inside the nonprofit. Membership is INHERITED:
only an active group member can be enrolled at all, and a
sub-group row may carry an optional per-sub-group role that
overrides the group role for that sub-group's work.

Scoping: an event or task with a ``subgroup_id`` belongs to that
sub-group. THE RULE — a scoped item is visible only to

- members enrolled in that sub-group, and
- group organisers and above (they run the whole group), and
- nobody else: not other members, not guests, not members of a
  different sub-group. On member-facing reads a scoped item the
  caller may not see is indistinguishable from a missing one
  (404 / absent from lists), exactly like a private group.

The rule is enforced on the member-facing surfaces: event list
+ detail, task list + detail, the calendar views (agenda, month,
tonight, conflicts), planning search, and the announcements
board. Two organiser-published broadcasts deliberately stay
group-wide in this phase and say so in the memory map: the
iCal feed (issued by an organiser for the whole group) and the
weekly digest (one stored digest per group, B-099).
"""

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import ROLE_ORDER, Group, role_at_least
from gpa.models.identity import User
from gpa.models.nonprofit import SubGroup, SubGroupMembership
from gpa.services import audit
from gpa.services.groups import active_membership


def get_subgroup(db: Session, group: Group, subgroup_id: str) -> SubGroup:
    """One sub-group of this group, or 404 (another group's
    sub-group is indistinguishable from a missing one)."""
    subgroup = db.get(SubGroup, subgroup_id)
    if subgroup is None or subgroup.group_id != group.id:
        raise AppError("not_found", detail="That sub-group could not be found.")
    return subgroup


def create_subgroup(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    name: str,
    description: str = "",
) -> SubGroup:
    """Create a named sub-group. Names are unique per group —
    "the choir" cannot be two different rooms."""
    name = (name or "").strip()
    if not name:
        raise AppError("validation_error", detail="A sub-group needs a name.")
    existing = (
        db.query(SubGroup)
        .filter(SubGroup.group_id == group.id, SubGroup.name == name[:120])
        .one_or_none()
    )
    if existing is not None:
        raise AppError("conflict", detail="This group already has a sub-group with that name.")
    subgroup = SubGroup(
        group_id=group.id, name=name[:120], description=description,
        created_by=actor_label,
    )
    db.add(subgroup)
    db.flush()
    audit.record(
        db, actor=actor_label, action="subgroup.created",
        group_id=group.id, entity_type="subgroup", entity_id=subgroup.id,
        detail={"name": subgroup.name},
    )
    db.commit()
    return subgroup


def list_subgroups(db: Session, group: Group) -> list[SubGroup]:
    """The group's sub-groups, name order (stable for shelves)."""
    return (
        db.query(SubGroup)
        .filter(SubGroup.group_id == group.id)
        .order_by(SubGroup.name, SubGroup.id)
        .all()
    )


def update_subgroup(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    subgroup_id: str,
    changes: dict,
) -> SubGroup:
    """Rename / re-describe a sub-group (name stays unique)."""
    subgroup = get_subgroup(db, group, subgroup_id)
    if "name" in changes:
        name = str(changes["name"]).strip()
        if not name:
            raise AppError("validation_error", detail="A sub-group needs a name.")
        clash = (
            db.query(SubGroup)
            .filter(
                SubGroup.group_id == group.id,
                SubGroup.name == name[:120],
                SubGroup.id != subgroup.id,
            )
            .one_or_none()
        )
        if clash is not None:
            raise AppError("conflict", detail="This group already has a sub-group with that name.")
        subgroup.name = name[:120]
    if "description" in changes:
        subgroup.description = str(changes["description"])
    audit.record(
        db, actor=actor_label, action="subgroup.updated",
        group_id=group.id, entity_type="subgroup", entity_id=subgroup.id,
        detail={"fields": sorted(changes)},
    )
    db.commit()
    return subgroup


def delete_subgroup(
    db: Session, *, actor_label: str, group: Group, subgroup_id: str
) -> None:
    """Remove a sub-group and its enrolments.

    Refused while anything is still scoped to it — events, tasks,
    announcements — because un-scoping them silently would WIDEN
    their audience, the one direction a delete must never leak.
    The organiser un-scopes or moves the content first; the
    refusal says exactly how much of what is in the way.
    """
    from gpa.models.events import Event
    from gpa.models.nonprofit import Announcement
    from gpa.models.planning import Task

    subgroup = get_subgroup(db, group, subgroup_id)
    blockers = {
        "events": db.query(Event).filter(Event.subgroup_id == subgroup.id).count(),
        "tasks": db.query(Task).filter(Task.subgroup_id == subgroup.id).count(),
        "announcements": db.query(Announcement)
        .filter(Announcement.subgroup_id == subgroup.id)
        .count(),
    }
    blockers = {kind: count for kind, count in blockers.items() if count}
    if blockers:
        parts = ", ".join(f"{count} {kind}" for kind, count in sorted(blockers.items()))
        raise AppError(
            "conflict",
            detail=(
                f"This sub-group still scopes {parts}. Move or un-scope "
                "them first — deleting here must never widen who can see them."
            ),
        )
    db.query(SubGroupMembership).filter(
        SubGroupMembership.subgroup_id == subgroup.id
    ).delete()
    db.delete(subgroup)
    audit.record(
        db, actor=actor_label, action="subgroup.deleted",
        group_id=group.id, entity_type="subgroup", entity_id=subgroup.id,
        detail={"name": subgroup.name},
    )
    db.commit()


# ------------------------------------------------------------------
# Enrolment (membership inherited)
# ------------------------------------------------------------------


def enrolment_of(
    db: Session, subgroup_id: str, user_id: str
) -> SubGroupMembership | None:
    """One person's enrolment row in one sub-group, if any."""
    return (
        db.query(SubGroupMembership)
        .filter(
            SubGroupMembership.subgroup_id == subgroup_id,
            SubGroupMembership.user_id == user_id,
        )
        .one_or_none()
    )


def ensure_enrolment(
    db: Session,
    *,
    group: Group,
    subgroup: SubGroup,
    user_id: str,
    role: str | None = None,
) -> SubGroupMembership:
    """Enrol without committing (the importer's road, B-110).

    Creates the row when missing; an existing row keeps its
    per-sub-group role unless a new one is named. The caller
    owns validation (the person must be a group member) and the
    transaction.
    """
    row = enrolment_of(db, subgroup.id, user_id)
    if row is None:
        row = SubGroupMembership(
            subgroup_id=subgroup.id, group_id=group.id,
            user_id=user_id, role=role,
        )
        db.add(row)
        db.flush()
    elif role is not None:
        row.role = role
    return row


def enroll_member(
    db: Session,
    *,
    actor_label: str,
    actor_user_id: str | None,
    actor_role: str | None,
    group: Group,
    subgroup_id: str,
    user_id: str,
    role: str | None = None,
) -> SubGroupMembership:
    """Enrol one group member in one sub-group.

    An organiser manages anyone's enrolment; a member may enrol
    THEMSELVES (joining the choir is the member's own act). The
    per-sub-group role, when given, is an organiser's grant.
    """
    subgroup = get_subgroup(db, group, subgroup_id)
    is_organiser = actor_role is not None and role_at_least(actor_role, "organiser")
    is_self = actor_user_id is not None and actor_user_id == user_id
    if not (is_organiser or is_self):
        raise AppError(
            "forbidden",
            detail="Enrolling someone else takes an organiser; you may enrol yourself.",
        )
    if role is not None:
        if role not in ROLE_ORDER:
            raise AppError("validation_error", detail="That is not a role.")
        if not is_organiser:
            raise AppError(
                "forbidden",
                detail="A per-sub-group role is an organiser's grant.",
            )
    if active_membership(db, user_id, group.id) is None:
        raise AppError(
            "validation_error",
            detail="Only a member of this group can join its sub-groups.",
        )
    if enrolment_of(db, subgroup.id, user_id) is not None:
        raise AppError("conflict", detail="That person is already in this sub-group.")
    row = SubGroupMembership(
        subgroup_id=subgroup.id, group_id=group.id, user_id=user_id, role=role
    )
    db.add(row)
    db.flush()
    audit.record(
        db, actor=actor_label, action="subgroup.member_enrolled",
        group_id=group.id, entity_type="subgroup", entity_id=subgroup.id,
        detail={"user_id": user_id, "role": role},
    )
    db.commit()
    return row


def unenroll_member(
    db: Session,
    *,
    actor_label: str,
    actor_user_id: str | None,
    actor_role: str | None,
    group: Group,
    subgroup_id: str,
    user_id: str,
) -> None:
    """Take one person out of one sub-group (organiser, or the
    person themselves leaving)."""
    subgroup = get_subgroup(db, group, subgroup_id)
    is_organiser = actor_role is not None and role_at_least(actor_role, "organiser")
    is_self = actor_user_id is not None and actor_user_id == user_id
    if not (is_organiser or is_self):
        raise AppError(
            "forbidden",
            detail="Removing someone else takes an organiser; you may remove yourself.",
        )
    row = enrolment_of(db, subgroup.id, user_id)
    if row is None:
        raise AppError("not_found", detail="That person is not in this sub-group.")
    db.delete(row)
    audit.record(
        db, actor=actor_label, action="subgroup.member_removed",
        group_id=group.id, entity_type="subgroup", entity_id=subgroup.id,
        detail={"user_id": user_id},
    )
    db.commit()


def list_members(
    db: Session, group: Group, subgroup: SubGroup
) -> list[dict]:
    """The sub-group's enrolled members, with both roles and the
    effective one (the per-sub-group role when set, else the
    group role)."""
    rows = (
        db.query(SubGroupMembership)
        .filter(SubGroupMembership.subgroup_id == subgroup.id)
        .order_by(SubGroupMembership.created_at, SubGroupMembership.id)
        .all()
    )
    out = []
    for row in rows:
        user = db.get(User, row.user_id)
        membership = active_membership(db, row.user_id, group.id)
        group_role = membership.role if membership else None
        out.append(
            {
                "user_id": row.user_id,
                "name": (user.name or "") if user else "",
                "email": user.email if user else "",
                "group_role": group_role,
                "subgroup_role": row.role,
                "effective_role": row.role or group_role,
            }
        )
    return out


def member_count(db: Session, subgroup: SubGroup) -> int:
    """How many people are enrolled in one sub-group."""
    return (
        db.query(SubGroupMembership)
        .filter(SubGroupMembership.subgroup_id == subgroup.id)
        .count()
    )


# ------------------------------------------------------------------
# THE scoping rule (used by every member-facing read surface)
# ------------------------------------------------------------------


def enrolled_subgroup_ids(db: Session, group: Group, user_id: str | None) -> set[str]:
    """The sub-groups of this group one person is enrolled in."""
    if user_id is None:
        return set()
    return {
        row.subgroup_id
        for row in db.query(SubGroupMembership)
        .filter(
            SubGroupMembership.group_id == group.id,
            SubGroupMembership.user_id == user_id,
        )
        .all()
    }


def visible_subgroup_ids(
    db: Session, group: Group, *, user_id: str | None, role: str | None
) -> set[str] | None:
    """The sub-group ids whose scoped content this viewer may see.

    Returns None for UNRESTRICTED — organisers and above run the
    whole group, so scoping does not narrow their view. Everyone
    else gets exactly their enrolments (an empty set for seats
    and non-members: group-wide content only).
    """
    if role is not None and role_at_least(role, "organiser"):
        return None
    return enrolled_subgroup_ids(db, group, user_id)


def can_see_scoped(
    subgroup_id: str | None, visible_ids: set[str] | None
) -> bool:
    """The rule as a predicate over one item's scope: unscoped
    items are group-wide; scoped items need the viewer's set to
    contain them (or the viewer to be unrestricted)."""
    if subgroup_id is None:
        return True
    if visible_ids is None:
        return True
    return subgroup_id in visible_ids
