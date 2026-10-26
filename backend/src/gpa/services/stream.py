"""The group event stream (roadmap B-033, §5's stream contract).

What this file is for: a group's screens — the frontend's review
queue, the terminal app later — need to watch ingest WORK happen:
a transcription queued, running, done. This module is that watch.

It is deliberately built on the Job table, the durable record,
and NOT on an in-memory event bus: a bus forgets everything the
moment the server restarts or a second process serves, while the
table is the same truth the retry machinery and the Doctor read.
The generator polls the group's jobs and speaks two event kinds:

- ``snapshot`` — once, on connect: the group's recent jobs with
  their status and progress, so a fresh screen paints the world
  as it is;
- ``job.progress`` — whenever a job's status or progress
  changes: id, kind, status, progress, and the source it serves.
- ``comment.created`` — whenever a comment lands in one of the
  group's discussion threads (B-060; any parent since B-084):
  the comment id, its parent, its author, and its words.
  Polled from the Comment table like
  everything else here — a comment hidden or deleted later is
  a moderation act the REST views show; the stream announces
  arrivals, it does not rewrite them.
- ``notification.created`` — whenever a notification lands in
  the CONNECTED member's inbox for this group (Phase 8,
  B-101): the nudge that tells their screen to re-read the
  inbox. Only the recipient's own stream carries it — the
  frame names the kind and the event, never the body; the
  inbox itself is the content's home.

Honesty note (B-033): the ingests that complete synchronously
(paste, file, newsletter, image, link) never pass through a job,
so their stage/progress arrives final in the create response —
there is nothing to stream. Job-driven work (transcription
today, extraction and publishing later) is what flows here.
"""

import json
import time
from typing import Callable, Iterator

from sqlalchemy.orm import Session

from gpa.models.core import Job
from gpa.models.events import Comment, Thread
from gpa.models.notifications import Notification

# How much of a group's job history a snapshot carries. Screens
# care about recent work; the full history lives behind the jobs
# API and the audit trail.
SNAPSHOT_LIMIT = 50


def _payload(job: Job) -> dict:
    # Sealed at rest since B-166; payload_of is the reading
    # law. A row that cannot be read projects as empty rather
    # than breaking the stream.
    from gpa.errors import AppError
    from gpa.services import jobs as job_service

    try:
        return job_service.payload_of(job)
    except (json.JSONDecodeError, AppError):
        return {}


def _job_summary(job: Job) -> dict:
    payload = _payload(job)
    return {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "source_id": payload.get("source_id"),
    }


def group_jobs(db: Session, group_id: str) -> list[Job]:
    """The group's jobs, newest first (jobs name their group in
    their payload — the chassis' one addressing rule)."""
    rows = (
        db.query(Job)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(500)
        .all()
    )
    return [job for job in rows if _payload(job).get("group_id") == group_id]


def snapshot(db: Session, group_id: str) -> dict:
    """The connect-time picture: recent jobs with status/progress."""
    jobs = group_jobs(db, group_id)[:SNAPSHOT_LIMIT]
    return {"group_id": group_id, "jobs": [_job_summary(job) for job in jobs]}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _group_comments(db: Session, group_id: str) -> list[tuple[Comment, Thread]]:
    """The group's comments (through their threads), oldest
    first — the marker arithmetic below walks this order."""
    rows = (
        db.query(Comment, Thread)
        .join(Thread, Comment.thread_id == Thread.id)
        .filter(Thread.group_id == group_id)
        .order_by(Comment.created_at, Comment.id)
        .all()
    )
    return rows


def _comment_frame(comment: Comment, thread: Thread) -> dict:
    return {
        "comment_id": comment.id,
        "thread_id": thread.id,
        "event_id": thread.parent_id if thread.parent_type == "event" else None,
        "parent_type": thread.parent_type,
        "parent_id": thread.parent_id,
        "author": comment.author_label,
        "body": comment.body,
        "created_at": comment.created_at.isoformat() + "Z",
    }


def _my_notifications(db: Session, group_id: str, user_id: str) -> list[Notification]:
    """The connected member's inbox rows for this group, oldest
    first — the marker arithmetic walks this order."""
    return (
        db.query(Notification)
        .filter(
            Notification.group_id == group_id,
            Notification.recipient_user_id == user_id,
            Notification.in_app.is_(True),
            Notification.status != "superseded",
        )
        .order_by(Notification.created_at, Notification.id)
        .all()
    )


def _notification_frame(row: Notification) -> dict:
    """The nudge: kind + event + subject, never the body —
    the inbox is the content's home (B-101)."""
    return {
        "notification_id": row.id,
        "kind": row.kind,
        "event_id": row.event_id,
        "subject": row.subject,
        "urgent": row.urgent,
        "created_at": row.created_at.isoformat() + "Z",
    }


def group_events(
    session_factory: Callable[[], Session],
    group_id: str,
    *,
    user_id: str | None = None,
    poll_seconds: float = 1.0,
    max_polls: int | None = None,
) -> Iterator[str]:
    """Yield SSE frames: one snapshot, then job.progress changes,
    comment.created arrivals, and (for a connected member) the
    notification.created nudges for their own inbox.

    ``max_polls`` bounds the generator for tests and short-lived
    consumers; the HTTP route leaves it None and the stream runs
    until the client disconnects. Each poll reads through a
    fresh session — the table, not this process's memory, is
    the state being watched.
    """
    seen: dict[str, tuple[str, int]] = {}
    with session_factory() as db:
        snap = snapshot(db, group_id)
        existing = _group_comments(db, group_id)
        existing_notifications = (
            _my_notifications(db, group_id, user_id) if user_id else []
        )
    for job in snap["jobs"]:
        seen[job["job_id"]] = (job["status"], job["progress"])
    # The comment marker starts at the newest comment that
    # existed at connect: the stream announces NEW arrivals,
    # it does not replay the group's whole conversation.
    comment_marker: tuple | None = None
    if existing:
        newest = existing[-1][0]
        comment_marker = (newest.created_at, newest.id)
    notification_marker: tuple | None = None
    if existing_notifications:
        newest_row = existing_notifications[-1]
        notification_marker = (newest_row.created_at, newest_row.id)
    yield _sse("snapshot", snap)
    polls = 0
    while max_polls is None or polls < max_polls:
        time.sleep(poll_seconds)
        polls += 1
        with session_factory() as db:
            jobs = group_jobs(db, group_id)[:SNAPSHOT_LIMIT]
            comments = _group_comments(db, group_id)
            notifications = (
                _my_notifications(db, group_id, user_id) if user_id else []
            )
        for job in jobs:
            summary = _job_summary(job)
            state = (summary["status"], summary["progress"])
            if seen.get(summary["job_id"]) != state:
                seen[summary["job_id"]] = state
                yield _sse("job.progress", summary)
        for comment, thread in comments:
            marker = (comment.created_at, comment.id)
            if comment_marker is None or marker > comment_marker:
                comment_marker = marker
                if comment.deleted_at is None and comment.hidden_at is None:
                    yield _sse("comment.created", _comment_frame(comment, thread))
        for row in notifications:
            marker = (row.created_at, row.id)
            if notification_marker is None or marker > notification_marker:
                notification_marker = marker
                yield _sse("notification.created", _notification_frame(row))
