"""Poll + availability routes (roadmap B-087, B-090).

Creating and voting are member+ draft-class acts; closing a
poll and applying a date-poll winner are organiser+ behind the
publish scope — the winner moves a calendar, which is a
publishing act. Availability slots are proposed by organisers;
marks are any member's own can/can't.

The vote route holds the commit-before-answer law in its
shape (B-087): the service commits the vote; the tally in the
response is computed AFTER, from committed rows — this handler
literally cannot answer with a tally its own vote has not
landed in.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.events import EventResponse, project_event, to_utc_naive
from gpa.schemas.planning import (
    ApplyWinnerRequest,
    AvailabilityMarkRequest,
    AvailabilitySlotCreateRequest,
    AvailabilitySlotResponse,
    PollCreateRequest,
    PollResponse,
    PollResultsResponse,
    PollVoteRequest,
    availability_view,
    poll_view,
    results_view,
)
from gpa.services import availability as availability_service
from gpa.services import polls as poll_service

router = APIRouter(prefix="/api/v1/groups", tags=["polls"])


# ------------------------------------------------------------------
# Polls (B-087)
# ------------------------------------------------------------------


@router.post("/{group_id}/polls", response_model=PollResponse, status_code=201)
def create_poll(
    body: PollCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Ask the group something, with votable options."""
    poll = poll_service.create_poll(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        question=body.question,
        kind=body.kind,
        event_id=body.event_id,
        closes_at=to_utc_naive(body.closes_at) if body.closes_at else None,
        options=[
            {
                "label": option.label,
                "starts_at": to_utc_naive(option.starts_at) if option.starts_at else None,
                "ends_at": to_utc_naive(option.ends_at) if option.ends_at else None,
            }
            for option in body.options
        ],
    )
    results = poll_service.poll_results(db, poll)
    return poll_view(poll, results)


@router.get("/{group_id}/polls", response_model=list[PollResponse])
def list_polls(
    status: str | None = Query(default=None),
    event_id: str | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """The group's polls with their tallies and the caller's
    current vote marked."""
    rows = poll_service.list_polls(db, context.group, status=status, event_id=event_id)
    views = []
    for poll in rows:
        mine = poll_service.my_vote(db, voter_label=principal.label, poll=poll)
        views.append(
            poll_view(
                poll, poll_service.poll_results(db, poll),
                my_option_id=mine.option_id if mine else None,
            )
        )
    return views


@router.get("/{group_id}/polls/{poll_id}", response_model=PollResponse)
def get_poll(
    poll_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    poll = poll_service.get_poll(db, context.group, poll_id)
    mine = poll_service.my_vote(db, voter_label=principal.label, poll=poll)
    return poll_view(
        poll, poll_service.poll_results(db, poll),
        my_option_id=mine.option_id if mine else None,
    )


@router.post(
    "/{group_id}/polls/{poll_id}/vote", response_model=PollResultsResponse
)
def cast_vote(
    poll_id: str,
    body: PollVoteRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Vote — or change your vote; one member holds one current
    vote, by the table's own constraint. The service COMMITS
    the vote before this handler reads any tally: the results
    below are computed from committed rows only."""
    vote = poll_service.cast_vote(
        db,
        actor_label=principal.label,
        user_id=principal.user_id,
        seat_id=principal.seat_id,
        group=context.group,
        poll_id=poll_id,
        option_id=body.option_id,
    )
    # The vote is committed at this line (the service's law).
    # Only now is a tally read — it can only ever describe the
    # world as committed, the caller's own vote included.
    poll = poll_service.get_poll(db, context.group, poll_id)
    results = poll_service.poll_results(db, poll)
    return results_view(results, my_option_id=vote.option_id)


@router.post("/{group_id}/polls/{poll_id}/close", response_model=PollResponse)
def close_poll(
    poll_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Freeze the tally (organiser+)."""
    poll = poll_service.close_poll(
        db, actor_label=principal.label, group=context.group, poll_id=poll_id
    )
    return poll_view(poll, poll_service.poll_results(db, poll))


@router.post(
    "/{group_id}/polls/{poll_id}/apply-winner", response_model=EventResponse
)
def apply_winner(
    poll_id: str,
    body: ApplyWinnerRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Apply a date poll's winner: move the attached event, or
    mint a new one at the winning moment (organiser+)."""
    event = poll_service.apply_winner(
        db, actor_label=principal.label, group=context.group,
        poll_id=poll_id, title=body.title,
    )
    return project_event(event, "organiser")


# ------------------------------------------------------------------
# Availability (B-090)
# ------------------------------------------------------------------


@router.post(
    "/{group_id}/availability/slots",
    response_model=AvailabilitySlotResponse,
    status_code=201,
)
def propose_slot(
    body: AvailabilitySlotCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Propose a moment for members to mark can/can't."""
    slot = availability_service.propose_slot(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        starts_at=to_utc_naive(body.starts_at),
        ends_at=to_utc_naive(body.ends_at),
        label=body.label,
        event_id=body.event_id,
    )
    rows = availability_service.ranked_availability(db, context.group)
    row = next(r for r in rows if r["slot"].id == slot.id)
    return availability_view(row)


@router.get(
    "/{group_id}/availability", response_model=list[AvailabilitySlotResponse]
)
def ranked_availability(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Every proposed slot, ranked: most can first. The marks
    shown are the group's own, against the group's own slots —
    nothing from any private calendar exists here to show."""
    rows = availability_service.ranked_availability(db, context.group)
    return [availability_view(row) for row in rows]


@router.post(
    "/{group_id}/availability/slots/{slot_id}/mark",
    response_model=AvailabilitySlotResponse,
)
def mark_slot(
    slot_id: str,
    body: AvailabilityMarkRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Mark can or can't — re-marking updates your one mark."""
    availability_service.mark_slot(
        db,
        actor_label=principal.label,
        user_id=principal.user_id,
        seat_id=principal.seat_id,
        group=context.group,
        slot_id=slot_id,
        can_attend=body.can,
    )
    rows = availability_service.ranked_availability(db, context.group)
    row = next(r for r in rows if r["slot"].id == slot_id)
    return availability_view(row)
