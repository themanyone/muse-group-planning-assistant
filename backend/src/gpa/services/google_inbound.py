"""Google Calendar inbound edits (roadmap B-159).

B-132's decided scope was one-way OUT: events flow to the
group's Google calendar as copies, and a Google-side edit was
never read back — it stood, for B-137's reconciliation to
notice and report. Phase 16 revisits that door and widens it
exactly one notch, the B-124 notch:

- **Inbound edits arrive as change candidates.** A scheduled
  pull (or an organiser's press of the pull door) reads the
  outside copies through the B-132 client boundary and
  compares each against TWO anchors: the event's current
  canonical payload, and the payload hash the link row
  remembers from the last push. The three-way comparison is
  what tells an outside edit from our own not-yet-pushed one:

  - outside == current payload → the copies agree; nothing.
  - outside == last-pushed payload, current != last-pushed →
    OUR side moved and the copy is merely stale; the outbound
    sync owns that, and drafting a "change" from it would
    propose reverting our own edit. Nothing.
  - outside differs from both → a person edited the copy on
    Google. That edit is drafted as a B-077 change candidate
    (authored ``system:google-calendar``, backed by a source
    whose words state the edit), and the EVENT DOES NOT MOVE.
    It waits in the review queue, visibly, until a person
    approves or rejects it there — two-way sync without ever
    bypassing the queue, which was the whole point.

- **Reconciliation still only reports.** B-137 notices drift
  (including a copy deleted outside, which this pull skips —
  a deletion is not an edit to draft from); this pull is the
  one that drafts. The two never write outward and never
  write to an event.

Field mapping follows B-124's applicability: starts, ends,
place, and cancellation are B-077 fields; a retitle is
recorded in the proposal's words (the queue's edit door can
apply it) but is not itself an applicable field. The dedupe
rule is B-124's too: the same proposal, still undecided, is
never drafted twice — a pull is safe to re-run, and once a
person settles a proposal, a still-standing outside edit may
be drafted again (the person has spoken; the drift remains
a fact until the copies agree).
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.models.events import Event
from gpa.models.groups import Candidate, Group
from gpa.models.integrations import ExternalEventLink
from gpa.services import audit
from gpa.services import google_calendar as google_service
from gpa.services import sources as source_service

# Who the return path's candidates and sources are authored
# by — a system label, never a person's (the B-045 rule).
RETURN_AUTHOR = "system:google-calendar"


def pull_edits(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    integration_id: str,
) -> dict:
    """Draft change candidates for one connection's Google-
    side edits. Returns ``{"candidates_created": n,
    "checked": m}`` — m counts the linked copies compared.

    One transaction: backing sources and their candidates
    land together or not at all (commit before the answer).
    """
    row = google_service._integration(db, group, integration_id)
    config = google_service.integration_config(settings, row)
    client = google_service._client_or_refusal()
    calendar_id = str(config["calendar_id"])

    links = (
        db.query(ExternalEventLink)
        .filter(
            ExternalEventLink.group_id == group.id,
            ExternalEventLink.connector_kind == google_service.CONNECTOR_KIND,
        )
        .all()
    )
    created = 0
    for link in links:
        event = db.get(Event, link.event_id)
        if event is None or event.deleted_at is not None or event.status == "draft":
            continue  # Drafts never travelled; nothing to return.
        outside = client.get_event(calendar_id, link.external_id)
        if outside is None:
            continue  # A missing copy is drift for B-137 to report.
        candidate = _draft_change(
            db, group=group, event=event, link=link, outside=outside
        )
        if candidate is not None:
            created += 1
    audit.record(
        db, actor=actor_label, action="google.pulled",
        group_id=group.id, entity_type="integration", entity_id=row.id,
        detail={"candidates_created": created, "checked": len(links)},
    )
    db.commit()
    return {"candidates_created": created, "checked": len(links)}


def _draft_change(
    db: Session,
    *,
    group: Group,
    event: Event,
    link: ExternalEventLink,
    outside: dict,
) -> Candidate | None:
    """Compare one outside copy against the event it mirrors
    (three-way, per the module docstring); when the outside
    side genuinely edited, build the change candidate — or
    None when the copies agree, when only our side moved, or
    when this exact proposal already waits undecided."""
    current_payload = google_service.event_payload(event)
    projected = google_service.projected(outside, current_payload)
    if projected == current_payload:
        return None  # The copies agree.
    if google_service.payload_hash(projected) == link.payload_hash:
        return None  # The copy still says what we last pushed;
        # our event moved since — the outbound sync owns it.

    tz = ZoneInfo(event.timezone or group.home_timezone or "UTC")
    fields: dict = {}
    sentences: list[str] = []

    def _outside_instant(key: str) -> datetime | None:
        raw = outside.get(key)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    for key, field, label in (
        ("start", "starts", "starts"),
        ("end", "ends", "ends"),
    ):
        outside_instant = _outside_instant(key)
        current = event.starts_at if field == "starts" else event.ends_at
        if outside_instant is not None and outside_instant != current:
            local = outside_instant.replace(tzinfo=timezone.utc).astimezone(tz)
            sentence = (
                f"On Google Calendar, '{event.title}' now {label} at "
                f"{local.strftime('%Y-%m-%d %H:%M')} ({tz.key})."
            )
            fields[field] = {
                "value": local.isoformat(),
                "quote": sentence,
                "supported": True,
                "resolved_date": local.date().isoformat(),
                "resolved_time": local.time().isoformat(),
            }
            sentences.append(sentence)

    outside_place = str(outside.get("location") or "")
    if outside_place != (event.place_name or ""):
        sentence = (
            f"On Google Calendar, '{event.title}' now meets at "
            f"{outside_place or 'no stated place'}."
        )
        fields["place"] = {
            "value": outside_place, "quote": sentence, "supported": True,
        }
        sentences.append(sentence)

    outside_title = str(outside.get("summary") or "")
    if outside_title and outside_title != event.title:
        # A retitle is not one of B-077's applicable fields —
        # it is recorded in the proposal's words so a person
        # sees it, and the queue's own edit door can apply it.
        sentences.append(
            f"On Google Calendar, the event is titled '{outside_title}'."
        )

    if outside.get("status") == "cancelled" and event.status != "cancelled":
        sentence = f"On Google Calendar, '{event.title}' was cancelled."
        fields["cancelled"] = {
            "value": True, "quote": sentence, "supported": True,
        }
        sentences.append(sentence)

    if not fields:
        return None  # The copies differ only in words (a
        # retitle alone drafts nothing applicable — it stays
        # visible as drift for B-137 to report).

    # The dedupe rule (B-124's): the same proposal, still
    # undecided, is not drafted twice.
    fingerprint = json.dumps(fields, sort_keys=True)
    pending = (
        db.query(Candidate)
        .filter(
            Candidate.group_id == group.id,
            Candidate.kind == "event_change",
            Candidate.created_by == RETURN_AUTHOR,
            Candidate.status.in_(("pending", "edited")),
        )
        .all()
    )
    for other in pending:
        try:
            other_draft = json.loads(other.draft_json or "{}")
        except json.JSONDecodeError:
            continue
        if other_draft.get("target_event_id") != event.id:
            continue
        if json.dumps(other_draft.get("fields", {}), sort_keys=True) == fingerprint:
            return None

    words = " ".join(sentences)
    backing = source_service.create_source(
        db, actor_label=RETURN_AUTHOR, group=group, kind="manual",
        title=f"Google Calendar edit: {event.title}"[:240],
        granted_scope="calendar return path (B-159)",
        chunks=[{"text": words, "sender": "Google Calendar"}],
    )
    # create_source committed; the candidate rides its own
    # commit at the end of the pull — re-fetch the chunk in
    # this session for the provenance row (the B-124 pattern).
    chunk = source_service.list_chunks(db, backing)[0]
    draft = {
        "title": f"Change to {event.title}",
        "target_event_id": event.id,
        "target_title": event.title,
        "fields": fields,
    }
    candidate = Candidate(
        group_id=group.id,
        kind="event_change",
        title=f"Change to {event.title}"[:240],
        created_by=RETURN_AUTHOR,
        status="pending",
        source_id=backing.id,
        draft_json=json.dumps(draft),
        flags_json="[]",
    )
    db.add(candidate)
    db.flush()
    for field_name in fields:
        source_service.add_provenance(
            db, candidate_id=candidate.id, chunk=chunk,
            field=field_name, quote=fields[field_name]["quote"],
        )
    db.flush()
    return candidate
