"""Everything the system holds about one person
(roadmap B-144): ``GET /api/v1/users/me/data``.

The shape is written for the person it describes, not for
the database: sections in plain words, records resolved to
the names a member would recognise (group names, event
titles), and — by construction — no secret material: no
password hash, no token hashes, no API-key hashes or sealed
values anywhere in the payload. Keys appear by their public
prefix and label, the way the member's own key list shows
them.

This is a READ of the person's own footprint: their account,
their memberships, the things they wrote and chose (comments,
RSVPs, votes, attendance), the candidates they authored,
their notifications, their linked outside accounts, and the
seats they created. Other people's content ABOUT them (a
comment someone else left) is that author's record, not
theirs, and does not ride here.
"""

from sqlalchemy.orm import Session

from gpa.models.events import Comment, Event, Rsvp
from gpa.models.groups import AgentSeat, Candidate, Group, Membership
from gpa.models.identity import ApiKey, User
from gpa.models.integrations import ExternalAccountLink
from gpa.models.nonprofit import AttendanceMark
from gpa.models.notifications import Notification
from gpa.models.planning import Poll, PollVote, Task


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def build_me_data(db: Session, user: User) -> dict:
    label = f"user:{user.id}"

    memberships = (
        db.query(Membership).filter(Membership.user_id == user.id).all()
    )
    groups_by_id = {}
    for membership in memberships:
        group = db.get(Group, membership.group_id)
        if group is not None:
            groups_by_id[group.id] = group.name
    my_groups = [
        {
            "group_id": m.group_id,
            "group_name": groups_by_id.get(m.group_id, ""),
            "role": m.role,
            "status": m.status,
            "joined_at": _iso(m.created_at),
        }
        for m in memberships
    ]

    api_keys = (
        db.query(ApiKey).filter(ApiKey.user_id == user.id).all()
    )
    my_keys = [
        {
            "label": key.label,
            "prefix": key.prefix,  # the public prefix, never the key
            "scopes": sorted(key.scope_set()),
            "created_at": _iso(key.created_at),
            "last_used_at": _iso(key.last_used_at),
            "revoked_at": _iso(key.revoked_at),
        }
        for key in api_keys
    ]

    seats = (
        db.query(AgentSeat)
        .filter(AgentSeat.created_by_user_id == user.id)
        .all()
    )
    my_seats = [
        {
            "seat_id": seat.id,
            "name": seat.name,
            "group_id": seat.group_id,
            "group_name": groups_by_id.get(seat.group_id)
            or (db.get(Group, seat.group_id).name if db.get(Group, seat.group_id) else ""),
            "role": seat.role,
            "created_at": _iso(seat.created_at),
        }
        for seat in seats
    ]

    notifications = (
        db.query(Notification)
        .filter(Notification.recipient_user_id == user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    my_notifications = {
        "total": len(notifications),
        "recent": [
            {
                "kind": n.kind,
                "subject": n.subject,
                "group_name": groups_by_id.get(n.group_id or "", ""),
                "created_at": _iso(n.created_at),
            }
            for n in notifications[:20]
        ],
    }

    comments = (
        db.query(Comment)
        .filter(Comment.author_user_id == user.id)
        .order_by(Comment.created_at)
        .all()
    )
    my_comments = [
        {
            "body": c.body,
            "created_at": _iso(c.created_at),
            "edited": c.edited_at is not None,
        }
        for c in comments
    ]

    rsvps = db.query(Rsvp).filter(Rsvp.user_id == user.id).all()
    my_rsvps = []
    for rsvp in rsvps:
        event = db.get(Event, rsvp.event_id)
        my_rsvps.append({
            "event_id": rsvp.event_id,
            "event_title": event.title if event else "",
            "status": rsvp.status,
        })

    votes = db.query(PollVote).filter(PollVote.user_id == user.id).all()
    my_votes = []
    for vote in votes:
        poll = db.get(Poll, vote.poll_id)
        my_votes.append({
            "poll_id": vote.poll_id,
            "question": poll.question if poll else "",
            "option_id": vote.option_id,
        })

    attendance = (
        db.query(AttendanceMark)
        .filter(AttendanceMark.user_id == user.id)
        .all()
    )
    my_attendance = []
    for mark in attendance:
        event = db.get(Event, mark.event_id)
        my_attendance.append({
            "event_id": mark.event_id,
            "event_title": event.title if event else "",
            "marked_at": _iso(mark.created_at),
        })

    tasks = db.query(Task).filter(Task.owner_id == user.id).all()
    my_tasks = [
        {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "group_name": groups_by_id.get(task.group_id, ""),
        }
        for task in tasks
    ]

    candidates = (
        db.query(Candidate).filter(Candidate.created_by == label).all()
    )
    my_candidates = [
        {
            "candidate_id": c.id,
            "kind": c.kind,
            "status": c.status,
            "group_name": groups_by_id.get(c.group_id, ""),
            "created_at": _iso(c.created_at),
        }
        for c in candidates
    ]

    links = (
        db.query(ExternalAccountLink)
        .filter(ExternalAccountLink.user_id == user.id)
        .all()
    )
    my_links = [
        {
            "connector": link.connector_kind,
            "external_name": link.external_name,
            "group_name": groups_by_id.get(link.group_id, ""),
            "linked_at": _iso(link.created_at),
        }
        for link in links
    ]

    return {
        "account": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "timezone": user.timezone,
            "locale": user.locale,
            "member_since": _iso(user.created_at),
        },
        "my_groups": my_groups,
        "my_api_keys": my_keys,
        "seats_i_created": my_seats,
        "my_notifications": my_notifications,
        "comments_i_wrote": my_comments,
        "my_rsvps": my_rsvps,
        "my_poll_votes": my_votes,
        "my_attendance": my_attendance,
        "tasks_assigned_to_me": my_tasks,
        "candidates_i_authored": my_candidates,
        "my_linked_accounts": my_links,
    }
