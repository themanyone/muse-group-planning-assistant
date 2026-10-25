"""RSVP routes (roadmap B-061).

Your answer to an event: an upsert (answering again updates
your row), a withdrawal, and the list + summary a calendar
surface shows. Member+ throughout; answering rides the draft
scope for key callers. Capacity, waitlisting, and promotion
live in the service — the routes only carry words in and out.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.events import RsvpListResponse, RsvpRequest, RsvpResponse, rsvp_view
from gpa.services import events as event_service
from gpa.services import rsvps as rsvp_service

router = APIRouter(prefix="/api/v1/groups", tags=["rsvps"])


@router.put("/{group_id}/events/{event_id}/rsvp", response_model=RsvpResponse)
def set_rsvp(
    event_id: str,
    body: RsvpRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Answer — or change your answer. When the room is full,
    the response says so honestly: ``waitlisted`` with your
    position in line."""
    row = rsvp_service.set_rsvp(
        db, request.app.state.settings,
        principal_label=principal.label,
        user_id=principal.user_id,
        seat_id=principal.seat_id,
        group=context.group,
        event_id=event_id,
        status=body.status,
        guests=body.guests,
        note=body.note,
    )
    return rsvp_view(row)


@router.delete("/{group_id}/events/{event_id}/rsvp", status_code=204)
def remove_rsvp(
    event_id: str,
    request: Request,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Withdraw your answer. A freed seat promotes the next in
    line, with their notification job, in the same act."""
    rsvp_service.remove_rsvp(
        db, request.app.state.settings,
        principal_label=principal.label,
        group=context.group,
        event_id=event_id,
    )
    from fastapi import Response

    return Response(status_code=204)


@router.get("/{group_id}/events/{event_id}/rsvps", response_model=RsvpListResponse)
def list_rsvps(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Every answer + the summary counts."""
    rows = rsvp_service.list_rsvps(db, context.group, event_id)
    event = event_service.get_event(db, context.group, event_id)
    return RsvpListResponse(
        rsvps=[rsvp_view(row) for row in rows],
        summary=rsvp_service.summary(db, event),
    )
