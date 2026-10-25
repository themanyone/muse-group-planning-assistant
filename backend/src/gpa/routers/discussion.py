"""Discussion routes (B-060, generalised by B-084).

ONE thread system, four parents: events, tasks, decisions,
polls. Reading a thread is member+; posting is a member draft
act; editing is the author's own draft act; hiding is an
organiser publish act. The event routes keep their B-060 paths
(``/events/{event_id}/comments`` reads the same rows as the
generic ``/comments/event/{event_id}`` — one implementation,
one truth). Every rule lives in services/discussion.py.

Ordering law in this file: the comment-act routes register
BEFORE the generic parent routes — ``POST
/comments/{id}/hide`` must win over ``POST
/comments/{parent_type}/{parent_id}``, or moderation calls
would be read as posts to a parent named "hide".
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.events import (
    CommentCreateRequest,
    CommentEditRequest,
    CommentResponse,
    comment_view,
)
from gpa.services import discussion as discussion_service

router = APIRouter(prefix="/api/v1/groups", tags=["discussion"])


def _view(db: Session, comment) -> CommentResponse:
    """One comment rendered with its thread."""
    return comment_view(comment, discussion_service.thread_of_comment(db, comment))


def _list(db: Session, context: GroupContext, parent_type: str, parent_id: str):
    """A parent's discussion. Members see live comments only;
    organisers see the hidden and the tombstones too, flagged."""
    rows = discussion_service.list_comments(
        db, group=context.group, parent_type=parent_type,
        parent_id=parent_id, viewer_role=context.role,
    )
    return [_view(db, row) for row in rows]


def _post(
    db: Session, context: GroupContext, principal: Principal,
    parent_type: str, parent_id: str, body: CommentCreateRequest,
) -> CommentResponse:
    """Post to a parent's thread, as yourself — a seat's words
    are the seat's (B-018), a person's are theirs."""
    comment = discussion_service.post_comment(
        db,
        author_label=principal.label,
        author_user_id=principal.user_id,
        author_seat_id=principal.seat_id,
        group=context.group,
        parent_type=parent_type,
        parent_id=parent_id,
        viewer_role=context.role,
        body=body.body,
    )
    return _view(db, comment)


# ------------------------------------------------------------------
# Event parents (the B-060 paths, unchanged)
# ------------------------------------------------------------------


@router.get(
    "/{group_id}/events/{event_id}/comments", response_model=list[CommentResponse]
)
def list_event_comments(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """An event's discussion thread."""
    return _list(db, context, "event", event_id)


@router.post(
    "/{group_id}/events/{event_id}/comments",
    response_model=CommentResponse,
    status_code=201,
)
def post_event_comment(
    event_id: str,
    body: CommentCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Say something on an event."""
    return _post(db, context, principal, "event", event_id, body)


# ------------------------------------------------------------------
# Comment acts (parent-independent, as before)
# ------------------------------------------------------------------


@router.patch(
    "/{group_id}/comments/{comment_id}", response_model=CommentResponse
)
def edit_comment(
    comment_id: str,
    body: CommentEditRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Edit your own comment (nobody else's — ever)."""
    comment = discussion_service.edit_comment(
        db,
        author_label=principal.label,
        user_id=principal.user_id,
        seat_id=principal.seat_id,
        group=context.group,
        comment_id=comment_id,
        body=body.body,
    )
    return _view(db, comment)


@router.delete("/{group_id}/comments/{comment_id}", response_model=CommentResponse)
def delete_comment(
    comment_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Delete your own comment: a tombstone, returned flagged."""
    comment = discussion_service.delete_comment(
        db,
        author_label=principal.label,
        user_id=principal.user_id,
        seat_id=principal.seat_id,
        group=context.group,
        comment_id=comment_id,
    )
    return _view(db, comment)


@router.post(
    "/{group_id}/comments/{comment_id}/hide", response_model=CommentResponse
)
def hide_comment(
    comment_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Moderation: hide a comment (audited, reversible)."""
    comment = discussion_service.set_hidden(
        db, actor_label=principal.label, group=context.group,
        comment_id=comment_id, hidden=True,
    )
    return _view(db, comment)


@router.post(
    "/{group_id}/comments/{comment_id}/unhide", response_model=CommentResponse
)
def unhide_comment(
    comment_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Moderation, reversed: the comment returns to view."""
    comment = discussion_service.set_hidden(
        db, actor_label=principal.label, group=context.group,
        comment_id=comment_id, hidden=False,
    )
    return _view(db, comment)


# ------------------------------------------------------------------
# Any parent (B-084): tasks, decisions, polls — and events too
# ------------------------------------------------------------------


@router.get(
    "/{group_id}/comments/{parent_type}/{parent_id}",
    response_model=list[CommentResponse],
)
def list_parent_comments(
    parent_type: str,
    parent_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Any planning parent's thread through the same door."""
    return _list(db, context, parent_type, parent_id)


@router.post(
    "/{group_id}/comments/{parent_type}/{parent_id}",
    response_model=CommentResponse,
    status_code=201,
)
def post_parent_comment(
    parent_type: str,
    parent_id: str,
    body: CommentCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Say something on a task, a decision, or a poll."""
    return _post(db, context, principal, parent_type, parent_id, body)


