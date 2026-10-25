"""Announcements board (roadmap B-106).

A board of organiser-posted announcements, separate from chat
and from event discussion. An announcement is NOT a publication
shortcut: posting one requires an organiser ACCOUNT — the
B-043 law restated for humans. In service terms:

- a plain member cannot announce (the door refuses: 403);
- an agent seat cannot announce either, even holding an
  organiser role and publish scope — a seat drafts candidates
  for the queue; it does not speak to the group as the group.

Posts may be pinned (they lead the board) and may expire (an
expired post leaves the board; the row stays — expiry is a
state, not an erasure, and organisers can still read it). A
post may be scoped to one sub-group, and then the board shows
it under the B-105 rule.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.nonprofit import Announcement
from gpa.services import audit


def _require_person(actor_is_seat: bool) -> None:
    """The B-043 law, restated for the board: announcing is a
    person's act with an organiser account."""
    if actor_is_seat:
        raise AppError(
            "forbidden",
            detail=(
                "An announcement needs an organiser account — "
                "a seat drafts for the review queue, it does not announce."
            ),
        )


def get_announcement(db: Session, group: Group, announcement_id: str) -> Announcement:
    """One announcement of this group, or 404."""
    row = db.get(Announcement, announcement_id)
    if row is None or row.group_id != group.id:
        raise AppError("not_found", detail="That announcement could not be found.")
    return row


def create_announcement(
    db: Session,
    *,
    actor_label: str,
    actor_user_id: str | None,
    actor_is_seat: bool,
    group: Group,
    title: str,
    body: str,
    pinned: bool = False,
    expires_at: datetime | None = None,
    subgroup_id: str | None = None,
) -> Announcement:
    """Post to the board. Role (organiser+) is the door's job;
    the person's-act rule is enforced HERE so no route can
    forget it."""
    _require_person(actor_is_seat)
    if not title or not title.strip():
        raise AppError("validation_error", detail="An announcement needs a title.")
    if not body or not body.strip():
        raise AppError("validation_error", detail="An announcement needs its words.")
    if subgroup_id is not None:
        from gpa.services import subgroups as subgroup_service

        subgroup_service.get_subgroup(db, group, subgroup_id)
    row = Announcement(
        group_id=group.id, subgroup_id=subgroup_id,
        title=title.strip()[:240], body=body, pinned=bool(pinned),
        expires_at=expires_at, created_by=actor_label,
        author_user_id=actor_user_id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db, actor=actor_label, action="announcement.created",
        group_id=group.id, entity_type="announcement", entity_id=row.id,
        detail={"title": row.title, "subgroup_id": subgroup_id, "pinned": row.pinned},
    )
    db.commit()
    return row


def list_announcements(
    db: Session,
    group: Group,
    *,
    visible_ids: set[str] | None,
    include_expired: bool = False,
    now: datetime | None = None,
) -> list[Announcement]:
    """The board as one viewer sees it: scoped posts under the
    B-105 rule, expired posts only when asked (the router only
    honours that ask from organisers), pinned first, then
    newest first."""
    from gpa.services import subgroups as subgroup_service

    moment = now or now_utc()
    rows = (
        db.query(Announcement)
        .filter(Announcement.group_id == group.id)
        .all()
    )
    out = []
    for row in rows:
        if not subgroup_service.can_see_scoped(row.subgroup_id, visible_ids):
            continue
        if not include_expired and row.expires_at is not None and row.expires_at <= moment:
            continue
        out.append(row)
    out.sort(key=lambda r: (not r.pinned, -r.created_at.timestamp(), r.id))
    return out


def update_announcement(
    db: Session,
    *,
    actor_label: str,
    actor_is_seat: bool,
    group: Group,
    announcement_id: str,
    changes: dict,
) -> Announcement:
    """Edit a post: words, pin, expiry, scope."""
    _require_person(actor_is_seat)
    row = get_announcement(db, group, announcement_id)
    if "title" in changes:
        if not str(changes["title"]).strip():
            raise AppError("validation_error", detail="An announcement needs a title.")
        row.title = str(changes["title"]).strip()[:240]
    if "body" in changes:
        if not str(changes["body"]).strip():
            raise AppError("validation_error", detail="An announcement needs its words.")
        row.body = str(changes["body"])
    if "pinned" in changes:
        row.pinned = bool(changes["pinned"])
    if "expires_at" in changes:
        row.expires_at = changes["expires_at"]
    if "subgroup_id" in changes:
        if changes["subgroup_id"] is not None:
            from gpa.services import subgroups as subgroup_service

            subgroup_service.get_subgroup(db, group, changes["subgroup_id"])
        row.subgroup_id = changes["subgroup_id"]
    audit.record(
        db, actor=actor_label, action="announcement.updated",
        group_id=group.id, entity_type="announcement", entity_id=row.id,
        detail={"fields": sorted(changes)},
    )
    db.commit()
    return row


def delete_announcement(
    db: Session,
    *,
    actor_label: str,
    actor_is_seat: bool,
    group: Group,
    announcement_id: str,
) -> None:
    """Take a post down. The row goes; the audit keeps the fact
    (and the title) — a taken-down post is a decision, recorded
    like any other."""
    _require_person(actor_is_seat)
    row = get_announcement(db, group, announcement_id)
    title = row.title
    db.delete(row)
    audit.record(
        db, actor=actor_label, action="announcement.deleted",
        group_id=group.id, entity_type="announcement", entity_id=announcement_id,
        detail={"title": title},
    )
    db.commit()


def is_expired(row: Announcement, *, now: datetime | None = None) -> bool:
    """Has this post's moment passed?"""
    if row.expires_at is None:
        return False
    return row.expires_at <= (now or now_utc())
