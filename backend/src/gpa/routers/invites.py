"""Invite routes (Phase 8, B-098).

Two doors:

- The group door (organiser+): create an invite for one
  address to one event, list an event's invites. The creation
  response carries the link — the raw token exists in exactly
  two places after that: the invitee's notification and this
  response. Listings show the prefix only.
- The public door (no account): the token IS the credential.
  GET shows the event's public face; POST records the answer
  as ``guest:<email>``. A tampered token is a 404 that creates
  nothing.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.config import Settings, get_settings
from gpa.db import get_db
from gpa.deps import GroupContext, require_group_role
from gpa.models.notifications import EventInvite
from gpa.schemas.notifications import (
    InviteCreateRequest,
    InviteRespondRequest,
    InviteRespondResponse,
    InviteResponse,
    PublicInviteResponse,
)
from gpa.services import events as event_service
from gpa.services import invites as invite_service

group_router = APIRouter(prefix="/api/v1/groups", tags=["invites"])
public_router = APIRouter(prefix="/api/v1/invites", tags=["invites"])


def _invite_response(invite: EventInvite, link: str | None = None) -> InviteResponse:
    return InviteResponse(
        id=invite.id, group_id=invite.group_id, event_id=invite.event_id,
        email=invite.email, user_id=invite.user_id,
        token_prefix=invite.token_prefix, status=invite.status,
        expires_at=invite.expires_at, rsvp_id=invite.rsvp_id, link=link,
    )


@group_router.post(
    "/{group_id}/events/{event_id}/invites",
    response_model=InviteResponse, status_code=201,
)
def create_invite(
    event_id: str,
    body: InviteCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> InviteResponse:
    """Invite one address to this event (organiser+). The
    response's link is shown ONCE — send it on, or trust the
    notification the invitee also receives."""
    event = event_service.get_event(db, context.group, event_id)
    invite, raw = invite_service.create_invite(
        db, settings,
        actor_label=context.principal.label,
        group=context.group, event=event, email=body.email,
    )
    return _invite_response(invite, link=invite_service.invite_link(settings, raw))


@group_router.get(
    "/{group_id}/events/{event_id}/invites", response_model=list[InviteResponse]
)
def list_invites(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
) -> list[InviteResponse]:
    """An event's invites (prefixes only — the tokens
    themselves are gone by design)."""
    event = event_service.get_event(db, context.group, event_id)
    rows = (
        db.query(EventInvite)
        .filter(EventInvite.event_id == event.id)
        .order_by(EventInvite.created_at, EventInvite.id)
        .all()
    )
    return [_invite_response(row) for row in rows]


@public_router.get("/{token}", response_model=PublicInviteResponse)
def view_invite(
    token: str,
    db: Session = Depends(get_db),
) -> PublicInviteResponse:
    """The invite's public face: enough of the event to answer
    it — the B-059 PUBLIC projection (no host, no cost, no
    access notes), never the group's internals."""
    from gpa.schemas.events import project_event
    from gpa.services.groups import get_group

    invite = invite_service.find_invite(db, token)
    group = get_group(db, invite.group_id)
    event = event_service.get_event(db, group, invite.event_id)
    view = project_event(event, "public")
    return PublicInviteResponse(
        event_id=event.id, group_id=group.id, group_name=group.name,
        title=view.title, description=view.description,
        starts_at=view.starts_at, ends_at=view.ends_at,
        timezone=view.timezone, place_name=view.place_name,
        place_address=view.place_address, status=view.status,
        email=invite.email, account_offered=invite.user_id is None,
    )


@public_router.post("/{token}/rsvp", response_model=InviteRespondResponse, status_code=201)
def respond_invite(
    token: str,
    body: InviteRespondRequest,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> InviteRespondResponse:
    """Answer without an account: the RSVP lands attributed
    to the invited address (``guest:<email>``), and an account
    is offered — in the response, and never as a toll gate."""
    invite, rsvp = invite_service.respond(
        db, settings, token=token, status=body.status,
        guests=body.guests, note=body.note,
    )
    return InviteRespondResponse(
        rsvp_id=rsvp.id, principal_label=rsvp.principal_label,
        status=rsvp.status, account_offered=True,
    )
