"""Group routes (roadmap B-015 → B-019).

Groups, their members, their agent seats, and their audit view.
Every handler authorises through gpa.deps — ``require_group_role``
for role gates, ``require_scope`` for credential gates — and defers
every rule to services/groups.py.
"""

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, get_group_context, get_principal, require_group_role, require_scope
from gpa.errors import AppError
from gpa.schemas.core import AuditEntryResponse
from gpa.schemas.groups import (
    CandidateCreateRequest,
    CandidateResponse,
    GroupCreateRequest,
    GroupResponse,
    GroupUpdateRequest,
    InviteRequest,
    MembershipResponse,
    RoleChangeRequest,
    SeatCreateRequest,
    SeatResponse,
)
from gpa.services import audit as audit_service
from gpa.services import groups as group_service

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


def _group_response(context: GroupContext) -> GroupResponse:
    """Shape a group, including the caller's own role in it."""
    g = context.group
    from gpa.schemas.groups import ReminderRule
    from gpa.services import reminders as reminder_service

    return GroupResponse(
        id=g.id, name=g.name, slug=g.slug, description=g.description,
        visibility=g.visibility, home_timezone=g.home_timezone,
        default_place=g.default_place, my_role=context.role,
        reminder_rules=[ReminderRule(**rule) for rule in reminder_service.rules_for(g)],
        digest_weekday=g.digest_weekday, digest_time=g.digest_time,
        digest_enabled=g.digest_enabled, ai_reminder_wording=g.ai_reminder_wording,
    )


def _person(db: Session, principal: Principal):
    """The caller's user row — member management belongs to people;
    seats act through candidate routes, not these."""
    if principal.is_seat or principal.user_id is None:
        raise AppError("forbidden", detail="That action belongs to a person, not a seat.")
    from gpa.services.identity import get_user

    return get_user(db, principal.user_id)


# --- Groups themselves ---------------------------------------------------


@router.post("", response_model=GroupResponse, status_code=201)
def create_group(
    body: GroupCreateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("draft")),
):
    """Create a group; the creator becomes its owner."""
    user = _person(db, principal)
    group = group_service.create_group(
        db, creator=user, name=body.name, description=body.description,
        visibility=body.visibility, home_timezone=body.home_timezone,
        default_place=body.default_place,
    )
    return _group_response(GroupContext(group=group, role="owner", principal=principal))


@router.get("/{group_id}", response_model=GroupResponse)
def read_group(context: GroupContext = Depends(get_group_context)):
    """Read a group. Non-members may read invite/public-listed
    groups; private groups are invisible to them (404 in deps)."""
    return _group_response(context)


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group(
    body: GroupUpdateRequest,
    context: GroupContext = Depends(require_group_role("admin")),
    db: Session = Depends(get_db),
):
    """Edit group settings (admin+)."""
    changes = body.model_dump(exclude_none=True)
    group_service.update_group(db, actor_label=context.principal.label, group=context.group, changes=changes)
    return _group_response(context)


# --- Members (B-016) -------------------------------------------------------


@router.get("/{group_id}/members", response_model=list[MembershipResponse])
def list_members(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Everyone in the group — invited and active."""
    return [
        MembershipResponse(
            id=m.id, user_id=u.id, email=u.email, name=u.name, role=m.role, status=m.status
        )
        for m, u in group_service.list_members(db, context.group)
    ]


@router.post("/{group_id}/invites", response_model=MembershipResponse, status_code=201)
def invite_member(
    body: InviteRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """Invite a person by email (organiser+)."""
    actor = _person(db, context.principal)
    membership = group_service.invite_member(
        db, actor=actor, group=context.group, email=body.email, role=body.role
    )
    from gpa.services.identity import get_user

    invited = get_user(db, membership.user_id)
    return MembershipResponse(
        id=membership.id, user_id=invited.id, email=invited.email,
        name=invited.name, role=membership.role, status=membership.status,
    )


@router.post("/{group_id}/invites/accept", response_model=MembershipResponse)
def accept_invite(
    context: GroupContext = Depends(get_group_context),
    db: Session = Depends(get_db),
):
    """Accept my invitation to this group."""
    user = _person(db, context.principal)
    membership = group_service.accept_invite(db, user=user, group=context.group)
    return MembershipResponse(
        id=membership.id, user_id=user.id, email=user.email,
        name=user.name, role=membership.role, status=membership.status,
    )


@router.post("/{group_id}/invites/decline", status_code=204)
def decline_invite(
    context: GroupContext = Depends(get_group_context),
    db: Session = Depends(get_db),
):
    """Decline my invitation to this group."""
    user = _person(db, context.principal)
    group_service.decline_invite(db, user=user, group=context.group)
    return None


@router.patch("/{group_id}/members/{user_id}/role", response_model=MembershipResponse)
def change_role(
    user_id: str,
    body: RoleChangeRequest,
    context: GroupContext = Depends(require_group_role("admin")),
    db: Session = Depends(get_db),
):
    """Change a member's role (admin+; owner/admin moves are
    owner-only inside the service — B-016)."""
    actor = _person(db, context.principal)
    membership = group_service.change_role(
        db, actor=actor, actor_role=context.role or "guest",
        group=context.group, target_user_id=user_id, new_role=body.role,
    )
    from gpa.services.identity import get_user

    target = get_user(db, user_id)
    return MembershipResponse(
        id=membership.id, user_id=target.id, email=target.email,
        name=target.name, role=membership.role, status=membership.status,
    )


@router.delete("/{group_id}/members/{user_id}", status_code=204)
def remove_member(
    user_id: str,
    context: GroupContext = Depends(require_group_role("admin")),
    db: Session = Depends(get_db),
):
    """Remove a member (admin+; last-owner rule in the service)."""
    actor = _person(db, context.principal)
    group_service.remove_member(db, actor=actor, group=context.group, target_user_id=user_id)
    return None


@router.post("/{group_id}/leave", status_code=204)
def leave_group(
    context: GroupContext = Depends(require_group_role("guest")),
    db: Session = Depends(get_db),
):
    """Leave the group myself (the last owner may not — B-016)."""
    user = _person(db, context.principal)
    group_service.leave_group(db, user=user, group=context.group)
    return None


# --- Agent seats (B-018) ---------------------------------------------------


@router.post("/{group_id}/seats", response_model=SeatResponse, status_code=201)
def create_seat(
    body: SeatCreateRequest,
    context: GroupContext = Depends(require_group_role("admin")),
    db: Session = Depends(get_db),
):
    """Create an agent seat and receive its key — shown once."""
    actor = _person(db, context.principal)
    seat, full_key = group_service.create_seat(
        db, actor=actor, group=context.group, name=body.name,
        role=body.role, scopes=body.scopes,
    )
    return SeatResponse(
        id=seat.id, group_id=seat.group_id, name=seat.name, role=seat.role,
        key=full_key, key_prefix=full_key[:12],
    )


@router.get("/{group_id}/seats", response_model=list[SeatResponse])
def list_seats(
    context: GroupContext = Depends(require_group_role("admin")),
    db: Session = Depends(get_db),
):
    """List the group's seats (never their keys)."""
    return [
        SeatResponse(id=s.id, group_id=s.group_id, name=s.name, role=s.role)
        for s in group_service.list_seats(db, context.group)
    ]


# --- Candidates: the review-queue stub (B-018) -------------------------------


def _candidate_response(candidate) -> CandidateResponse:
    try:
        draft = json.loads(candidate.draft_json or "{}")
    except json.JSONDecodeError:
        draft = {}
    try:
        flags = json.loads(candidate.flags_json or "[]")
    except json.JSONDecodeError:
        flags = []
    return CandidateResponse(
        id=candidate.id, group_id=candidate.group_id, kind=candidate.kind,
        title=candidate.title, status=candidate.status,
        created_by=candidate.created_by, approved_by=candidate.approved_by,
        draft=draft if isinstance(draft, dict) else {},
        confidence=candidate.confidence,
        flags=flags if isinstance(flags, list) else [],
        source_id=candidate.source_id,
        prompt_name=candidate.prompt_name,
        prompt_version=candidate.prompt_version,
        job_id=candidate.job_id,
        merged_into_id=candidate.merged_into_id,
        resulting_entity_type=candidate.resulting_entity_type,
        resulting_entity_id=candidate.resulting_entity_id,
    )


@router.post("/{group_id}/candidates", response_model=CandidateResponse, status_code=201)
def create_candidate(
    body: CandidateCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Draft a candidate. Needs membership (member+) AND, for key
    callers, the draft scope — a read key stops here (B-014)."""
    candidate = group_service.create_candidate(
        db, actor_label=principal.label, group=context.group,
        kind=body.kind, title=body.title,
    )
    return _candidate_response(candidate)


@router.post("/{group_id}/candidates/{candidate_id}/approve", response_model=CandidateResponse)
def approve_candidate(
    candidate_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Approve a candidate (organiser+ person with publish rights).
    Seats are refused inside the service itself (B-018's rule)."""
    candidate = group_service.approve_candidate(
        db, actor_label=principal.label, actor_is_seat=principal.is_seat,
        group=context.group, candidate_id=candidate_id,
    )
    return _candidate_response(candidate)


# --- Audit (B-019) -----------------------------------------------------------


@router.get("/{group_id}/audit", response_model=list[AuditEntryResponse])
def read_audit(
    context: GroupContext = Depends(require_group_role("admin")),
    db: Session = Depends(get_db),
):
    """The group's audit trail, newest first (admin+)."""
    return [
        AuditEntryResponse(
            id=row.id, actor=row.actor, action=row.action,
            entity_type=row.entity_type, entity_id=row.entity_id,
            detail=json.loads(row.detail_json or "{}"),
            created_at=row.created_at.isoformat(),
        )
        for row in audit_service.list_for_group(db, context.group.id)
    ]
