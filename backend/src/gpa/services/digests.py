"""The weekly digest (roadmap B-099).

Once a week, each group gets one email (and inbox item) that
says what its week holds: the planning digest's contents
(B-093 — tasks done, decisions settled, questions open, tasks
due) joined with the week ahead's published events. The
digest composes from those two sources, renders through the
in-repo Jinja templates (``src/gpa/templates/digest.txt.j2`` /
``digest.html.j2``) into plain text AND HTML, stores the
``Digest`` row (both renderings, so the sent words are the
stored words), and delivers one notification per member
through the outbox — the digest itself never sends inline.

The alt-text law stands over the HTML: the shipped templates
carry no images at all, and compose still RUNS the check —
any ``<img`` without a real ``alt`` fails the compose loudly
rather than shipping a silent letter.

Scheduling: a ``digest_run`` job chain — saving a group's
digest settings (or an organiser pressing "send now") anchors
the next run at the group's configured weekday/time; each run
composes, delivers, and queues its own successor a week out.
The anchor is recorded in the audit; a group whose digest is
disabled gets no chain.
"""

import json
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader
from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group, Membership
from gpa.models.identity import User, now_utc
from gpa.models.core import Job
from gpa.models.notifications import Digest
from gpa.internal.client import call_internal
from gpa.services import audit
from gpa.services import jobs as job_service
from gpa.services import notifications as notify_service
from gpa.services import planning

DIGEST_JOB_KIND = "digest_run"
DEFAULT_DIGEST_WEEKDAY = 0  # Monday
DEFAULT_DIGEST_TIME = "08:00"

def _autoescape(template_name: str | None) -> bool:
    """Escape by file KIND (B-142): "digest.html.j2" is markup
    and escapes; "digest.txt.j2" is plain text and must not —
    escaping it put literal "&lt;" in members' text mail, and
    Jinja's select_autoescape keys on the final extension
    alone (".j2" for both), so the decision is spelled out
    here instead of inferred from suffix lists."""
    if template_name is None:
        return True
    return ".html." in template_name or template_name.endswith(
        (".html", ".htm", ".xml")
    )


_env = Environment(
    loader=PackageLoader("gpa", "templates"),
    autoescape=_autoescape,
    trim_blocks=True,
    lstrip_blocks=True,
)

_IMG_WITHOUT_ALT = re.compile(
    r"<img\b(?![^>]*\balt\s*=\s*\"[^\"]+\")[^>]*>", re.IGNORECASE
)


def assert_alt_text(html: str) -> None:
    """The image law, executable: every <img> in a rendered
    letter must carry a real (non-empty) alt text, or the
    compose fails — an image a screen reader cannot speak is
    a defect, in mail as in books."""
    bad = _IMG_WITHOUT_ALT.search(html)
    if bad:
        raise AppError(
            "internal_error",
            detail="The digest HTML carries an image without alt text.",
        )


def _format_when(event: Event) -> str:
    zone = ZoneInfo(event.timezone or "UTC")
    local = event.starts_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    return local.strftime("%A %d %B, %H:%M")


def compose_digest(db: Session, *, group: Group, now: datetime | None = None) -> Digest:
    """Compose and STORE this week's digest (unsent). The
    material is B-093's planning digest — this week's published
    events, unclaimed tasks, open questions, undecided polls —
    extended with the week ahead's published events, so
    "coming up" spans the digest week and the one after."""
    now = now or now_utc()
    plan = planning.planning_digest(db, group, now=now)
    week_end = plan["week_end"]
    ahead_rows = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.deleted_at.is_(None),
            Event.status == "published",
            Event.starts_at >= week_end,
            Event.starts_at < week_end + timedelta(days=7),
        )
        .order_by(Event.starts_at, Event.id)
        .all()
    )
    upcoming = sorted(
        [*plan["events"], *ahead_rows], key=lambda e: (e.starts_at, e.id)
    )
    context = {
        "group_name": group.name,
        "week_label": (
            f"{plan['week_start'].strftime('%d %B')} – "
            f"{week_end.strftime('%d %B %Y')}"
        ),
        "upcoming": [
            {
                "title": event.title,
                "when": _format_when(event),
                "place": ", ".join(
                    part for part in (event.place_name, event.place_address) if part
                ),
            }
            for event in upcoming
        ],
        "unclaimed_tasks": [
            {"title": task.title} for task in plan["unclaimed_tasks"]
        ],
        "questions": [
            {"text": question.text} for question in plan["open_questions"]
        ],
        "polls": [
            {"question": poll.question} for poll in plan["undecided_polls"]
        ],
    }
    body_html = _env.get_template("digest.html.j2").render(**context)
    assert_alt_text(body_html)
    body_text = _env.get_template("digest.txt.j2").render(**context)
    digest = Digest(
        group_id=group.id,
        week_start=plan["week_start"],
        week_end=plan["week_end"],
        subject=f"{group.name} — your week ahead",
        body_text=body_text,
        body_html=body_html,
        created_by="system:digest",
    )
    db.add(digest)
    db.flush()
    return digest


def deliver_digest(db: Session, *, group: Group, digest: Digest) -> int:
    """One digest notification per active member, through the
    outbox. Returns the count written."""
    member_rows = (
        db.query(Membership, User)
        .join(User, User.id == Membership.user_id)
        .filter(
            Membership.group_id == group.id,
            Membership.status == "active",
        )
        .all()
    )
    count = 0
    for _membership, user in member_rows:
        row = notify_service.create_notification(
            db,
            group=group,
            recipient_user=user,
            recipient_label=f"user:{user.id}",
            recipient_email=user.email,
            kind="digest",
            subject=digest.subject,
            body_text=digest.body_text,
            body_html=digest.body_html,
        )
        if row is not None:
            count += 1
    digest.sent_at = now_utc()
    db.flush()
    return count


def run_digest(
    db: Session, *, actor_label: str, group: Group, now: datetime | None = None
) -> Digest:
    """Compose + deliver + stamp, in one commit."""
    digest = compose_digest(db, group=group, now=now)
    count = deliver_digest(db, group=group, digest=digest)
    digest.created_by = actor_label
    audit.record(
        db, actor=actor_label, action="digest.sent",
        group_id=group.id, entity_type="digest", entity_id=digest.id,
        detail={"recipients": count},
    )
    db.commit()
    return digest


def next_run_at(group: Group, *, after: datetime | None = None) -> datetime:
    """The group's next digest moment: its configured weekday
    and time in its home timezone, strictly after ``after``."""
    after = after or now_utc()
    zone = ZoneInfo(group.home_timezone or "UTC")
    weekday = group.digest_weekday if group.digest_weekday is not None else DEFAULT_DIGEST_WEEKDAY
    hhmm = group.digest_time or DEFAULT_DIGEST_TIME
    try:
        hour, minute = (int(part) for part in hhmm.split(":"))
        at = time(hour, minute)
    except ValueError:
        at = time(8, 0)
    local_after = after.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    days_ahead = (weekday - local_after.weekday()) % 7
    candidate = datetime.combine(
        local_after.date() + timedelta(days=days_ahead), at, tzinfo=zone
    )
    if candidate <= local_after:
        candidate += timedelta(days=7)
    return candidate.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def ensure_scheduled(db: Session, *, group: Group) -> Job | None:
    """Anchor (or re-anchor) the group's digest chain: exactly
    one outstanding ``digest_run`` job per group. Called when
    digest settings are saved. A disabled digest anchors
    nothing (and any queued run it had is cancelled)."""
    outstanding = (
        db.query(Job)
        .filter(
            Job.kind == DIGEST_JOB_KIND,
            Job.status == "queued",
        )
        .all()
    )
    mine = [job for job in outstanding if _job_group_id(job) == group.id]
    for job in mine:
        job.status = "cancelled"
    db.flush()
    if not group.digest_enabled:
        return None
    return job_service.queue(
        db,
        kind=DIGEST_JOB_KIND,
        payload={"group_id": group.id},
        run_at=next_run_at(group),
    )


def _job_group_id(job: Job) -> str | None:
    """A digest job's group, from its payload (jobs name their
    group in the payload — the chassis' addressing rule)."""
    try:
        return job_service.payload_of(job).get("group_id")
    except (json.JSONDecodeError, AppError):
        return None


def handle_digest_run(db: Session, payload: dict) -> None:
    """The chain link: compose + deliver this group's digest,
    then queue next week's run — the chain perpetuates itself
    one link at a time, so a group's settings change only ever
    needs the anchor refreshed (ensure_scheduled)."""
    group = db.get(Group, payload.get("group_id") or "")
    if group is None or not group.digest_enabled:
        return
    run_digest(db, actor_label="system:digest", group=group)
    call_internal(
        db, caller="notifications", operation="jobs.enqueue",
        payload={
            "kind": DIGEST_JOB_KIND,
            "payload": {"group_id": group.id},
            "run_at": next_run_at(group).isoformat(),
        },
    )


def _settings() -> Settings:
    from gpa.config import get_settings

    return get_settings()


def register_handlers() -> None:
    job_service.register_handler(DIGEST_JOB_KIND, handle_digest_run)


register_handlers()
