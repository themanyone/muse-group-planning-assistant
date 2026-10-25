"""Event invites (roadmap B-098).

An invite is a bearer link with the same discipline as every
token in this codebase (B-014): the raw token is shown ONCE, at
creation, and only its SHA-256 hash rests in the database. The
link opens the event's public face and lets its holder RSVP
WITHOUT an account — their answer lands as ``guest:<email>``,
attributed to the address the invite was sent to — and an
account is offered after, never demanded before.

Tampering fails closed: a token whose hash matches nothing is
a 404 that creates nothing — the router test walks a flipped
character through and asserts no RSVP exists afterwards.
Invites to people WITH accounts are notifications too (kind
``invite``), so a member hears about it in the channels they
chose; the email to an outside address IS the invite itself,
carrying the link.
"""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.events import Event, Rsvp
from gpa.models.groups import Group
from gpa.models.identity import User, now_utc
from gpa.models.notifications import EventInvite
from gpa.services import audit
from gpa.services import notifications as notify_service
from gpa.services import rsvps as rsvp_service

# An invite link lives a fortnight by default — long enough for
# a person to find the email again, short enough that a stale
# link is not a permanent door.
INVITE_TTL_DAYS = 14


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invite_link(settings: Settings, token: str) -> str:
    """The link the invitee opens, on the configured base."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/invite/{token}"


def create_invite(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    event: Event,
    email: str,
) -> tuple[EventInvite, str]:
    """Create one invite and notify its holder. Returns the row
    and the raw token — the ONLY moment the token exists
    outside the invitee's mailbox."""
    email = email.strip().lower()
    member = db.query(User).filter(User.email == email).one_or_none()
    raw = secrets.token_urlsafe(32)
    invite = EventInvite(
        group_id=group.id,
        event_id=event.id,
        email=email,
        user_id=member.id if member else None,
        token_hash=_hash(raw),
        token_prefix=raw[:8],
        invited_by=actor_label,
        expires_at=now_utc() + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    db.flush()
    link = invite_link(settings, raw)
    notify_service.create_notification(
        db,
        group=group,
        recipient_user=member,
        recipient_label=f"user:{member.id}" if member else f"guest:{email}",
        recipient_email=email,
        kind="invite",
        subject=f"You're invited: {event.title}",
        body_text=(
            f"You are invited to '{event.title}'. Open this link to "
            f"see the event and answer: {link}"
        ),
        event_id=event.id,
    )
    audit.record(
        db, actor=actor_label, action="invite.created",
        group_id=group.id, entity_type="event_invite", entity_id=invite.id,
        detail={"email": email, "event_id": event.id, "member": member is not None},
    )
    db.commit()
    return invite, raw


def find_invite(db: Session, token: str) -> EventInvite:
    """Resolve a presented token, or 404 — tampered, unknown,
    and expired are the same closed door, and none of them
    create anything."""
    invite = (
        db.query(EventInvite)
        .filter(EventInvite.token_hash == _hash(token))
        .one_or_none()
    )
    if invite is None or invite.expires_at <= now_utc():
        raise AppError("not_found", detail="That invitation link could not be found.")
    return invite


def respond(
    db: Session,
    settings: Settings,
    *,
    token: str,
    status: str,
    guests: int = 0,
    note: str = "",
) -> tuple[EventInvite, Rsvp]:
    """Answer an invite without an account (B-098's core
    promise): the RSVP lands under ``guest:<email>`` — the
    invited address, which is the attribution the group sees —
    through the very same RSVP service members use, so capacity
    and the waitlist treat a guest's answer exactly like a
    member's; the invite records its answer."""
    invite = find_invite(db, token)
    group = db.get(Group, invite.group_id)
    label = f"guest:{invite.email}"
    rsvp = rsvp_service.set_rsvp(
        db,
        settings,
        principal_label=label,
        user_id=None,
        seat_id=None,
        group=group,
        event_id=invite.event_id,
        status=status,
        guests=guests,
        note=note,
    )
    invite.status = "accepted" if status in ("coming", "maybe") else "declined"
    invite.rsvp_id = rsvp.id
    invite.responded_at = now_utc()
    audit.record(
        db, actor=label, action="invite.responded",
        group_id=group.id, entity_type="event_invite", entity_id=invite.id,
        detail={"status": status, "rsvp_id": rsvp.id},
    )
    db.commit()
    return invite, rsvp
