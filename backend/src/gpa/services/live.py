"""Live-chat source registration (roadmap B-032).

What this file is for: the PERMISSION to read a live chat, made
into a record before any connector exists. A ``chat_live`` source
registered here says: this named member granted the assistant
this scope over this conversation in this other system. The
polling/webhook receiver that will actually fill the source with
chunks arrives with its integration in Phase 12 — deliberately
after this record, because the permission is the part that must
never be an afterthought, a default, or a backfill.

The invariants are enforced HERE, at the only door:

- a granting principal who is a PERSON (an agent seat cannot
  grant access to a conversation — access is a human's to give);
- a non-empty scope, in the granting flow's own words;
- a connector kind and an external conversation id to bind to.

A registration that fails any of these does not exist. There is
no code path around this service that can mint a live source:
the sources core accepts the kind, but only this service is
wired to the registration route, and it checks first.
"""

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.services import sources as source_service


def register_live_source(
    db: Session,
    *,
    actor_label: str,
    actor_is_seat: bool,
    group: Group,
    connector_kind: str,
    external_id: str,
    granted_scope: str,
    title: str = "",
):
    """Register one live-chat source under a person's grant."""
    if actor_is_seat:
        raise AppError(
            "forbidden",
            detail="Live-chat access must be granted by a person, not a seat.",
        )
    if not connector_kind.strip() or not external_id.strip():
        raise AppError(
            "validation_error",
            detail="A live source must name its connector and the conversation it binds to.",
        )
    if not granted_scope.strip():
        raise AppError(
            "validation_error",
            detail="A live source cannot exist without a granted scope.",
        )
    return source_service.create_source(
        db, actor_label=actor_label, group=group, kind="chat_live",
        title=title or f"{connector_kind} chat",
        granted_scope=granted_scope, chunks=[],
        status="registered", stage="registered",
        connector_kind=connector_kind.strip(), external_id=external_id.strip(),
    )
