"""The review queue (roadmap B-043, B-044, B-047).

Candidates are the only thing AI may create, and this service is
the queue humans work: list (filtered by kind / status / minimum
confidence), read with full provenance, edit the draft, reject,
merge two drafts into one. Approval itself lives in
services/groups.py (B-018's founding rule — seats never approve —
was written there first and stays the single door); this module
extends its reach to edited drafts and adds everything around it.

Two laws run through the whole file:

- **Decisions never erase.** Rejection keeps the draft and its
  provenance; merging keeps the merged row whole (status
  ``merged``, ``merged_into_id`` set) — the queue's history is
  evidence, like the audit log.
- **Flags are the API's to decide** (B-044): after any human
  edit, flags are recomputed here from the draft's evidence.
  Clients send drafts; they never send flags.
"""

import json

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Candidate, Group
from gpa.models.sources import Provenance
from gpa.services import audit, quality

# Statuses a reviewer may still act on (edit / reject / merge /
# approve). Decided rows — approved, rejected, merged,
# superseded — are history and refuse further decisions.
OPEN_STATUSES = ("pending", "edited")


def get_candidate(db: Session, group: Group, candidate_id: str) -> Candidate:
    """One candidate of this group, or 404 (a candidate of
    another group is indistinguishable from a missing one)."""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.group_id != group.id:
        raise AppError("not_found", detail="That candidate could not be found.")
    return candidate


def draft_of(candidate: Candidate) -> dict:
    """The candidate's draft as a dict ({} when unparseable —
    which the service treats as a broken draft, not a crash)."""
    try:
        draft = json.loads(candidate.draft_json or "{}")
    except json.JSONDecodeError:
        return {}
    return draft if isinstance(draft, dict) else {}


def flags_of(candidate: Candidate) -> list[str]:
    """The candidate's stored server-computed flags."""
    try:
        flags = json.loads(candidate.flags_json or "[]")
    except json.JSONDecodeError:
        return []
    return flags if isinstance(flags, list) else []


def list_candidates(
    db: Session,
    group: Group,
    *,
    kind: str | None = None,
    status: str | None = None,
    min_confidence: float | None = None,
) -> list[Candidate]:
    """The queue, newest first, with the triage filters (B-043)."""
    query = db.query(Candidate).filter(Candidate.group_id == group.id)
    if kind:
        query = query.filter(Candidate.kind == kind)
    if status:
        query = query.filter(Candidate.status == status)
    if min_confidence is not None:
        query = query.filter(Candidate.confidence >= min_confidence)
    return query.order_by(Candidate.created_at.desc(), Candidate.id.desc()).all()


def provenance_of(db: Session, candidate: Candidate) -> list[dict]:
    """The candidate's per-field evidence in field order: quote,
    chunk, support verdict (written by extraction, B-040/B-045) —
    plus any hand-added rows (B-023's add_provenance)."""
    rows = (
        db.query(Provenance)
        .filter(Provenance.candidate_id == candidate.id)
        .order_by(Provenance.created_at, Provenance.id)
        .all()
    )
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.field, row.chunk_id)
        if key in seen:
            continue  # one row per field/chunk in the detail view
        seen.add(key)
        out.append(
            {
                "field": row.field,
                "chunk_id": row.chunk_id,
                "chunk_position": None,
                "quote": row.quote,
                # A vision row's evidence is its region (B-063):
                # the box rides along so the queue can highlight
                # the rectangle the field was read from.
                "region": (
                    json.loads(row.region_json) if row.region_json else None
                ),
                "supported": True,
                "sent_at": None,
            }
        )
    # Draft-carried entries cover fields whose quote verified
    # against a chunk AND fields that failed verification (their
    # Provenance row is absent by design — unsupported means *no*
    # supporting row). Merge them field by field so the detail
    # view shows every field's verdict exactly once.
    fields = draft_of(candidate).get("fields", {})
    by_field = {entry["field"]: entry for entry in out}
    for name, entry in fields.items():
        supported = bool(entry.get("supported"))
        if name in by_field and supported:
            by_field[name]["supported"] = True
            by_field[name]["quote"] = entry.get("quote", by_field[name]["quote"])
            by_field[name]["chunk_position"] = entry.get("chunk_position")
            by_field[name]["sent_at"] = entry.get("sent_at")
            if by_field[name].get("region") is None:
                by_field[name]["region"] = entry.get("region")
        elif name not in by_field:
            out.append(
                {
                    "field": name,
                    "chunk_id": None,
                    "chunk_position": entry.get("chunk_position"),
                    "quote": entry.get("quote", ""),
                    # A vision field without a row (its region
                    # failed validation) still shows the box the
                    # model claimed, labelled unsupported (B-063).
                    "region": entry.get("region"),
                    "supported": supported,
                    "sent_at": entry.get("sent_at"),
                }
            )
        else:
            by_field[name]["supported"] = supported
            by_field[name]["sent_at"] = entry.get("sent_at")
    return out


def _siblings(db: Session, candidate: Candidate) -> list[tuple[str | None, dict]]:
    """Live same-kind candidates of the group (for conflict
    flags): (source_id, draft) pairs, excluding this candidate."""
    rows = (
        db.query(Candidate)
        .filter(
            Candidate.group_id == candidate.group_id,
            Candidate.kind == candidate.kind,
            Candidate.id != candidate.id,
            Candidate.status.in_(OPEN_STATUSES + ("approved",)),
        )
        .all()
    )
    return [(row.source_id, draft_of(row)) for row in rows]


def refresh_flags(db: Session, candidate: Candidate) -> None:
    """Recompute a candidate's flags from its draft + siblings
    (B-044), preserving the pipeline's coverage flag, which is
    computed over the extraction run rather than the draft alone."""
    kept = [f for f in flags_of(candidate) if f == "shared_chunk_unflagged"]
    computed = quality.compute_flags(
        draft_of(candidate),
        siblings=_siblings(db, candidate),
        own_source_id=candidate.source_id,
    )
    merged = list(dict.fromkeys(kept + computed))  # order-stable dedupe
    candidate.flags_json = json.dumps(merged)


def edit_candidate(
    db: Session, *, actor_label: str, group: Group, candidate_id: str, draft: dict
) -> Candidate:
    """A reviewer's edit: replace the draft, mark the candidate
    ``edited``, recompute flags, and re-derive the title from the
    draft's subject field so the queue list stays truthful.
    Editing a decided candidate is refused — decisions are
    history."""
    candidate = get_candidate(db, group, candidate_id)
    if candidate.status not in OPEN_STATUSES:
        raise AppError("conflict", detail="That candidate has already been decided.")
    if not isinstance(draft, dict) or "fields" not in draft:
        raise AppError("validation_error", detail="A draft must carry its 'fields' map.")
    draft.setdefault("kind", candidate.kind)
    candidate.draft_json = json.dumps(draft)
    candidate.status = "edited"
    subject_field = quality.SUBJECT_FIELD.get(candidate.kind, "title")
    subject = draft.get("fields", {}).get(subject_field, {}).get("value")
    if subject:
        candidate.title = str(subject)[:240]
    refresh_flags(db, candidate)
    audit.record(
        db, actor=actor_label, action="candidate.edited",
        group_id=group.id, entity_type="candidate", entity_id=candidate.id,
    )
    db.commit()
    return candidate


def reject_candidate(
    db: Session, *, actor_label: str, group: Group, candidate_id: str
) -> Candidate:
    """Reject a draft. The row, its draft and its provenance all
    stay — a rejection is a decision about the draft, not an
    erasure of it."""
    candidate = get_candidate(db, group, candidate_id)
    if candidate.status not in OPEN_STATUSES:
        raise AppError("conflict", detail="That candidate has already been decided.")
    candidate.status = "rejected"
    audit.record(
        db, actor=actor_label, action="candidate.rejected",
        group_id=group.id, entity_type="candidate", entity_id=candidate.id,
    )
    db.commit()
    return candidate


def merge_candidates(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    candidate_id: str,
    into_candidate_id: str,
) -> Candidate:
    """Merge one open candidate into another (B-043): the loser
    is marked ``merged`` with ``merged_into_id`` pointing at the
    survivor; both drafts and both provenance sets survive —
    merging decides which draft the queue works, it does not
    rewrite either draft's contents."""
    if candidate_id == into_candidate_id:
        raise AppError("validation_error", detail="A candidate cannot merge into itself.")
    loser = get_candidate(db, group, candidate_id)
    survivor = get_candidate(db, group, into_candidate_id)
    if loser.kind != survivor.kind:
        raise AppError("validation_error", detail="Only candidates of the same kind can merge.")
    for row in (loser, survivor):
        if row.status not in OPEN_STATUSES:
            raise AppError("conflict", detail="A decided candidate cannot take part in a merge.")
    loser.status = "merged"
    loser.merged_into_id = survivor.id
    audit.record(
        db, actor=actor_label, action="candidate.merged",
        group_id=group.id, entity_type="candidate", entity_id=loser.id,
        detail={"into": survivor.id},
    )
    db.commit()
    return survivor
