"""The reconciliation job (roadmap B-137).

Every night (02:30 group-local), and on an admin's demand,
the group's outside copies are COMPARED against ours —
Facebook events and Google calendar entries — and drift is
reported to the group's organisers. Facebook-side edits and
deletions, and likewise Google-side ones, surface as drift.

The law of this job, written once, plainly: **reconciliation
never writes outward.** A drifted outside copy is a fact to
report, not a mess to tidy — the B-058/B-131 bright line
(sync happens only at an organiser's button press) applies
to machines on a schedule exactly as it applies to people.
The job's only writes are inward: one change notification to
each organiser naming the drifted events, and its own audit
record.

The chain follows the digest pattern (B-101): one
outstanding job per group, each run queueing its successor
for the next night, anchored when a group connects a
Facebook or Google integration (and by the manual trigger).
"""

import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.models.events import Event
from gpa.models.groups import Group, Membership
from gpa.models.identity import User, now_utc
from gpa.models.core import Job
from gpa.models.integrations import ExternalEventLink
from gpa.internal.client import call_internal
from gpa.services import audit
from gpa.errors import AppError
from gpa.services import jobs as job_service

RECONCILE_JOB_KIND = "reconcile"
RUN_AT = time(2, 30)  # nightly, group-local

# The link kinds this job compares. Samtal links are absent
# on purpose: B-124's change candidates already carry the
# village's edits home; reconciling there too would report
# every candidate twice.
_KINDS = ("facebook_events", "google_calendar")


def next_run_at(group: Group, *, after: datetime | None = None) -> datetime:
    """The next 02:30 in the group's home timezone, strictly
    after ``after`` (naive UTC, the house convention)."""
    after = after or now_utc()
    zone = ZoneInfo(group.home_timezone or "UTC")
    local_after = after.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    candidate = datetime.combine(local_after.date(), RUN_AT, tzinfo=zone)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return candidate.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _job_group_id(job: Job) -> str | None:
    try:
        return job_service.payload_of(job).get("group_id")
    except (json.JSONDecodeError, AppError):
        return None


def ensure_scheduled(db: Session, *, group: Group) -> Job | None:
    """Anchor the group's nightly chain: exactly one
    outstanding reconcile job per group (the digest rule)."""
    outstanding = (
        db.query(Job)
        .filter(Job.kind == RECONCILE_JOB_KIND, Job.status == "queued")
        .all()
    )
    mine = [job for job in outstanding if _job_group_id(job) == group.id]
    if mine:
        return mine[0]
    return job_service.queue(
        db, kind=RECONCILE_JOB_KIND,
        payload={"group_id": group.id}, run_at=next_run_at(group),
    )


def _outside_copy(db: Session, settings, link: ExternalEventLink) -> dict | None:
    """The outside system's current copy of one event, or
    None when it is gone. Read through the client boundary —
    in this environment, the recorded fixtures. Both doors
    are addressed (a Page id, a calendar id), and the
    address lives in the link's integration config."""
    from gpa.models.integrations import Integration
    from gpa.services import integrations as integration_service

    # A link names its group + kind, not an integration row;
    # the group's connection of that kind carries the config.
    integration = (
        db.query(Integration)
        .filter(
            Integration.group_id == link.group_id,
            Integration.kind == link.connector_kind,
        )
        .first()
    )
    config = (
        integration_service.read_config(settings, integration)
        if integration is not None
        else {}
    )
    if link.connector_kind == "facebook_events":
        from gpa.services import facebook_events

        return facebook_events.get_client().get_event(
            str(config.get("page_id", "")), link.external_id
        )
    from gpa.services import google_calendar

    return google_calendar.get_client().get_event(
        str(config.get("calendar_id", "")), link.external_id
    )


def _canonical(db: Session, kind: str, event: Event) -> dict:
    """Our copy of the event, in the outside system's own
    payload shape — the thing the outside copy is compared
    against (the same canonical payloads the publish/sync
    doors send)."""
    if kind == "facebook_events":
        from gpa.services import facebook_events

        return facebook_events.payload_for(db, event)
    from gpa.services import google_calendar

    return google_calendar.event_payload(event)


def _differs(db: Session, link: ExternalEventLink, event: Event, outside: dict) -> bool:
    """Has the outside copy drifted from ours? The comparison
    is each owning service's own projection (the outside
    system's envelope fields — its id, its URL — ride along
    in its records and are not drift)."""
    canonical = _canonical(db, link.connector_kind, event)
    if link.connector_kind == "facebook_events":
        from gpa.services import facebook_events

        return facebook_events.projected(outside, canonical) != canonical
    from gpa.services import google_calendar

    return google_calendar.projected(outside, canonical) != canonical


# Human names for the connectors, for the report's sentences.
_KIND_WORDS = {"facebook_events": "Facebook", "google_calendar": "Google Calendar"}


def reconcile_group(
    db: Session, settings=None, *, group: Group, actor: str = "system:reconcile"
) -> list[dict]:
    # Settings arrive from the caller (the manual trigger
    # passes the app's own; the nightly job takes the process
    # settings — the deployment's env, which is what sealed
    # the configs there). Unsealing with the WRONG settings
    # reads every config as empty, so this is threaded, never
    # assumed.
    if settings is None:
        from gpa.config import get_settings

        settings = get_settings()
    """Compare every outside copy once; report drift to the
    organisers; return the drift list.

    A drift entry: {event_id, title, connector, kind:
    "differs" | "missing", external_id}. Neither kind is
    acted on — see the module law. An integration that is not
    connected (unavailable client, incomplete config) yields
    no drift entries: an unconnected door has no copies to
    compare, and the health board already carries that fact.
    """
    drift: list[dict] = []
    links = (
        db.query(ExternalEventLink)
        .filter(
            ExternalEventLink.group_id == group.id,
            ExternalEventLink.connector_kind.in_(_KINDS),
        )
        .all()
    )
    for link in links:
        event = db.get(Event, link.event_id)
        if event is None:
            continue
        try:
            outside = _outside_copy(db, settings, link)
        except Exception:
            # A door that cannot be read tonight (unavailable
            # client, refused credential) is a health-board
            # fact, not drift — never invent a difference.
            continue
        if outside is None:
            drift.append(
                {
                    "event_id": event.id, "title": event.title,
                    "connector": link.connector_kind, "kind": "missing",
                    "external_id": link.external_id,
                }
            )
        elif _differs(db, link, event, outside):
            drift.append(
                {
                    "event_id": event.id, "title": event.title,
                    "connector": link.connector_kind, "kind": "differs",
                    "external_id": link.external_id,
                }
            )
    if drift:
        sentences = []
        for entry in drift:
            where = _KIND_WORDS[entry["connector"]]
            if entry["kind"] == "missing":
                sentences.append(
                    f"'{entry['title']}' is missing from {where} — its "
                    "copy there was deleted or never arrived."
                )
            else:
                sentences.append(
                    f"'{entry['title']}' differs on {where} — the copy "
                    "there no longer matches ours."
                )
        body = (
            "The nightly reconciliation found differences between "
            "this group's events and their outside copies:\n\n"
            + "\n".join(f"- {s}" for s in sentences)
            + "\n\nNothing was changed on either side — this report "
            "never repairs. A copy that is MISSING comes back the "
            "next time an organiser presses publish/sync. A copy "
            "that DIFFERS stands as the outside system has it until "
            "a person settles it: bring the edit home by hand, or "
            "remove the outside copy and press again."
        )
        organisers = (
            db.query(Membership)
            .filter(
                Membership.group_id == group.id,
                Membership.status == "active",
                Membership.role.in_(("owner", "admin", "organiser")),
            )
            .all()
        )
        for membership in organisers:
            user = db.get(User, membership.user_id)
            if user is None:
                continue
            call_internal(
                db, caller="integrations", operation="notify.request",
                payload={
                    "group_id": group.id,
                    "audience": {"user_id": user.id, "label": f"user:{user.id}"},
                    "kind": "change.drift",
                    "subject": "Outside copies have drifted",
                    "body_text": body,
                },
            )
    audit.record(
        db, actor=actor, action="integration.reconciled",
        group_id=group.id, entity_type="group", entity_id=group.id,
        detail={"drift_count": len(drift), "drift": drift},
    )
    db.commit()
    return drift


def handle_reconcile(db: Session, payload: dict) -> None:
    """One chain link: reconcile, then queue tomorrow night's
    run — the chain perpetuates itself (the digest pattern),
    so the anchor only ever needs placing once."""
    group = db.get(Group, payload.get("group_id") or "")
    if group is None:
        return
    reconcile_group(db, group=group)
    job_service.queue(
        db, kind=RECONCILE_JOB_KIND,
        payload={"group_id": group.id}, run_at=next_run_at(group),
    )


def register_handlers() -> None:
    job_service.register_handler(RECONCILE_JOB_KIND, handle_reconcile)


register_handlers()
