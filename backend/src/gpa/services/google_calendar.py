"""Google Calendar sync, one-way out (roadmap B-132).

The group's published events also live on a Google calendar
the group chooses — as COPIES, under the same law as every
outward surface in this project:

- **One-way is the decided scope.** Events flow out and
  cancellations follow them; inbound Google edits are NOT
  read back in the hackathon build, because an edit made on
  Google has not passed the review queue, and the approval
  law has no side doors. If a copy is changed over there,
  this sync leaves it changed (the reconciliation job,
  B-137, is the thing that NOTICES and tells the organisers
  — it never quietly fixes it either). The handoff states
  this scope in so many words so nobody promises two-way.
  **Phase 16 (B-159) revisited the inbound half** — not the
  law: services/google_inbound.py now PULLS those edits in
  as B-077 change candidates, where they wait for a human.
  Nothing reads an edit back INTO an event, here or there;
  the queue remains the only door, in both directions.
- **Connect is the credential act.** A group's
  ``google_calendar`` integration carries, sealed, the
  calendar id and the access token a deployment's OAuth
  consent flow handed over. ``connect`` below verifies
  those credentials against the client boundary and records
  the verdict on the integration row — the connection's
  birth certificate. In this environment no Google account
  is connected and no real token exists: the recorded-
  fixture double proves the shape, and the process client
  is the unavailable one, refusing in plain words.
- **Sync is a person's press**, like B-131's: a whole-group
  run that upserts what changed and propagates
  cancellations as cancellations. Payload hashes on the
  link rows (the B-124 pattern) make a re-run quiet.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.integrations import ExternalEventLink, Integration
from gpa.services import audit
from gpa.services import integrations as integration_service

CONNECTOR_KIND = "google_calendar"


class GoogleUnavailable(Exception):
    """No live Google connection exists in this deployment.

    Raised by the unavailable client; the sync and connect
    doors turn it into a plain-words refusal.
    """


class GoogleCalendarClient(Protocol):
    """What the assistant may ask Google Calendar.

    Payloads carry the assistant's own event id as ``ref``
    plus Google-shaped fields (``summary``, RFC-3339
    ``start``/``end``, ``status``). One-way: there is a read
    here ONLY so a sync can address an existing copy and the
    reconciliation job can compare — never to import edits.
    """

    def ping(self) -> bool:
        """True when the connection answers."""
        ...

    def upsert_event(self, calendar_id: str, payload: dict) -> str:
        """Create or replace one event copy; returns the
        calendar-side id. Keyed on the payload's ``ref``, so
        repeat syncs update the same copy."""
        ...

    def cancel_event(self, calendar_id: str, external_id: str) -> None:
        """Mark one copy cancelled (it stays, readable as
        cancelled — a cancellation propagates as one)."""
        ...

    def get_event(self, calendar_id: str, external_id: str) -> dict | None:
        """Read one copy back, or None when it is gone."""
        ...

    def list_events(self, calendar_id: str) -> list[dict]:
        """Every copy currently on the calendar."""
        ...


class UnavailableGoogleClient:
    """The default process client: no account is connected.

    Every call refuses in plain words — the honest stand-in
    for a connection that does not exist, exactly as the
    other boundaries in this codebase behave.
    """

    MESSAGE = (
        "No Google account is connected to this deployment — "
        "calendar sync runs against recorded fixtures until "
        "a deployment connects an account."
    )

    def ping(self) -> bool:
        raise GoogleUnavailable(self.MESSAGE)

    def upsert_event(self, calendar_id: str, payload: dict) -> str:
        raise GoogleUnavailable(self.MESSAGE)

    def cancel_event(self, calendar_id: str, external_id: str) -> None:
        raise GoogleUnavailable(self.MESSAGE)

    def get_event(self, calendar_id: str, external_id: str) -> dict | None:
        raise GoogleUnavailable(self.MESSAGE)

    def list_events(self, calendar_id: str) -> list[dict]:
        raise GoogleUnavailable(self.MESSAGE)


class FixtureGoogleClient:
    """The recorded-fixture double (tests + offline demos).

    Upserts key on the payload's ``ref``; ids are sequential
    per client; a cancellation marks the copy's status and
    keeps it standing. Tests reach behind it —
    :meth:`edit_event`, :meth:`remove_event` — to plant the
    outside drift B-137 exists to notice. All data invented.
    """

    def __init__(self) -> None:
        self._calendars: dict[str, dict[str, dict]] = {}
        self._next = 0

    def ping(self) -> bool:
        return True

    def _calendar(self, calendar_id: str) -> dict[str, dict]:
        return self._calendars.setdefault(calendar_id, {})

    def upsert_event(self, calendar_id: str, payload: dict) -> str:
        calendar = self._calendar(calendar_id)
        for external_id, stored in calendar.items():
            if stored.get("ref") == payload.get("ref"):
                stored.update(payload)
                return external_id
        self._next += 1
        external_id = f"gcal-event-{self._next}"
        calendar[external_id] = {"id": external_id, **payload}
        return external_id

    def cancel_event(self, calendar_id: str, external_id: str) -> None:
        stored = self._calendar(calendar_id).get(external_id)
        if stored is not None:
            stored["status"] = "cancelled"

    def get_event(self, calendar_id: str, external_id: str) -> dict | None:
        stored = self._calendar(calendar_id).get(external_id)
        return dict(stored) if stored is not None else None

    def list_events(self, calendar_id: str) -> list[dict]:
        return [dict(row) for row in self._calendar(calendar_id).values()]

    # -- Behind the glass: how tests plant outside drift. --

    def edit_event(self, calendar_id: str, external_id: str, **fields) -> None:
        """Change a copy directly, as an edit on Google
        would — the assistant is not told (one-way scope)."""
        self._calendar(calendar_id)[external_id].update(fields)

    def remove_event(self, calendar_id: str, external_id: str) -> None:
        """Delete a copy directly, over on Google's side."""
        self._calendar(calendar_id).pop(external_id, None)


_client: GoogleCalendarClient = UnavailableGoogleClient()


def set_client(client: GoogleCalendarClient | None) -> None:
    global _client
    _client = client if client is not None else UnavailableGoogleClient()


def get_client() -> GoogleCalendarClient:
    return _client


# ---------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------


def _iso(instant: datetime) -> str:
    """A stored naive-UTC instant in RFC-3339 shape."""
    return instant.replace(tzinfo=timezone.utc).isoformat()


def event_payload(event: Event) -> dict:
    """The canonical Google payload for one event — the
    exact dict whose hash the link row remembers."""
    return {
        "ref": event.id,
        "summary": event.title,
        "start": _iso(event.starts_at),
        "end": _iso(event.ends_at),
        "timeZone": event.timezone,
        "description": event.description or "",
        "location": event.place_name or "",
        "status": "cancelled" if event.status == "cancelled" else "confirmed",
    }


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def projected(outside: dict, payload: dict) -> dict:
    """An outside copy projected onto the payload's keys —
    the comparison B-137's reconciliation and the sync's
    repair both use: the outside system's envelope fields
    ride along in its records and are not part of the
    event's facts."""
    return {key: outside.get(key) for key in payload}


# ---------------------------------------------------------------------
# Connect + sync
# ---------------------------------------------------------------------


def _integration(db: Session, group: Group, integration_id: str) -> Integration:
    row = integration_service.get_integration(db, group, integration_id)
    if row.kind != CONNECTOR_KIND:
        raise AppError(
            "validation_error",
            detail="That integration is not a Google Calendar connection.",
        )
    return row


def integration_config(settings: Settings, row: Integration) -> dict:
    """The connection's config, plainly gated: a calendar
    id and an access token must both be present (sealed in
    the B-127 envelope; presence is all this door checks —
    the client boundary is where a live client reads them)."""
    config = integration_service.read_config(settings, row)
    missing = [k for k in ("calendar_id", "access_token") if not config.get(k)]
    if missing:
        raise AppError(
            "validation_error",
            detail=(
                "The Google connection is incomplete — its "
                f"config is missing: {', '.join(missing)}. Add "
                "them under Integrations, then connect again."
            ),
        )
    return config


def _client_or_refusal() -> GoogleCalendarClient:
    try:
        client = get_client()
        client.ping()
    except GoogleUnavailable as err:
        raise AppError("validation_error", detail=str(err)) from err
    return client


def connect(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    integration_id: str,
) -> Integration:
    """Verify the connection's credentials against the
    client boundary and record the verdict on the row.

    In a deployment, the sealed token is what Google's OAuth
    consent flow issued for the group's chosen calendar;
    connecting is the act that PROVES it answers before any
    sync trusts it. A failed ping is recorded as the row's
    health (error + the boundary's own words), and the door
    answers with that record — not a stack trace."""
    row = _integration(db, group, integration_id)
    config = integration_config(settings, row)
    client = get_client()

    row.last_checked_at = now_utc()
    try:
        client.ping()
    except GoogleUnavailable as err:
        row.status = "error"
        row.last_error = str(err)
        db.commit()
        return row
    except Exception as err:  # A broken client is a health fact too.
        row.status = "error"
        row.last_error = f"The Google connection failed its check: {err}"
        db.commit()
        return row
    row.status = "ok"
    row.last_error = ""
    row.last_success_at = now_utc()
    audit.record(
        db, actor=actor_label, action="integration.connected",
        group_id=group.id, entity_type="integration", entity_id=row.id,
        detail={"kind": CONNECTOR_KIND, "calendar_id": str(config["calendar_id"])},
    )
    db.commit()
    return row


def sync_group(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    integration_id: str,
) -> dict:
    """One sync run for the group's Google calendar.

    Published events upsert; cancellations propagate as
    cancellations; drafts never travel (the bright line);
    a copy whose hash already agrees is left alone. Returns
    the counts {pushed, updated, cancelled, unchanged}. One
    transaction — the run's copies and link rows land
    together or not at all."""
    row = _integration(db, group, integration_id)
    config = integration_config(settings, row)
    client = _client_or_refusal()
    calendar_id = str(config["calendar_id"])

    counts = {"pushed": 0, "updated": 0, "cancelled": 0, "unchanged": 0}
    events = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.status.in_(("published", "cancelled")),
            Event.deleted_at.is_(None),
        )
        .all()
    )
    for event in events:
        payload = event_payload(event)
        digest = payload_hash(payload)
        link = (
            db.query(ExternalEventLink)
            .filter(
                ExternalEventLink.event_id == event.id,
                ExternalEventLink.connector_kind == CONNECTOR_KIND,
            )
            .one_or_none()
        )
        if link is not None and link.payload_hash == digest:
            # Our side has not moved. One calendar-side case
            # still acts: a copy that was DELETED over there
            # is restored by falling through to the upsert
            # below — a sync that finds its copy gone puts it
            # back. A copy that was EDITED over there STANDS:
            # never silently overwritten (this slice's one-way
            # law, pinned by test); B-137's reconciliation
            # reports it for a person to settle.
            outside = client.get_event(calendar_id, link.external_id)
            if outside is not None:
                counts["unchanged"] += 1
                continue
        if event.status == "cancelled":
            if link is None:
                counts["unchanged"] += 1  # Never pushed; nothing to cancel.
                continue
            client.cancel_event(calendar_id, link.external_id)
            link.payload_hash = digest
            counts["cancelled"] += 1
            continue
        external_id = client.upsert_event(calendar_id, payload)
        if link is None:
            link = ExternalEventLink(
                group_id=group.id, event_id=event.id,
                connector_kind=CONNECTOR_KIND, external_id=external_id,
            )
            db.add(link)
            counts["pushed"] += 1
        else:
            counts["updated"] += 1
        link.external_id = external_id
        link.payload_hash = digest
    audit.record(
        db, actor=actor_label, action="google.synced",
        group_id=group.id, entity_type="integration", entity_id=row.id,
        detail=dict(counts),
    )
    db.commit()
    return counts
