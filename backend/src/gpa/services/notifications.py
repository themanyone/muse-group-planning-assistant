"""Notification core: the outbox, preferences, delivery (B-095).

THE OUTBOX RULE (B-095): the request that creates a notification
NEVER sends it inline. Creating writes one ``Notification`` row
and one ``notify`` job in the caller's transaction; a worker
runs the job later and the transport (services/mailer.py) does
the sending. A mail outage can therefore never fail an RSVP, a
publish, or any other user flow — it can only delay words.

POLICY AT CREATION (B-100): which channels a row travels by is
decided once, when the row is created, from the recipient's
preferences in that group (default: everything on). That is
what makes B-101's promise — inbox and email never disagree
about what was sent — a property of the data rather than a
hope: there is exactly one row, and both channels are its
fields. A member who switched every channel off for a kind
gets no row at all (the suppression is audited instead).

QUIET HOURS (B-100): a REMINDER whose moment falls inside the
recipient's quiet hours is held — status ``held``,
``send_after`` moved to the quiet end — never dropped, never
sent early. Urgent rows (an organiser's cancellation) skip
quiet hours entirely; urgency is a person's mark, never the
system's guess.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Rsvp
from gpa.models.groups import Group
from gpa.models.identity import User, now_utc
from gpa.models.notifications import (
    CHANNELS,
    KIND_CATEGORIES,
    MailEvent,
    MemberNotificationSettings,
    Notification,
    NotificationPreference,
    category_of,
)
from gpa.services import audit
from gpa.services import jobs as job_service
from gpa.services import mailer

NOTIFY_JOB_KIND = "notify"

# The send job's attempt ceiling, mirrored on the notification
# row: when a notification's own attempts reach it, the row is
# "dead" — B-102's admin-visible end of the road. Matches the
# job chassis default (config job_max_attempts).
MAX_SEND_ATTEMPTS = 5


# ------------------------------------------------------------------
# Preferences + quiet hours (B-100)
# ------------------------------------------------------------------


def _preference_row(
    db: Session, user_id: str, group_id: str, channel: str, category: str
) -> NotificationPreference | None:
    return (
        db.query(NotificationPreference)
        .filter(
            NotificationPreference.user_id == user_id,
            NotificationPreference.group_id == group_id,
            NotificationPreference.channel == channel,
            NotificationPreference.kind_category == category,
        )
        .one_or_none()
    )


def channel_allowed(
    db: Session, *, user: User, group: Group, channel: str, category: str
) -> bool:
    """One switch's state; absence of a row is the default: on."""
    row = _preference_row(db, user.id, group.id, channel, category)
    return True if row is None else bool(row.enabled)


def preferences_view(db: Session, *, user: User, group: Group) -> dict:
    """The member's full effective policy in this group: every
    channel × category switch (defaults filled in) plus their
    quiet hours — the shape the preferences screen renders."""
    switches = [
        {
            "channel": channel,
            "kind": category,
            "enabled": channel_allowed(
                db, user=user, group=group, channel=channel, category=category
            ),
        }
        for channel in CHANNELS
        for category in KIND_CATEGORIES
    ]
    settings = _quiet_settings(db, user.id, group.id)
    quiet = None
    if settings is not None and settings.quiet_start and settings.quiet_end:
        quiet = {
            "start": settings.quiet_start,
            "end": settings.quiet_end,
            "timezone": settings.timezone or user.timezone or group.home_timezone,
        }
    return {"group_id": group.id, "switches": switches, "quiet_hours": quiet}


def _parse_hhmm(value: str, field: str) -> time:
    """An "HH:MM" word as a time, or a plain-words 422."""
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        raise AppError(
            "validation_error",
            detail=f"{field} must be a time of day written HH:MM (e.g. 22:00).",
        )


def set_preferences(
    db: Session,
    *,
    actor_label: str,
    user: User,
    group: Group,
    switches: list[dict],
    quiet_hours: dict | None,
) -> dict:
    """Write the member's switches and quiet hours (B-100).

    Vocabulary is closed: an unknown channel or kind category
    is a 422, never a silently-stored string nothing reads.
    Quiet hours may be set (both ends required), or cleared by
    passing ``quiet_hours: null`` semantics — the router passes
    None to leave them untouched and {} to clear.
    """
    for switch in switches:
        channel = switch.get("channel")
        category = switch.get("kind")
        if channel not in CHANNELS:
            raise AppError(
                "validation_error",
                detail=f"Channel must be one of: {', '.join(CHANNELS)}.",
            )
        if category not in KIND_CATEGORIES:
            raise AppError(
                "validation_error",
                detail=f"Kind must be one of: {', '.join(KIND_CATEGORIES)}.",
            )
        row = _preference_row(db, user.id, group.id, channel, category)
        if row is None:
            row = NotificationPreference(
                user_id=user.id, group_id=group.id,
                channel=channel, kind_category=category,
            )
            db.add(row)
        row.enabled = bool(switch.get("enabled", True))
    if quiet_hours is not None:
        settings = _quiet_settings(db, user.id, group.id)
        if settings is None:
            settings = MemberNotificationSettings(user_id=user.id, group_id=group.id)
            db.add(settings)
        if quiet_hours == {}:
            settings.quiet_start = None
            settings.quiet_end = None
            settings.timezone = None
        else:
            start = quiet_hours.get("start")
            end = quiet_hours.get("end")
            if not start or not end:
                raise AppError(
                    "validation_error",
                    detail="Quiet hours need both a start and an end (HH:MM).",
                )
            _parse_hhmm(start, "quiet_hours.start")
            _parse_hhmm(end, "quiet_hours.end")
            tz_name = quiet_hours.get("timezone") or user.timezone or group.home_timezone
            try:
                ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                raise AppError(
                    "validation_error",
                    detail=f"'{tz_name}' is not a timezone this system knows.",
                )
            settings.quiet_start = start
            settings.quiet_end = end
            settings.timezone = tz_name
    audit.record(
        db, actor=actor_label, action="notification.preferences_set",
        group_id=group.id, entity_type="user", entity_id=user.id,
        detail={"switches": len(switches), "quiet_hours": quiet_hours is not None},
    )
    db.commit()
    return preferences_view(db, user=user, group=group)


def _quiet_settings(db: Session, user_id: str, group_id: str) -> MemberNotificationSettings | None:
    return (
        db.query(MemberNotificationSettings)
        .filter(
            MemberNotificationSettings.user_id == user_id,
            MemberNotificationSettings.group_id == group_id,
        )
        .one_or_none()
    )


def _quiet_window(
    db: Session, *, user: User, group: Group
) -> tuple[time, time, ZoneInfo] | None:
    """The member's quiet window (start, end, zone), or None."""
    settings = _quiet_settings(db, user.id, group.id)
    if settings is None or not settings.quiet_start or not settings.quiet_end:
        return None
    start = _parse_hhmm(settings.quiet_start, "quiet_start")
    end = _parse_hhmm(settings.quiet_end, "quiet_end")
    if start == end:
        return None  # An empty window is no window.
    zone = ZoneInfo(settings.timezone or user.timezone or group.home_timezone or "UTC")
    return start, end, zone


def _in_quiet(moment: datetime, start: time, end: time, zone: ZoneInfo) -> bool:
    """Is this UTC instant inside the window, in its own zone?
    Windows may wrap midnight (22:00 → 07:00 is the norm)."""
    local = moment.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone).time()
    if start < end:
        return start <= local < end
    return local >= start or local < end


def _quiet_end_after(moment: datetime, start: time, end: time, zone: ZoneInfo) -> datetime:
    """The UTC instant the current quiet window ends, for a
    moment inside it: today's end, or tomorrow's when the
    window wraps past midnight and we are in its early leg."""
    local = moment.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    end_today = local.replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0
    )
    if start > end and local.time() >= start:
        end_today += timedelta(days=1)  # Evening leg: ends tomorrow.
    return end_today.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


# ------------------------------------------------------------------
# Creating notifications (the outbox)
# ------------------------------------------------------------------


def create_notification(
    db: Session,
    *,
    group: Group | None,
    recipient_label: str,
    kind: str,
    subject: str,
    body_text: str,
    body_html: str = "",
    recipient_user: User | None = None,
    recipient_email: str = "",
    event_id: str | None = None,
    urgent: bool = False,
    send_after: datetime | None = None,
    reminder_rule: str = "",
    template_body: str = "",
    wording_source: str = "template",
    rsvp_id: str | None = None,
    now: datetime | None = None,
) -> Notification | None:
    """Write one notification row + its send job. NEVER sends.

    Policy is applied here, once: preferences choose the
    channels (a member with every channel off for the kind gets
    no row — the suppression is audited and None returned), and
    quiet hours hold a non-urgent reminder whose moment falls
    inside them. The caller owns the commit (this runs inside
    the caller's transaction, so the row, the job, and whatever
    the caller was doing land together or not at all).
    """
    now = now or now_utc()
    category = category_of(kind)
    if recipient_user is not None and group is not None:
        send_email = channel_allowed(
            db, user=recipient_user, group=group, channel="email", category=category
        )
        in_app = channel_allowed(
            db, user=recipient_user, group=group, channel="in_app", category=category
        )
        if not recipient_email:
            recipient_email = recipient_user.email
    elif recipient_user is not None:
        send_email, in_app = True, True
        if not recipient_email:
            recipient_email = recipient_user.email
    else:
        # An outside guest: email is the only road to them.
        send_email, in_app = True, False
    if not send_email and not in_app:
        audit.record(
            db, actor="system:notify", action="notification.suppressed",
            group_id=group.id if group else None,
            entity_type="event", entity_id=event_id or "",
            detail={"to": recipient_label, "kind": kind, "reason": "preferences"},
        )
        return None

    due = send_after or now
    status = "queued"
    if (
        category == "reminder"
        and not urgent
        and recipient_user is not None
        and group is not None
    ):
        window = _quiet_window(db, user=recipient_user, group=group)
        if window is not None and _in_quiet(due, *window):
            due = _quiet_end_after(due, *window)
            status = "held"

    row = Notification(
        group_id=group.id if group else None,
        event_id=event_id,
        recipient_user_id=recipient_user.id if recipient_user else None,
        recipient_label=recipient_label,
        recipient_email=recipient_email,
        kind=kind,
        send_email=send_email,
        in_app=in_app,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        template_body=template_body,
        wording_source=wording_source,
        urgent=urgent,
        send_after=due,
        status=status,
        reminder_rule=reminder_rule,
    )
    db.add(row)
    db.flush()  # The job's payload names this row.
    job = job_service.queue(
        db,
        kind=NOTIFY_JOB_KIND,
        payload={
            "notification_id": row.id,
            "group_id": row.group_id,
            "event_id": event_id,
            "rsvp_id": rsvp_id,
            "to": recipient_label,
            "kind": kind,
        },
        run_at=due,
        max_attempts=MAX_SEND_ATTEMPTS,
    )
    row.job_id = job.id
    audit.record(
        db, actor="system:notify", action="notification.created",
        group_id=row.group_id, entity_type="notification", entity_id=row.id,
        detail={"to": recipient_label, "kind": kind, "status": status},
    )
    db.flush()
    return row


def event_recipients(db: Session, event_id: str) -> list[dict]:
    """Who hears about an event: its RSVP'd people (coming and
    maybe — the people with a stake in the moment), each as
    {user, label, email}. Guests who answered through an invite
    token ride as user=None with their invited address; a
    member's +guests are the member's own party and are reached
    through the member, never as separate rows (B-097)."""
    rows = (
        db.query(Rsvp)
        .filter(Rsvp.event_id == event_id, Rsvp.status.in_(("coming", "maybe")))
        .order_by(Rsvp.created_at, Rsvp.id)
        .all()
    )
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row.principal_label in seen:
            continue
        seen.add(row.principal_label)
        user = db.get(User, row.user_id) if row.user_id else None
        email = user.email if user else ""
        if user is None and row.principal_label.startswith("guest:"):
            email = row.principal_label.removeprefix("guest:")
        out.append(
            {"user": user, "label": row.principal_label, "email": email, "rsvp": row}
        )
    return out


def notify_rsvp_promotion(
    db: Session, *, group: Group, event, rsvp: Rsvp
) -> Notification | None:
    """A freed seat reached the next in line (B-061's flow, now
    a real notification through the outbox instead of the old
    audit-only stub). Called by services/rsvps.py."""
    user = db.get(User, rsvp.user_id) if rsvp.user_id else None
    return create_notification(
        db,
        group=group,
        recipient_user=user,
        recipient_label=rsvp.principal_label,
        recipient_email=user.email if user else "",
        kind="rsvp.promoted",
        subject=f"A place opened up at '{event.title}'",
        body_text=(
            f"A place opened up at '{event.title}' — you were next "
            "in line and now have a seat."
        ),
        event_id=event.id,
        rsvp_id=rsvp.id,
    )


# ------------------------------------------------------------------
# Delivery (the notify job handler)
# ------------------------------------------------------------------


def deliver(db: Session, notification: Notification) -> None:
    """Send one notification through the transport.

    The in-app channel needs no sending — the row IS the inbox
    item. Email goes through the mailer boundary; a
    TransportError marks the row failed (or dead at the attempt
    ceiling) and RAISES, so the job chassis retries with
    backoff and dead-letters in its own bookkeeping too. This
    function never runs inside a user request.
    """
    if notification.status in ("superseded", "sent", "dead"):
        return
    now = now_utc()
    if notification.send_after and notification.send_after > now:
        # The job fired early (a manual requeue ahead of a held
        # row, say): re-queue at the due moment and stand down —
        # the words must not arrive before their time.
        job = job_service.queue(
            db,
            kind=NOTIFY_JOB_KIND,
            payload={
                "notification_id": notification.id,
                "group_id": notification.group_id,
                "event_id": notification.event_id,
                "to": notification.recipient_label,
                "kind": notification.kind,
            },
            run_at=notification.send_after,
            max_attempts=MAX_SEND_ATTEMPTS,
        )
        notification.job_id = job.id
        db.flush()
        return
    if notification.send_email and notification.recipient_email:
        try:
            mailer.get_transport().send(
                to=notification.recipient_email,
                subject=notification.subject,
                body_text=notification.body_text,
                body_html=notification.body_html,
            )
        except mailer.TransportError as exc:
            notification.attempts += 1
            notification.last_error = str(exc)[:2000]
            notification.status = (
                "dead" if notification.attempts >= MAX_SEND_ATTEMPTS else "failed"
            )
            # COMMIT before raising: the job chassis rolls its
            # session back on a handler failure, and the failure
            # record is exactly what must survive that rollback
            # (B-102 — a failure is data, not a side effect).
            db.commit()
            raise
    notification.status = "sent"
    notification.sent_at = now
    notification.last_error = ""
    db.flush()


def handle_notify(db: Session, payload: dict) -> None:
    """The ``notify`` job handler: deliver one outbox row.

    Registered for the kind the job chassis has carried since
    B-061; the payload names the notification (Phase 8's shape).
    A payload without one is a pre-outbox shape — it is recorded
    in the audit, in the old stub's words, and never fails.
    """
    notification_id = payload.get("notification_id")
    if not notification_id:
        audit.record(
            db, actor="system:notify", action="notification.queued",
            group_id=payload.get("group_id"),
            entity_type="event", entity_id=payload.get("event_id", ""),
            detail={
                "to": payload.get("to"), "kind": payload.get("kind"),
                "message": payload.get("message"),
            },
        )
        return
    notification = db.get(Notification, notification_id)
    if notification is None:
        return  # The row is gone (a purge); the job is done.
    deliver(db, notification)


def register_handlers() -> None:
    """Register the notify handler with the job chassis."""
    job_service.register_handler(NOTIFY_JOB_KIND, handle_notify)


register_handlers()


# ------------------------------------------------------------------
# Inbox (B-101) and deliverability (B-102)
# ------------------------------------------------------------------


def inbox(db: Session, *, user: User, unread_only: bool = False) -> list[Notification]:
    """The person's inbox: their in-app notifications, newest
    first, superseded rows excluded (they were replaced before
    they ever arrived)."""
    query = db.query(Notification).filter(
        Notification.recipient_user_id == user.id,
        Notification.in_app.is_(True),
        Notification.status != "superseded",
    )
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    return query.order_by(Notification.created_at.desc(), Notification.id.desc()).all()


def mark_read(db: Session, *, user: User, notification_id: str) -> Notification:
    """Mark one inbox item read. Another person's item is a
    404 — an inbox is private, and its existence is too."""
    row = db.get(Notification, notification_id)
    if row is None or row.recipient_user_id != user.id or not row.in_app:
        raise AppError("not_found", detail="That inbox item could not be found.")
    if row.read_at is None:
        row.read_at = now_utc()
    db.commit()
    return row


def list_failures(db: Session, *, group: Group) -> dict:
    """The admin's deliverability view (B-102): this group's
    failed and dead notifications, plus the provider's mail
    events about them — a failing address is VISIBLE here,
    never silently retried forever."""
    rows = (
        db.query(Notification)
        .filter(
            Notification.group_id == group.id,
            Notification.status.in_(("failed", "dead")),
        )
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .all()
    )
    ids = [row.id for row in rows]
    events = (
        db.query(MailEvent)
        .filter(MailEvent.notification_id.in_(ids))
        .order_by(MailEvent.created_at.desc(), MailEvent.id.desc())
        .all()
        if ids
        else []
    )
    return {"notifications": rows, "mail_events": events}


def resend(
    db: Session, *, actor_label: str, group: Group, notification_id: str
) -> Notification:
    """Requeue a failed or dead notification (B-102): an
    admin's deliberate act — attempts reset, a fresh send job
    at once, the row queued again. Nothing retries forever on
    its own; a person decides a dead letter deserves one more
    road."""
    row = db.get(Notification, notification_id)
    if row is None or row.group_id != group.id:
        raise AppError("not_found", detail="That notification could not be found.")
    if row.status not in ("failed", "dead", "held", "queued"):
        raise AppError(
            "conflict",
            detail=f"A notification in status '{row.status}' cannot be resent.",
        )
    row.attempts = 0
    row.last_error = ""
    row.status = "queued"
    row.send_after = min(row.send_after, now_utc())
    db.flush()
    job = job_service.queue(
        db,
        kind=NOTIFY_JOB_KIND,
        payload={
            "notification_id": row.id,
            "group_id": row.group_id,
            "event_id": row.event_id,
            "to": row.recipient_label,
            "kind": row.kind,
        },
        run_at=row.send_after,
        max_attempts=MAX_SEND_ATTEMPTS,
    )
    row.job_id = job.id
    audit.record(
        db, actor=actor_label, action="notification.resent",
        group_id=group.id, entity_type="notification", entity_id=row.id,
        detail={"to": row.recipient_label, "kind": row.kind},
    )
    db.commit()
    return row


def record_mail_event(
    db: Session,
    *,
    provider: str,
    email: str,
    event_type: str,
    notification_id: str | None = None,
    detail: str = "",
) -> MailEvent:
    """Record a provider webhook's word (B-102, stubbed
    receiver): a bounce also marks its notification failed —
    the address said no, and the failure list must show it.
    (The real provider's signature checks land with it in
    Phase 12; this receiver is the recorded shape.)"""
    if event_type not in ("bounce", "complaint"):
        raise AppError(
            "validation_error",
            detail="A mail event is a 'bounce' or a 'complaint'.",
        )
    row = MailEvent(
        provider=provider, email=email, event_type=event_type,
        notification_id=notification_id, detail=detail,
    )
    db.add(row)
    if notification_id:
        notification = db.get(Notification, notification_id)
        if notification is not None and event_type == "bounce":
            notification.status = "failed"
            notification.last_error = f"bounced ({provider})"
    audit.record(
        db, actor=f"system:webhook:{provider}", action="mail.event",
        group_id=None, entity_type="notification", entity_id=notification_id or "",
        detail={"email": email, "event_type": event_type},
    )
    db.commit()
    return row
