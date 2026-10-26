"""The merge service and contradiction keeping (roadmap
B-076, B-079; Doc 07 §6).

B-075's matcher proposes; this service is where a person
disposes. Merging folds one candidate into an EXISTING event,
field by field, under one rule: **the better-sourced value
wins**.

- A field the event lacks is filled — when the candidate's
  value is supported by its source. An unsupported claim fills
  nothing and overwrites nothing: it is reported in the merge
  outcome as skipped, never silently believed.
- A field both sides state, and agree on, stands — agreement
  itself is recorded by linking the candidate's source.
- A fuller statement of the same value refines it ("hall"
  becomes "Village Hall" when the fuller form is the one a
  source supports).
- A genuine disagreement is settled by support: the value
  backed by more distinct sources wins. A tie keeps the
  event's value — the first truth stands — and raises a
  **contradiction** (B-079): the disagreement stays visible
  on the event, with every value and its sources, until a
  person resolves it. Nothing is ever averaged away, and a
  merge never erases: the candidate keeps its draft, every
  provenance row becomes an EventSourceLink, and the fields
  that moved write EventChange rows through the one edit
  engine (services/events.apply_changes), so a merge that
  moves an event sets needs_reannounce exactly like an edit.
"""

import json
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event, EventContradiction, EventSourceLink
from gpa.models.groups import Candidate, Group
from gpa.models.identity import now_utc
from gpa.models.sources import Provenance, SourceChunk
from gpa.services import audit
from gpa.services import candidates as candidate_service
from gpa.services import sources as source_service
from gpa.services import events as event_service
from gpa.services.matching import normalised

# Candidate draft field → the event column it feeds, for the
# text fields a merge can move.
TEXT_FIELD_MAP: dict[str, str] = {
    "title": "title",
    "place": "place_name",
    "host": "host_label",
    "cost": "cost_text",
    "bring": "bring_list",
    "description": "description",
}

# Event column → the candidate/link field name that supports
# it (EventSourceLink.field carries candidate field names —
# the vocabulary minting wrote, B-049).
LINK_FIELD: dict[str, str] = {
    "title": "title",
    "starts_at": "starts",
    "ends_at": "ends",
    "place_name": "place",
    "host_label": "host",
    "cost_text": "cost",
    "bring_list": "bring",
    "description": "description",
}


# ------------------------------------------------------------------
# Support: how many distinct sources stand behind a value
# ------------------------------------------------------------------


def sources_for_field(db: Session, event: Event, event_field: str) -> list[str]:
    """The distinct sources whose link rows support one event
    field. A link row exists only for a field a pipeline (or a
    person) actually sourced — unsupported fields earn no rows
    (B-040) — so a row's presence IS the support."""
    link_name = LINK_FIELD.get(event_field, event_field)
    rows = (
        db.query(EventSourceLink)
        .filter(
            EventSourceLink.event_id == event.id,
            EventSourceLink.field == link_name,
        )
        .all()
    )
    return sorted({row.source_id for row in rows})


def _value_support(db: Session, event: Event, event_field: str) -> int:
    """The CURRENT value's support strength: the distinct
    sources linked for the field that have never been recorded
    backing a different value for it. A source whose dissent
    lost a merge is linked (its evidence is preserved) but it
    does not back the value it argued against — the
    contradiction rows are the record of who said what, so
    they arbitrate the count."""
    current_text = event_service._stringify(getattr(event, event_field))
    supporters = set(sources_for_field(db, event, event_field))
    dissenters: set[str] = set()
    rows = (
        db.query(EventContradiction)
        .filter(
            EventContradiction.event_id == event.id,
            EventContradiction.field == event_field,
        )
        .all()
    )
    for row in rows:
        for entry in json.loads(row.values_json or "[]"):
            if entry.get("value") != current_text:
                dissenters.update(entry.get("source_ids", []))
    return len(supporters - dissenters)


def link_candidate_sources(db: Session, event: Event, candidate: Candidate) -> list[str]:
    """Carry a candidate's evidence onto an event: one
    EventSourceLink per provenance row (field + quote), plus
    the candidate's own source link when provenance did not
    cover it — the same shape minting writes (B-049), so a
    merged event answers "where did this come from?" exactly
    like a minted one. Returns the source ids now linked."""
    # Quotes are sealed at rest (B-167), and a seal is
    # nonce-random — dedup must compare OPENED quotes, or
    # the same evidence would never match itself.
    from gpa.crypto import fields as _fields

    existing = {
        (row.source_id, row.field, _fields.open_text(db, event.group_id, "event_link.quote", row.quote))
        for row in db.query(EventSourceLink)
        .filter(EventSourceLink.event_id == event.id)
        .all()
    }
    linked: set[str] = {row[0] for row in existing}
    provenance_rows = (
        db.query(Provenance).filter(Provenance.candidate_id == candidate.id).all()
    )
    for row in provenance_rows:
        chunk = db.get(SourceChunk, row.chunk_id)
        if chunk is None:
            continue
        key = (chunk.source_id, row.field, source_service.open_quote(db, row))
        if key in existing:
            continue
        db.add(
            EventSourceLink(
                event_id=event.id, source_id=chunk.source_id,
                field=row.field,
                quote=_fields.seal_text(
                    db, event.group_id, "event_link.quote",
                    source_service.open_quote(db, row),
                ),
            )
        )
        existing.add(key)
        linked.add(chunk.source_id)
    if candidate.source_id and candidate.source_id not in linked:
        db.add(
            EventSourceLink(
                event_id=event.id, source_id=candidate.source_id, field="", quote=""
            )
        )
        linked.add(candidate.source_id)
    return sorted(linked)


# ------------------------------------------------------------------
# Contradictions (B-079)
# ------------------------------------------------------------------


def raise_contradiction(
    db: Session,
    *,
    event: Event,
    field: str,
    current_value,
    new_value,
    new_source_id: str | None,
) -> EventContradiction:
    """Record (or extend) an open contradiction on one event
    field. Values are kept in the change-history string form
    with every source that said them; the entry matching the
    event's current value is marked ``current``. An already
    open contradiction for the field absorbs the new value —
    one dispute per field, with all its voices."""
    current_text = event_service._stringify(current_value)
    new_text = event_service._stringify(new_value)
    row = (
        db.query(EventContradiction)
        .filter(
            EventContradiction.event_id == event.id,
            EventContradiction.field == field,
            EventContradiction.status == "open",
        )
        .one_or_none()
    )
    if row is None:
        values = [
            {
                "value": current_text,
                "source_ids": sources_for_field(db, event, field),
                "current": True,
            },
            {
                "value": new_text,
                "source_ids": [new_source_id] if new_source_id else [],
                "current": False,
            },
        ]
        row = EventContradiction(
            event_id=event.id, group_id=event.group_id, field=field,
            values_json=json.dumps(values),
        )
        db.add(row)
        db.flush()
        return row
    values = json.loads(row.values_json or "[]")
    by_value = {entry["value"]: entry for entry in values}
    if new_text not in by_value:
        values.append(
            {
                "value": new_text,
                "source_ids": [new_source_id] if new_source_id else [],
                "current": False,
            }
        )
    elif new_source_id and new_source_id not in by_value[new_text]["source_ids"]:
        by_value[new_text]["source_ids"].append(new_source_id)
    for entry in values:
        entry["current"] = entry["value"] == current_text
    row.values_json = json.dumps(values)
    db.flush()
    return row


def list_contradictions(
    db: Session, event: Event, *, include_resolved: bool = False
) -> list[EventContradiction]:
    """An event's contradictions, oldest first — open only,
    unless the caller asks for the resolved history too."""
    query = db.query(EventContradiction).filter(
        EventContradiction.event_id == event.id
    )
    if not include_resolved:
        query = query.filter(EventContradiction.status == "open")
    return query.order_by(EventContradiction.created_at, EventContradiction.id).all()


def _parse_value(field: str, text: str):
    """A contradiction value back to its column type — the
    inverse of the change-history string form."""
    if field in ("starts_at", "ends_at"):
        try:
            return datetime.fromisoformat(text.replace("Z", ""))
        except ValueError:
            raise AppError(
                "validation_error",
                detail=f"That recorded value for {field} is not a readable time.",
            ) from None
    if field == "all_day":
        return text == "true"
    if field in ("cost_cents", "capacity"):
        return int(text) if text else None
    return text


def resolve_contradiction(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    event_id: str,
    contradiction_id: str,
    chosen_value: str,
    note: str = "",
) -> EventContradiction:
    """A person settles a disagreement: the chosen value —
    which must be one the sources actually said — is applied
    through the edit engine (a moved time sets
    needs_reannounce, like any material change), and the
    contradiction closes with who chose, when, and what."""
    event = event_service.get_event(db, group, event_id)
    row = db.get(EventContradiction, contradiction_id)
    if row is None or row.event_id != event.id:
        raise AppError("not_found", detail="That contradiction could not be found.")
    if row.status != "open":
        raise AppError("conflict", detail="That contradiction is already resolved.")
    if event.status == "cancelled":
        raise AppError("conflict", detail="A cancelled event cannot be edited.")
    values = json.loads(row.values_json or "[]")
    if chosen_value not in [entry["value"] for entry in values]:
        raise AppError(
            "validation_error",
            detail="Choose one of the values the sources actually said.",
        )
    parsed = _parse_value(row.field, chosen_value)
    event_service.apply_changes(
        db, event=event, changes={row.field: parsed},
        actor_label=actor_label, reason=note or "contradiction resolved",
    )
    for entry in values:
        entry["current"] = entry["value"] == chosen_value
    row.values_json = json.dumps(values)
    row.status = "resolved"
    row.resolved_by = actor_label
    row.resolved_at = now_utc()
    row.resolution = chosen_value
    audit.record(
        db, actor=actor_label, action="event.contradiction_resolved",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"contradiction_id": row.id, "field": row.field, "value": chosen_value},
    )
    db.commit()
    return row


# ------------------------------------------------------------------
# The merge itself (B-076)
# ------------------------------------------------------------------


def _values_agree(current: str, new: str) -> bool:
    """Two statements of a text field agree when they are the
    same words, or one contains the other (a fuller and a
    shorter form of one fact)."""
    norm_current, norm_new = normalised(current), normalised(new)
    if not norm_current or not norm_new:
        return False
    return norm_current == norm_new or norm_current in norm_new or norm_new in norm_current


def _field_entry(draft: dict, name: str) -> dict:
    fields = draft.get("fields", {})
    entry = fields.get(name, {}) if isinstance(fields, dict) else {}
    return entry if isinstance(entry, dict) else {}


def merge_candidate_into_event(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    candidate_id: str,
    event_id: str,
) -> dict:
    """Fold one event candidate into one existing event.

    Returns the outcome report — per field: filled, applied
    (better-sourced or fuller), agreed, kept (the event's value
    stood, with a contradiction raised when the candidate
    genuinely disagreed), or skipped (unsupported/resolved
    nothing) — so the queue can tell the reviewer exactly what
    their merge did. The candidate is marked ``merged`` with
    the event named in its draft; its provenance becomes the
    event's links either way.
    """
    candidate = candidate_service.get_candidate(db, group, candidate_id)
    if candidate.kind != "event":
        raise AppError(
            "validation_error",
            detail="Only an event candidate can merge into an event.",
        )
    if candidate.status not in candidate_service.OPEN_STATUSES:
        raise AppError("conflict", detail="That candidate has already been decided.")
    event = event_service.get_event(db, group, event_id)
    if event.status == "cancelled":
        raise AppError("conflict", detail="A cancelled event cannot take a merge.")

    draft = candidate_service.draft_of(candidate)
    tz = ZoneInfo(event.timezone or group.home_timezone or "UTC")
    outcome: dict[str, list[str]] = {
        "applied": [], "filled": [], "agreed": [], "kept": [], "skipped": [],
    }
    changes: dict = {}
    contradictions: list[EventContradiction] = []

    def disagree(event_field: str, current_value, new_value) -> None:
        """Settle a genuine disagreement by support: strictly
        better-sourced wins; otherwise the event stands and the
        dispute is kept (B-079). The candidate side's strength
        ACCUMULATES: when an open contradiction already records
        other sources saying this same value, they count —
        agreement by independent sources is evidence (Doc 07
        §8), and a value that arrives better-sourced than the
        event's own settles the dispute it lands in."""
        new_text = event_service._stringify(new_value)
        cand_strength = 1
        open_row = (
            db.query(EventContradiction)
            .filter(
                EventContradiction.event_id == event.id,
                EventContradiction.field == event_field,
                EventContradiction.status == "open",
            )
            .one_or_none()
        )
        if open_row is not None:
            for entry in json.loads(open_row.values_json or "[]"):
                if entry.get("value") == new_text:
                    voices = set(entry.get("source_ids", []))
                    if candidate.source_id:
                        voices.add(candidate.source_id)
                    cand_strength = max(cand_strength, len(voices))
        if cand_strength > _value_support(db, event, event_field):
            changes[event_field] = new_value
            outcome["applied"].append(event_field)
            if open_row is not None:
                # The merge settled the dispute it landed in:
                # close it, naming the value that won and the
                # person whose merge carried the evidence.
                values = json.loads(open_row.values_json or "[]")
                for entry in values:
                    entry["current"] = entry["value"] == new_text
                open_row.values_json = json.dumps(values)
                open_row.status = "resolved"
                open_row.resolved_by = actor_label
                open_row.resolved_at = now_utc()
                open_row.resolution = new_text
            return
        outcome["kept"].append(event_field)
        contradictions.append(
            raise_contradiction(
                db, event=event, field=event_field,
                current_value=current_value, new_value=new_value,
                new_source_id=candidate.source_id,
            )
        )

    # --- Text fields -------------------------------------------------
    for cand_name, event_field in TEXT_FIELD_MAP.items():
        entry = _field_entry(draft, cand_name)
        value = entry.get("value")
        if value is None:
            value = draft.get(cand_name)
        if value is None or str(value) == "":
            continue
        value = str(value)
        supported = bool(entry.get("supported"))
        current = str(getattr(event, event_field) or "")
        if not supported:
            # An unsupported claim never fills and never
            # overwrites — reported, not believed.
            if not current:
                outcome["skipped"].append(event_field)
            continue
        if not current:
            changes[event_field] = value
            outcome["filled"].append(event_field)
            continue
        if _values_agree(current, value):
            if len(normalised(value)) > len(normalised(current)):
                # The fuller form of the same fact refines it.
                changes[event_field] = value
                outcome["applied"].append(event_field)
            else:
                outcome["agreed"].append(event_field)
            continue
        disagree(event_field, current, value)

    # --- The start (and end, when the draft resolves one) ------------
    starts_entry = _field_entry(draft, "starts")
    if starts_entry.get("value") is not None or starts_entry.get("date_phrase"):
        start_local, all_day = event_service._draft_local_start(draft)
        if start_local is None:
            # The draft claims a start it never resolved (e.g.
            # an ambiguous phrase) — a person picks the reading
            # in the queue; a merge must not guess it.
            outcome["skipped"].append("starts_at")
        elif not starts_entry.get("supported", False):
            outcome["skipped"].append("starts_at")
        else:
            candidate_start = event_service._local_to_utc(start_local, tz)
            if all_day:
                event_day = (
                    event.starts_at.replace(tzinfo=timezone.utc)
                    .astimezone(tz)
                    .date()
                )
                if event_day == start_local.date():
                    outcome["agreed"].append("starts_at")
                else:
                    disagree("starts_at", event.starts_at, candidate_start)
            elif candidate_start == event.starts_at:
                outcome["agreed"].append("starts_at")
            else:
                disagree("starts_at", event.starts_at, candidate_start)

    ends_entry = _field_entry(draft, "ends")
    if ends_entry.get("resolved_date") and ends_entry.get("supported", False):
        try:
            end_day = datetime.fromisoformat(str(ends_entry["resolved_date"])).date()
            end_time = (
                time.fromisoformat(str(ends_entry["resolved_time"]))
                if ends_entry.get("resolved_time")
                else time(0, 0)
            )
            candidate_end = event_service._local_to_utc(
                datetime.combine(end_day, end_time), tz
            )
        except ValueError:
            candidate_end = None
        if candidate_end is not None:
            if candidate_end == event.ends_at:
                outcome["agreed"].append("ends_at")
            else:
                disagree("ends_at", event.ends_at, candidate_end)

    # --- Apply, link, record ------------------------------------------
    if changes:
        event_service.apply_changes(
            db, event=event, changes=changes, actor_label=actor_label,
            reason=f"merge of candidate {candidate.id}",
        )
    linked_sources = link_candidate_sources(db, event, candidate)
    candidate.status = "merged"
    draft["merged_into_event_id"] = event.id
    candidate.draft_json = json.dumps(draft)
    audit.record(
        db, actor=actor_label, action="candidate.merged_into_event",
        group_id=group.id, entity_type="candidate", entity_id=candidate.id,
        detail={
            "event_id": event.id,
            "applied": outcome["applied"], "filled": outcome["filled"],
            "kept": outcome["kept"],
            "contradictions": [row.id for row in contradictions],
            "sources_linked": linked_sources,
        },
    )
    db.commit()
    return {
        "event": event,
        **outcome,
        "contradictions": contradictions,
    }


# ------------------------------------------------------------------
# Change candidates (B-077): approval applies the change
# ------------------------------------------------------------------


def _local_parts(instant_utc: datetime, tz: ZoneInfo):
    """An event's stored instant as (local date, local time) —
    the parts a partial change keeps when it moves only the
    other one ("moved to 7.30" keeps the day; "moved to
    Saturday" keeps the hour)."""
    local = instant_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    return local.date(), local.time()


def apply_change_candidate(
    db: Session,
    *,
    group: Group,
    candidate: Candidate,
    actor_label: str,
) -> Event:
    """Apply an approved change candidate to its event (B-077).

    Called inside the approval's transaction (never commits):
    the decision and the change land together or not at all.
    The change applies through the same machinery as an edit —
    EventChange rows, ``needs_reannounce`` on material fields —
    and the candidate's source joins the event's links, so the
    event can answer for its new state too.

    Refusals are plain and total (the candidate stays pending):
    no target linked, a target that is gone or cancelled, a
    claimed change its source does not back, a new time that
    never resolved to one reading, or a "change" that changes
    nothing. A rejected change candidate never reaches here —
    rejection is recorded and the event stands (Doc 07 §6).
    """
    draft = candidate_service.draft_of(candidate)
    target_id = draft.get("target_event_id")
    if not target_id:
        raise AppError(
            "validation_error",
            detail=(
                "This change does not name an event the group has, so "
                "there is nothing to apply it to. Edit the draft to "
                "name the event, or reject the change."
            ),
        )
    try:
        event = event_service.get_event(db, group, target_id)
    except AppError:
        raise AppError(
            "validation_error",
            detail="The event this change names is no longer there.",
        ) from None
    if event.status == "cancelled":
        raise AppError(
            "conflict",
            detail="That event is already cancelled — this change has nowhere to land.",
        )
    fields = draft.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}

    # Every field the draft CLAIMS must be backed by its
    # source: an unsupported claim is refused whole, in plain
    # words, rather than partially believed.
    claimed = [
        name
        for name in ("starts", "ends", "place", "cancelled")
        if isinstance(fields.get(name), dict)
        and fields[name].get("value") is not None
    ]
    for name in claimed:
        if not fields[name].get("supported", False):
            raise AppError(
                "validation_error",
                detail=(
                    f"The change's {name} is not backed by its source's "
                    "words, so it cannot be applied. Edit the draft "
                    "or reject the change."
                ),
            )

    tz = ZoneInfo(event.timezone or group.home_timezone or "UTC")
    changes: dict = {}
    reason = f"change candidate {candidate.id}"

    starts_entry = fields.get("starts") or {}
    moved = False
    if "starts" in claimed:
        resolved_date = starts_entry.get("resolved_date")
        resolved_time = starts_entry.get("resolved_time")
        if not resolved_date and not resolved_time:
            raise AppError(
                "validation_error",
                detail=(
                    "The change's new time never resolved to one "
                    "reading — edit the draft to pin the day or the "
                    "time down, or reject the change."
                ),
            )
        old_date, old_time = _local_parts(event.starts_at, tz)
        new_local = datetime.combine(
            datetime.fromisoformat(str(resolved_date)).date()
            if resolved_date else old_date,
            time.fromisoformat(str(resolved_time)) if resolved_time else old_time,
        )
        new_start = event_service._local_to_utc(new_local, tz)
        if new_start != event.starts_at:
            changes["starts_at"] = new_start
            moved = True

    ends_entry = fields.get("ends") or {}
    if "ends" in claimed:
        resolved_date = ends_entry.get("resolved_date")
        resolved_time = ends_entry.get("resolved_time")
        if not resolved_date and not resolved_time:
            raise AppError(
                "validation_error",
                detail=(
                    "The change's new end never resolved to one "
                    "reading — edit the draft or reject the change."
                ),
            )
        base_date, base_time = _local_parts(
            changes.get("starts_at", event.starts_at), tz
        )
        _end_date, end_time = _local_parts(event.ends_at, tz)
        new_local = datetime.combine(
            datetime.fromisoformat(str(resolved_date)).date()
            if resolved_date else base_date,
            time.fromisoformat(str(resolved_time))
            if resolved_time else end_time,
        )
        new_end = event_service._local_to_utc(new_local, tz)
        if new_end != event.ends_at:
            changes["ends_at"] = new_end
    elif moved:
        # A move moves the whole event: the end travels by the
        # same delta, keeping the event's length — a move that
        # silently shortened the event would be a second,
        # unannounced change.
        delta = changes["starts_at"] - event.starts_at
        changes["ends_at"] = event.ends_at + delta

    if "place" in claimed:
        new_place = str(fields["place"].get("value") or "")[:240]
        if new_place != event.place_name:
            changes["place_name"] = new_place

    is_cancel = "cancelled" in claimed
    if not changes and not is_cancel:
        raise AppError(
            "validation_error",
            detail=(
                "This change candidate does not actually change "
                "anything about the event — reject it, or edit "
                "the draft into a real change."
            ),
        )
    if changes:
        event_service.apply_changes(
            db, event=event, changes=changes,
            actor_label=actor_label, reason=reason,
        )
    if is_cancel:
        quote = str(fields["cancelled"].get("quote") or "")
        event_service.apply_cancellation(
            db, event=event, actor_label=actor_label,
            reason=quote or "Cancelled in a later announcement",
        )
    link_candidate_sources(db, event, candidate)
    candidate.resulting_entity_type = "event"
    candidate.resulting_entity_id = event.id
    audit.record(
        db, actor=actor_label, action="event.change_applied",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={
            "candidate_id": candidate.id,
            "fields": sorted(changes),
            "cancelled": is_cancel,
        },
    )
    return event
