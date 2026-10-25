"""Notification routes (Phase 8, B-100/B-101/B-102/B-099).

Three surfaces, one door discipline (deps.py):

- ``/users/me`` — the caller's own inbox and their per-group
  notification preferences (people only; an inbox is a
  person's).
- ``/groups/{id}`` — the admin's deliverability view and
  resend (organiser+), and the group's digests (member to
  read, organiser+ to run one now).
- ``/webhooks/mail/{provider}`` — the mail provider's bounce /
  complaint receiver, stubbed (B-102): the shape is recorded;
  signature verification arrives with the real provider
  driver in Phase 12.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, get_principal, require_group_role
from gpa.errors import AppError
from gpa.models.notifications import Digest, MailEvent, Notification
from gpa.schemas.notifications import (
    DigestResponse,
    FailuresResponse,
    InboxResponse,
    MailEventResponse,
    MailWebhookRequest,
    NotificationResponse,
    PreferencesResponse,
    PreferencesUpdateRequest,
)
from gpa.services import digests as digest_service
from gpa.services import notifications as notify_service

me_router = APIRouter(prefix="/api/v1/users/me", tags=["notifications"])
group_router = APIRouter(prefix="/api/v1/groups", tags=["notifications"])
webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["notifications"])


def _notification_response(row: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=row.id, group_id=row.group_id, event_id=row.event_id,
        recipient_label=row.recipient_label, recipient_email=row.recipient_email,
        kind=row.kind, send_email=row.send_email, in_app=row.in_app,
        subject=row.subject, body_text=row.body_text, body_html=row.body_html,
        template_body=row.template_body, wording_source=row.wording_source,
        urgent=row.urgent, send_after=row.send_after, status=row.status,
        attempts=row.attempts, last_error=row.last_error, sent_at=row.sent_at,
        read_at=row.read_at, created_at=row.created_at,
    )


def _person(principal: Principal):
    """The caller's user row — inboxes and preferences belong
    to people, never to seats."""
    if principal.is_seat or principal.user is None:
        raise AppError(
            "forbidden", detail="Notifications belong to a person, not a seat."
        )
    return principal.user


# --- The inbox (B-101) --------------------------------------------


@me_router.get("/inbox", response_model=InboxResponse)
def get_inbox(
    unread_only: bool = Query(default=False),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> InboxResponse:
    """My notifications across my groups: the same rows the
    email channel sends from — the two can never disagree
    about what was sent (B-101)."""
    user = _person(principal)
    rows = notify_service.inbox(db, user=user, unread_only=unread_only)
    unread = sum(1 for row in rows if row.read_at is None) if not unread_only else len(rows)
    return InboxResponse(
        items=[_notification_response(row) for row in rows], unread=unread
    )


@me_router.post("/inbox/{notification_id}/read", response_model=NotificationResponse)
def mark_inbox_read(
    notification_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> NotificationResponse:
    user = _person(principal)
    return _notification_response(
        notify_service.mark_read(db, user=user, notification_id=notification_id)
    )


# --- Preferences (B-100) -------------------------------------------


@me_router.get("/groups/{group_id}/notification-preferences", response_model=PreferencesResponse)
def get_preferences(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
) -> PreferencesResponse:
    """My effective notification policy in this group: every
    switch (defaults filled in) and my quiet hours."""
    user = _person(context.principal)
    view = notify_service.preferences_view(db, user=user, group=context.group)
    return PreferencesResponse(**view)


@me_router.put("/groups/{group_id}/notification-preferences", response_model=PreferencesResponse)
def put_preferences(
    body: PreferencesUpdateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
) -> PreferencesResponse:
    """Set my switches / quiet hours in this group."""
    user = _person(context.principal)
    quiet = None
    if body.quiet_hours is not None:
        quiet = body.quiet_hours.model_dump()
    view = notify_service.set_preferences(
        db,
        actor_label=context.principal.label,
        user=user,
        group=context.group,
        switches=[switch.model_dump() for switch in body.switches],
        quiet_hours=quiet,
    )
    return PreferencesResponse(**view)


# --- Deliverability (B-102) + digests (B-099) -----------------------


@group_router.get("/{group_id}/notification-failures", response_model=FailuresResponse)
def get_failures(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
) -> FailuresResponse:
    """Failed and dead notifications for this group, with the
    provider's mail events about them (organiser+). A failing
    address is visible here — never silently retried forever."""
    view = notify_service.list_failures(db, group=context.group)
    return FailuresResponse(
        notifications=[_notification_response(row) for row in view["notifications"]],
        mail_events=[
            MailEventResponse(
                id=event.id, provider=event.provider, email=event.email,
                event_type=event.event_type, notification_id=event.notification_id,
                detail=event.detail, created_at=event.created_at,
            )
            for event in view["mail_events"]
        ],
    )


@group_router.post(
    "/{group_id}/notifications/{notification_id}/resend",
    response_model=NotificationResponse,
)
def resend_notification(
    notification_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
) -> NotificationResponse:
    """Requeue a failed/dead notification (organiser+): a
    person's decision, with attempts reset."""
    row = notify_service.resend(
        db, actor_label=context.principal.label,
        group=context.group, notification_id=notification_id,
    )
    return _notification_response(row)


@group_router.get("/{group_id}/digests", response_model=list[DigestResponse])
def list_digests(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
) -> list[DigestResponse]:
    """The group's stored digests, newest first."""
    rows = (
        db.query(Digest)
        .filter(Digest.group_id == context.group.id)
        .order_by(Digest.created_at.desc(), Digest.id.desc())
        .all()
    )
    return [
        DigestResponse(
            id=row.id, group_id=row.group_id, week_start=row.week_start,
            week_end=row.week_end, subject=row.subject, body_text=row.body_text,
            body_html=row.body_html, created_by=row.created_by,
            created_at=row.created_at, sent_at=row.sent_at,
        )
        for row in rows
    ]


@group_router.post("/{group_id}/digest/run", response_model=DigestResponse, status_code=201)
def run_digest_now(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
) -> DigestResponse:
    """Compose and send this week's digest now (organiser+),
    and anchor the weekly chain from here."""
    digest = digest_service.run_digest(
        db, actor_label=context.principal.label, group=context.group
    )
    digest_service.ensure_scheduled(db, group=context.group)
    db.commit()
    return DigestResponse(
        id=digest.id, group_id=digest.group_id, week_start=digest.week_start,
        week_end=digest.week_end, subject=digest.subject, body_text=digest.body_text,
        body_html=digest.body_html, created_by=digest.created_by,
        created_at=digest.created_at, sent_at=digest.sent_at,
    )


# --- The mail webhook (B-102, stubbed) ------------------------------


@webhook_router.post("/mail/{provider}", response_model=MailEventResponse, status_code=201)
def mail_webhook(
    provider: str,
    body: MailWebhookRequest,
    db: Session = Depends(get_db),
) -> MailEventResponse:
    """Record a provider's bounce/complaint (stubbed
    receiver): a bounce also marks its notification failed,
    so the address shows on the admin failure list."""
    row: MailEvent = notify_service.record_mail_event(
        db, provider=provider, email=body.email, event_type=body.event_type,
        notification_id=body.notification_id, detail=body.detail,
    )
    return MailEventResponse(
        id=row.id, provider=row.provider, email=row.email,
        event_type=row.event_type, notification_id=row.notification_id,
        detail=row.detail, created_at=row.created_at,
    )
