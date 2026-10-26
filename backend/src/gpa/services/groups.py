"""Group services (roadmap B-015 → B-018).

Groups, memberships and the role ladder, agent seats, and the
candidate stub that proves the founding permission rule. Every
mutation is audited in the same transaction (B-019) and committed
before returning (B-003).
"""

import re

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import (
    ROLE_ORDER,
    VISIBILITIES,
    AgentSeat,
    Candidate,
    Group,
    Membership,
    role_at_least,
)
from gpa.models.identity import User
from gpa.models.nonprofit import CandidateApproval
from gpa.internal.client import call_internal
from gpa.services import audit
from gpa.services.identity import create_api_key, get_user_by_email, normalize_email


def slugify(name: str) -> str:
    """Turn a group name into a URL slug: lowercase words, hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "group"


def _unique_slug(db: Session, base: str) -> str:
    """First free slug for ``base``: base, base-2, base-3, … (B-015)."""
    candidate = base
    suffix = 2
    while db.query(Group).filter(Group.slug == candidate).one_or_none() is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def create_group(
    db: Session,
    *,
    creator: User,
    name: str,
    description: str = "",
    visibility: str = "private",
    home_timezone: str = "UTC",
    default_place: str = "",
) -> Group:
    """Create a group; the creator becomes its owner (active)."""
    if visibility not in VISIBILITIES:
        raise AppError("validation_error", detail="Visibility must be private, invite, or public-listed.")
    group = Group(
        name=name,
        slug=_unique_slug(db, slugify(name)),
        description=description,
        visibility=visibility,
        home_timezone=home_timezone,
        default_place=default_place,
    )
    db.add(group)
    db.flush()
    db.add(Membership(user_id=creator.id, group_id=group.id, role="owner", status="active"))
    audit.record(
        db, actor=f"user:{creator.id}", action="group.created",
        group_id=group.id, entity_type="group", entity_id=group.id,
        detail={"slug": group.slug},
    )
    db.commit()
    return group


def get_group(db: Session, group_id: str) -> Group:
    """Fetch a group by id or raise not_found."""
    group = db.get(Group, group_id)
    if group is None:
        raise AppError("not_found", detail="That group could not be found.")
    return group


def update_group(db: Session, *, actor_label: str, group: Group, changes: dict) -> Group:
    """Apply provided, validated changes to a group's settings."""
    if "visibility" in changes and changes["visibility"] not in VISIBILITIES:
        raise AppError("validation_error", detail="Visibility must be private, invite, or public-listed.")
    changes = dict(changes)
    # Phase 8: reminder_rules arrives as rule objects and rests
    # as JSON on the group (B-096); the schema already closed
    # the vocabulary, so this only serialises.
    if "reminder_rules" in changes:
        import json

        rules = changes.pop("reminder_rules")
        group.reminder_rules_json = json.dumps(
            [
                {"kind": rule["kind"], **({"days": rule["days"]} if rule.get("days") else {})}
                if isinstance(rule, dict) else rule
                for rule in rules
            ]
        )
    if "digest_time" in changes:
        from gpa.services.notifications import parse_hhmm

        parse_hhmm(changes["digest_time"], "digest_time")
    for field, value in changes.items():
        setattr(group, field, value)
    audit.record(
        db, actor=actor_label, action="group.updated",
        group_id=group.id, entity_type="group", entity_id=group.id,
        detail={"fields": sorted(changes)},
    )
    # Saving digest settings re-anchors the digest chain
    # (B-099): the next run lands at the new moment, and a
    # disabled digest's queued run is cancelled.
    if {"digest_weekday", "digest_time", "digest_enabled"} & set(changes) or (
        "digest_enabled" in changes
    ):
        from gpa.services import digests as digest_service

        digest_service.ensure_scheduled(db, group=group)
    db.commit()
    return group


# --- Memberships (B-016) ----------------------------------------------


def active_membership(db: Session, user_id: str, group_id: str) -> Membership | None:
    """The user's ACTIVE membership in a group, if any."""
    return (
        db.query(Membership)
        .filter(
            Membership.user_id == user_id,
            Membership.group_id == group_id,
            Membership.status == "active",
        )
        .one_or_none()
    )


def _owner_count(db: Session, group_id: str) -> int:
    """How many ACTIVE owners a group has — the last-owner rule's input."""
    return (
        db.query(Membership)
        .filter(
            Membership.group_id == group_id,
            Membership.role == "owner",
            Membership.status == "active",
        )
        .count()
    )


def invite_member(
    db: Session, *, actor: User, group: Group, email: str, role: str = "member"
) -> Membership:
    """Invite by email. A person without an account gets a
    passwordless account to claim later, plus the invited membership."""
    if role not in ROLE_ORDER:
        raise AppError("validation_error", detail="That is not a role.")
    email = normalize_email(email)
    user = get_user_by_email(db, email)
    if user is None:
        user = User(email=email)
        db.add(user)
        db.flush()
    existing = (
        db.query(Membership)
        .filter(Membership.user_id == user.id, Membership.group_id == group.id)
        .one_or_none()
    )
    if existing is not None:
        raise AppError("conflict", detail="That person is already a member or already invited.")
    membership = Membership(user_id=user.id, group_id=group.id, role=role, status="invited")
    db.add(membership)
    audit.record(
        db, actor=f"user:{actor.id}", action="membership.invited",
        group_id=group.id, entity_type="membership", entity_id=membership.id,
        detail={"email": email, "role": role},
    )
    db.commit()
    return membership


def _my_invite(db: Session, user: User, group: Group) -> Membership:
    """The caller's own invited membership, or a clean 404."""
    membership = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.group_id == group.id,
            Membership.status == "invited",
        )
        .one_or_none()
    )
    if membership is None:
        raise AppError("not_found", detail="No invitation is waiting for you in that group.")
    return membership


def accept_invite(db: Session, *, user: User, group: Group) -> Membership:
    """Accept an invitation: invited → active."""
    membership = _my_invite(db, user, group)
    membership.status = "active"
    audit.record(
        db, actor=f"user:{user.id}", action="membership.accepted",
        group_id=group.id, entity_type="membership", entity_id=membership.id,
    )
    db.commit()
    return membership


def decline_invite(db: Session, *, user: User, group: Group) -> None:
    """Decline an invitation: the row is marked declined (kept, so
    the audit story stays whole)."""
    membership = _my_invite(db, user, group)
    membership.status = "declined"
    audit.record(
        db, actor=f"user:{user.id}", action="membership.declined",
        group_id=group.id, entity_type="membership", entity_id=membership.id,
    )
    db.commit()


def _guard_last_owner(db: Session, membership: Membership) -> None:
    """The last-owner rule: a group must never be left ownerless."""
    if (
        membership.role == "owner"
        and membership.status == "active"
        and _owner_count(db, membership.group_id) <= 1
    ):
        raise AppError("last_owner")


def change_role(
    db: Session, *, actor: User, actor_role: str, group: Group, target_user_id: str, new_role: str
) -> Membership:
    """Change a member's role.

    Rules (B-016): granting or removing owner/admin powers is an
    owner-only act; other role changes need admin+. The last active
    owner can never be demoted.
    """
    if new_role not in ROLE_ORDER:
        raise AppError("validation_error", detail="That is not a role.")
    membership = active_membership(db, target_user_id, group.id)
    if membership is None:
        raise AppError("not_found", detail="That person is not an active member of this group.")
    touches_high = new_role in ("owner", "admin") or membership.role in ("owner", "admin")
    if touches_high and actor_role != "owner":
        raise AppError("forbidden", detail="Only an owner can grant or remove owner/admin roles.")
    if not touches_high and not role_at_least(actor_role, "admin"):
        raise AppError("forbidden", detail="Changing roles needs an admin or owner.")
    if membership.role == "owner" and new_role != "owner":
        _guard_last_owner(db, membership)
    membership.role = new_role
    audit.record(
        db, actor=f"user:{actor.id}", action="membership.role_changed",
        group_id=group.id, entity_type="membership", entity_id=membership.id,
        detail={"role": new_role},
    )
    db.commit()
    return membership


def remove_member(db: Session, *, actor: User, group: Group, target_user_id: str) -> None:
    """Remove a member (admin+; owners only by an owner). The
    last-owner rule applies here too."""
    membership = active_membership(db, target_user_id, group.id)
    if membership is None:
        raise AppError("not_found", detail="That person is not an active member of this group.")
    _guard_last_owner(db, membership)
    db.delete(membership)
    audit.record(
        db, actor=f"user:{actor.id}", action="membership.removed",
        group_id=group.id, entity_type="membership", entity_id=membership.id,
        detail={"user_id": target_user_id},
    )
    db.commit()


def leave_group(db: Session, *, user: User, group: Group) -> None:
    """Leave a group. The last active owner may not leave."""
    membership = active_membership(db, user.id, group.id)
    if membership is None:
        raise AppError("not_found", detail="You are not an active member of that group.")
    _guard_last_owner(db, membership)
    db.delete(membership)
    audit.record(
        db, actor=f"user:{user.id}", action="membership.left",
        group_id=group.id, entity_type="membership", entity_id=membership.id,
    )
    db.commit()


def list_members(db: Session, group: Group) -> list[tuple[Membership, User]]:
    """All memberships (invited and active) with their users."""
    rows = (
        db.query(Membership, User)
        .join(User, User.id == Membership.user_id)
        .filter(Membership.group_id == group.id)
        .all()
    )
    return [(m, u) for m, u in rows]


# --- Agent seats (B-018) -------------------------------------------------


def create_seat(
    db: Session,
    *,
    actor: User,
    group: Group,
    name: str,
    role: str = "member",
    scopes: list[str] | None = None,
) -> tuple[AgentSeat, str]:
    """Create an agent seat and issue its key.

    The seat's powers are its role MINUS approval (enforced when
    approving, see approve_candidate) and its key's scopes. Returns
    (seat, full key) — the key is shown once.
    """
    if role not in ROLE_ORDER:
        raise AppError("validation_error", detail="That is not a role.")
    seat = AgentSeat(group_id=group.id, name=name, role=role, created_by_user_id=actor.id)
    db.add(seat)
    db.flush()
    key_row, full_key = create_api_key(
        db, label=f"seat:{name}", scopes=scopes or ["read", "draft"], seat_id=seat.id
    )
    audit.record(
        db, actor=f"user:{actor.id}", action="seat.created",
        group_id=group.id, entity_type="seat", entity_id=seat.id,
        detail={"name": name, "role": role, "key_id": key_row.id},
    )
    db.commit()
    return seat, full_key


def list_seats(db: Session, group: Group) -> list[AgentSeat]:
    """All seats in a group."""
    return db.query(AgentSeat).filter(AgentSeat.group_id == group.id).all()


# --- Candidate stub (B-018): the founding permission rule -----------------


def create_candidate(
    db: Session, *, actor_label: str, group: Group, kind: str, title: str
) -> Candidate:
    """Draft a candidate into the review queue (any member/seat with
    draft rights; scope enforcement happens at the door in deps).

    The row itself is drafted through the internal API's
    ``candidates.create`` (B-162) — the extraction module owns
    the review queue's shape, so its one author writes the row;
    this module keeps the audit row and the outward
    announcement, in the same transaction, as it always did."""
    data = call_internal(
        db, caller="groups", operation="candidates.create",
        payload={
            "group_id": group.id, "kind": kind, "title": title,
            "created_by": actor_label,
        },
    )
    candidate = db.get(Candidate, data["candidate_id"])
    audit.record(
        db, actor=actor_label, action="candidate.created",
        group_id=group.id, entity_type="candidate", entity_id=candidate.id,
        detail={"kind": kind, "title": title},
    )
    # A new pending candidate is announced outward in the same
    # transaction (Phase 11, B-128): candidate.pending.
    call_internal(
        db, caller="groups", operation="integrations.emit",
        payload={
            "group_id": group.id, "kind": "candidate.pending",
            "data": {"candidate_id": candidate.id, "kind": kind, "title": title},
        },
    )
    db.commit()
    return candidate


def approvals_recorded(db: Session, candidate_id: str) -> int:
    """How many distinct approvers have recorded approval of a
    candidate so far (B-111) — the review queue's progress."""
    return (
        db.query(CandidateApproval)
        .filter(CandidateApproval.candidate_id == candidate_id)
        .count()
    )


def review_progress(db: Session, candidate: Candidate) -> tuple[int, int]:
    """(approvals recorded, approvals required) for the wire —
    required is the candidate's own group's rule (B-111)."""
    group = db.get(Group, candidate.group_id)
    required = (group.review_approvals_required or 1) if group else 1
    return approvals_recorded(db, candidate.id), required


def approve_candidate(
    db: Session, *, actor_label: str, actor_is_seat: bool, group: Group,
    candidate_id: str, actor_user_id: str | None = None,
    actor_role: str | None = None,
) -> Candidate:
    """Approve a candidate. THE rule (B-018): a seat can draft but
    can never approve — approval is a person's act, checked here in
    the service so no route can forget it.

    Phase 9 (B-111) adds the group's own review rules, enforced
    here for the same reason: the settings pack decides the
    lowest role that may approve (``review_min_role`` — the
    route's door is membership now, so the rule must live where
    it cannot be forgotten) and how many DISTINCT approvers a
    candidate needs (``review_approvals_required``). Below the
    count, an approval is RECORDED and the candidate stays
    pending; the approval that meets the count runs the whole
    ordinary approval — mints and all — in its transaction.
    """
    if actor_is_seat:
        raise AppError("seat_cannot_approve")
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.group_id != group.id:
        raise AppError("not_found", detail="That candidate could not be found.")
    # "edited" joins "pending" in Phase 3 (B-043): a draft a
    # reviewer has corrected is still an undecided draft, and
    # approving it is the normal happy path, not an exception.
    if candidate.status not in ("pending", "edited"):
        raise AppError("conflict", detail="That candidate has already been decided.")
    if actor_role is not None and not role_at_least(
        actor_role, group.review_min_role
    ):
        raise AppError(
            "forbidden",
            detail=(
                f"Approving in this group needs a {group.review_min_role} "
                "or above."
            ),
        )
    required = group.review_approvals_required or 1
    if required > 1:
        if actor_user_id is None:
            raise AppError(
                "forbidden",
                detail="Recording an approval needs a person.",
            )
        already = (
            db.query(CandidateApproval)
            .filter(
                CandidateApproval.candidate_id == candidate.id,
                CandidateApproval.approver_user_id == actor_user_id,
            )
            .one_or_none()
        )
        if already is not None:
            raise AppError(
                "conflict",
                detail="You have already recorded your approval of this candidate.",
            )
        db.add(
            CandidateApproval(
                group_id=group.id, candidate_id=candidate.id,
                approver_user_id=actor_user_id, approver_label=actor_label,
            )
        )
        db.flush()
        recorded = approvals_recorded(db, candidate.id)
        audit.record(
            db, actor=actor_label, action="candidate.approval_recorded",
            group_id=group.id, entity_type="candidate",
            entity_id=candidate.id,
            detail={
                "approvals_recorded": recorded,
                "approvals_required": required,
            },
        )
        if recorded < required:
            # Recorded, not decided: the candidate stays
            # pending and nothing mints. The commit lands
            # before the answer (the commit-before-answer law).
            db.commit()
            return candidate
        # This approval meets the count — fall through and run
        # the ordinary deciding approval below.
    candidate.status = "approved"
    candidate.approved_by = actor_label
    # Phase 4 (B-049): approval of an event candidate MINTS the
    # event, in this same transaction — the candidate's
    # resulting_entity_* fills in and the event is born published.
    # Minting refuses (and the whole approval rolls back, the
    # candidate still pending) when the draft cannot become an
    # event yet, e.g. it never resolved a start date. Other kinds
    # mint nothing here: their entities are later phases' work,
    # and approval for them stays a recorded decision only.
    from gpa.services import events as event_service

    minted_event = event_service.mint_from_candidate(
        db, group=group, candidate=candidate, actor_label=actor_label
    )
    if minted_event is not None:
        # Born published, so its reminders materialise in this
        # same transaction (B-096) — for the host; answers that
        # arrive later add their own through the RSVP road.
        from gpa.services import reminders as reminder_service

        reminder_service.schedule_reminders(db, group=group, event=minted_event)
        # And its birth is announced outward in the same
        # transaction (Phase 11, B-128): event.published.
        call_internal(
            db, caller="groups", operation="integrations.emit",
            payload={
                "group_id": group.id, "kind": "event.published",
                "event_id": minted_event.id,
            },
        )
    # Phase 7 (B-083/B-085/B-086): approval of a task, decision,
    # or question candidate MINTS its planning entity in the
    # same transaction, the way events mint — the planning
    # mints fill resulting_entity_* and refuse in plain words
    # (an empty draft, an owner who is not a member) so the
    # approval rolls back and the candidate stays pending.
    from gpa.services import planning as planning_service

    planning_service.mint_task_from_candidate(
        db, group=group, candidate=candidate, actor_label=actor_label
    )
    planning_service.mint_decision_from_candidate(
        db, group=group, candidate=candidate, actor_label=actor_label
    )
    planning_service.mint_question_from_candidate(
        db, group=group, candidate=candidate, actor_label=actor_label
    )
    # Phase 6 (B-077): approval of a change candidate APPLIES
    # it to its event, in this same transaction — a refused
    # application (no target, an unbacked claim, an unresolved
    # time) rolls the approval back and the candidate stays
    # pending, exactly like a mint that cannot happen yet.
    if candidate.kind == "event_change":
        from gpa.services import merging as merging_service

        changed_event = merging_service.apply_change_candidate(
            db, group=group, candidate=candidate, actor_label=actor_label
        )
        # The applied change reschedules the event's reminders
        # in this transaction too (B-096) — a moved event's old
        # reminder jobs never survive its approval.
        from gpa.services import reminders as reminder_service

        reminder_service.schedule_reminders(db, group=group, event=changed_event)
    audit.record(
        db, actor=actor_label, action="candidate.approved",
        group_id=group.id, entity_type="candidate", entity_id=candidate.id,
    )
    db.commit()
    return candidate
