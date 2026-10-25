"""The reminder engine (roadmap B-096) + AI wording (B-103).

Reminders are MATERIALISED, not computed on the fly: when an
event is published — and again whenever it moves, is edited, or
gains/loses an answer — one notification row per rule per
RSVP'd person is written through the outbox
(services/notifications.py), each backed by its own send job
timed for its moment. Rescheduling supersedes the old rows and
deletes their queued send jobs IN THE SAME TRANSACTION as the
change itself (the publish/update/RSVP service is
mid-transaction when it calls in here): no orphan reminder for
the old time can survive, because the old rows are marked
``superseded`` and their jobs are gone from the queue before
the commit lands.

The words a reminder carries come from the event's own facts:
the place, the bring-list, and what changed since last time
(B-050's EventChange rows) — so a reminder for a moved event
says it moved.

B-103 — AI WORDING (optional tier): where the group enables
``ai_reminder_wording``, the model may draft friendlier words
AROUND the facts. The facts are template-guaranteed: the AI
draft ships ONLY if every template fact (the formatted start,
the place, each bring item) survives verbatim in it; otherwise
the template ships and the row records
``wording_source="template_fallback"``. Either way the stored
row keeps BOTH versions (``template_body`` + ``body_text``) —
provenance, not trust.
"""

import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.ai import promptlib
from gpa.ai.client import ModelError, get_client
from gpa.models.events import Event, EventChange
from gpa.models.groups import Group
from gpa.models.identity import User, now_utc
from gpa.models.core import Job
from gpa.models.notifications import Notification
from gpa.services import notifications as notify_service

# The roadmap's default rules (B-096): a week out, a day out,
# and the morning of — all delivered at a civilised hour in the
# event's own timezone.
DEFAULT_RULES: tuple[dict, ...] = (
    {"kind": "days_before", "days": 7},
    {"kind": "days_before", "days": 1},
    {"kind": "morning_of"},
)
_DAYS_BEFORE_HOUR = 9  # 09:00 event-local for days-before rules
_MORNING_OF_HOUR = 8  # 08:00 event-local on the day itself

# Field names in a reminder's "what changed" lines — the same
# words the announcement drafts use (services/announce.py).
_CHANGE_WORDS = {
    "title": "Title", "starts_at": "Start", "ends_at": "End",
    "place_name": "Place", "place_address": "Address",
    "host_label": "Host", "cost_text": "Cost", "status": "Status",
}


def rules_for(group: Group) -> list[dict]:
    """The group's reminder rules: its stored JSON, validated,
    or the roadmap defaults when none (or nonsense) is stored."""
    if group.reminder_rules_json:
        try:
            parsed = json.loads(group.reminder_rules_json)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            rules = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind")
                if kind == "morning_of":
                    rules.append({"kind": "morning_of"})
                elif kind == "days_before" and isinstance(item.get("days"), int) \
                        and 0 < item["days"] <= 365:
                    rules.append({"kind": "days_before", "days": item["days"]})
            if rules:
                return rules
    return [dict(rule) for rule in DEFAULT_RULES]


def rule_label(rule: dict) -> str:
    """The stored marker identifying a reminder's rule."""
    if rule["kind"] == "morning_of":
        return "morning_of"
    return f"days_before:{rule['days']}"


def _reminder_moment(event: Event, rule: dict) -> datetime:
    """A rule's send moment, in UTC terms: days-before fires at
    09:00 on (start date − days) in the event's timezone;
    morning-of at 08:00 on the start date itself."""
    zone = ZoneInfo(event.timezone or "UTC")
    local_start = event.starts_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    if rule["kind"] == "morning_of":
        day = local_start.date()
        hour = _MORNING_OF_HOUR
    else:
        day = local_start.date() - timedelta(days=rule["days"])
        hour = _DAYS_BEFORE_HOUR
    local = datetime.combine(day, time(hour, 0), tzinfo=zone)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _format_start(event: Event) -> str:
    """The event's start as a person reads it, in its timezone."""
    zone = ZoneInfo(event.timezone or "UTC")
    local = event.starts_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    return local.strftime("%A %d %B %Y, %H:%M") + f" ({event.timezone})"


def _place(event: Event) -> str:
    """The place as one line: name and address, whichever the
    event carries."""
    return ", ".join(part for part in (event.place_name, event.place_address) if part)


def _bring_items(event: Event) -> list[str]:
    """The bring-list as items: the event's bring_list text is
    a person's own words ("a dish to share", or one item per
    line) — lines are the items, so fact-preservation can check
    each one."""
    return [line.strip() for line in (event.bring_list or "").splitlines() if line.strip()]


def _changes_since(db: Session, event: Event, *, since_days: int = 30) -> list[str]:
    """What changed, in words: the event's recent change rows
    (B-050), oldest first, as "Field: old → new" lines."""
    cutoff = now_utc() - timedelta(days=since_days)
    rows = (
        db.query(EventChange)
        .filter(EventChange.event_id == event.id, EventChange.created_at >= cutoff)
        .order_by(EventChange.created_at, EventChange.id)
        .all()
    )
    return [
        f"{_CHANGE_WORDS.get(row.field, row.field)}: {row.old_value} → {row.new_value}"
        for row in rows
    ]


def facts_for(db: Session, event: Event) -> dict:
    """The reminder's fact set — the ground truth every wording
    (template or AI) must preserve exactly."""
    return {
        "title": event.title,
        "start": _format_start(event),
        "place": _place(event),
        "bring": _bring_items(event),
        "changes": _changes_since(db, event),
    }


def template_body(facts: dict) -> str:
    """The template's words: complete and correct by
    construction — every fact present, nothing invented."""
    lines = [f"A reminder about '{facts['title']}'.", f"When: {facts['start']}"]
    if facts["place"]:
        lines.append(f"Where: {facts['place']}")
    if facts["bring"]:
        lines.append("Bring: " + "; ".join(facts["bring"]))
    if facts["changes"]:
        lines.append("What changed: " + "; ".join(facts["changes"]))
    return "\n".join(lines)


def facts_preserved(facts: dict, text: str) -> bool:
    """B-103's gate: EVERY template fact must survive verbatim
    in the AI draft — the formatted start, the place, and each
    bring item as its own substring. A draft that paraphrases a
    fact has changed a fact, and does not ship."""
    if facts["start"] not in text:
        return False
    if facts["place"] and facts["place"] not in text:
        return False
    return all(item in text for item in facts["bring"])


def _ai_wording(facts: dict, template: str) -> tuple[str, str]:
    """Ask the model for friendlier words; return
    (body, wording_source). The template ships on ANY doubt —
    a model error, a malformed answer, a lost fact — because a
    reminder that is merely plain always beats one that is
    wrong. The prompt (reminder_wording v1) takes the event's
    facts as one block: the template's own words."""
    try:
        prompt_template = promptlib.load_prompt("reminder_wording")
        prompt = prompt_template.render(EVENT=template)
        result = get_client().complete_json(
            prompt=prompt, schema=prompt_template.schema or {}, tier="spark"
        )
        body = str((result.data or {}).get("body") or "")
    except ModelError:
        return template, "template_fallback"
    if body and facts_preserved(facts, body):
        return body, "ai"
    return template, "template_fallback"


def _host_user(db: Session, event: Event) -> User | None:
    """The event's host as a User, when the creator was one:
    audit labels name people "user:<id>" (D-35), and the host
    hears their own reminders like anyone RSVP'd."""
    if event.created_by.startswith("user:"):
        return db.get(User, event.created_by.removeprefix("user:"))
    return None


def schedule_reminders(db: Session, *, group: Group, event: Event) -> list[Notification]:
    """(Re)materialise an event's reminders — B-096's core.

    Old reminder rows still outstanding are superseded and
    their queued send jobs DELETED (the move-an-event test's
    "no orphan" promise); rows already sent stand as history.
    New rows are written for every rule × recipient whose moment
    is still ahead — a moment already past is skipped, not sent
    late. Called from publish/update/RSVP/cancel inside their
    transactions; the caller commits.
    """
    now = now_utc()
    outstanding = (
        db.query(Notification)
        .filter(
            Notification.event_id == event.id,
            Notification.kind == "reminder",
            Notification.status.in_(("queued", "held", "failed")),
        )
        .all()
    )
    for row in outstanding:
        if row.job_id:
            job = db.get(Job, row.job_id)
            if job is not None and job.status == "queued":
                db.delete(job)
        row.status = "superseded"
        row.job_id = None
    db.flush()

    if event.status != "published" or event.deleted_at is not None:
        return []

    facts = facts_for(db, event)
    template = template_body(facts)
    body, source = template, "template"
    if group.ai_reminder_wording:
        body, source = _ai_wording(facts, template)
    subject = f"Reminder: {event.title}"

    recipients = notify_service.event_recipients(db, event.id)
    host = _host_user(db, event)
    if host is not None and all(
        r["user"] is None or r["user"].id != host.id for r in recipients
    ):
        recipients.append(
            {"user": host, "label": f"user:{host.id}", "email": host.email, "rsvp": None}
        )

    created: list[Notification] = []
    for rule in rules_for(group):
        moment = _reminder_moment(event, rule)
        if moment <= now:
            continue  # Past moments are skipped, never sent late.
        for recipient in recipients:
            row = notify_service.create_notification(
                db,
                group=group,
                recipient_user=recipient["user"],
                recipient_label=recipient["label"],
                recipient_email=recipient["email"],
                kind="reminder",
                subject=subject,
                body_text=body,
                event_id=event.id,
                send_after=moment,
                reminder_rule=rule_label(rule),
                template_body=template,
                wording_source=source,
                now=now,
            )
            if row is not None:
                created.append(row)
    return created
