"""Automatic data-repair jobs (roadmap B-173).

Things drift. A process dies between two writes that were
supposed to land together; an early build stored a derived
value wrongly; a hard delete (erasure, a tombstone purge)
takes a parent row and leaves a child pointing at nothing.
None of that is a person's fault and none of it should need
a person with a SQL prompt: this module is the fix-forward
crew. A ``data_repair`` job runs the repairs below, each of
which recomputes a truth from the rows that OWN the truth
and rewrites only the derived or dangling copy:

* **Job progress** — ``progress`` is derived from ``status``
  (the chassis sets 100 when a job finishes); a finished job
  resting below 100 is recomputed to it.
* **Orphaned provenance** — a provenance row whose chunk (or
  candidate) no longer exists supports nothing. Foreign-key
  cascades make these rare, not impossible (hard deletes run
  with the cascade as backstop, not as the plan); the repair
  removes the dangling row. Event source links are NOT
  touched: a link naming a deleted source is the B-023
  tombstone law working as designed — the event keeps the
  quote it carried.
* **Chunk positions** — a source's chunk order is derived
  from its rows; if positions carry gaps or duplicates (a
  torn append can leave both), they are renumbered 0..n-1
  preserving the order the rows already claim.
* **Flyer alt-text status** — the status is derived from the
  text beside it by B-065's genuineness rule; a row claiming
  ``complete`` over a placeholder (or resting at
  ``awaiting_description`` over a genuine description a
  person wrote) is recomputed so the visible-defect law
  tells the truth again.
* **Stale digests** — a digest row whose renderings are
  empty (a compose interrupted after the row landed) is
  re-rendered through the one rendering door
  (``digests.compose_digest``) for its own stored week, and
  the fresh renderings are copied onto the original row —
  the sent-words-are-stored-words law keeps one row per
  digest, so the fresh row the compose creates is removed
  again inside the same transaction.

Every repair returns the list of what it fixed, in plain
words, and the job handler writes ONE audit row
(``repair.completed``, actor ``system:repair``) carrying the
whole list — a repair that cannot say what it did did not
happen. Repairs never invent content and never delete user
material: the only rows removed are provenance rows pointing
at things that no longer exist, and a digest's temporary
re-render. A payload may name ``group_id`` to scope the
group-owned repairs to one group; job progress is instance
bookkeeping and is repaired regardless.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from gpa.models.core import Job
from gpa.models.groups import Candidate, Group
from gpa.models.notifications import Digest
from gpa.models.sources import Flyer, Provenance, Source, SourceChunk
from gpa.services import audit
from gpa.services import digests as digest_service
from gpa.services import jobs as job_service
from gpa.services import vision as vision_service

REPAIR_JOB_KIND = "data_repair"


def repair_job_progress(db: Session) -> list[str]:
    """Recompute the derived progress of finished jobs."""
    fixes: list[str] = []
    finished = (
        db.query(Job)
        .filter(Job.status == "done", Job.progress != 100)
        .all()
    )
    for job in finished:
        job.progress = 100
        fixes.append(f"job {job.id} ({job.kind}): progress set to 100 (it is done)")
    return fixes


def repair_orphaned_provenance(db: Session, *, group_id: str | None) -> list[str]:
    """Remove provenance rows whose chunk or candidate is gone.

    The check is structural — ids against ids — so sealed
    quote text (B-167) is never opened by a repair."""
    fixes: list[str] = []
    chunk_ids = {row[0] for row in db.query(SourceChunk.id).all()}
    candidate_ids = {row[0] for row in db.query(Candidate.id).all()}
    if group_id is None:
        in_scope = candidate_ids
    else:
        in_scope = {
            row[0]
            for row in db.query(Candidate.id)
            .filter(Candidate.group_id == group_id)
            .all()
        }
    for row in db.query(Provenance).all():
        missing = None
        if row.chunk_id not in chunk_ids:
            missing = "its chunk no longer exists"
        elif row.candidate_id not in candidate_ids:
            missing = "its candidate no longer exists"
        if missing is None:
            continue
        # Scope law: a scoped run touches only its group's
        # candidates' rows. A row whose candidate is GONE
        # belongs to no group — the unscoped run collects it.
        if group_id is not None and row.candidate_id not in in_scope:
            continue
        fixes.append(f"provenance {row.id}: removed ({missing})")
        db.delete(row)
    return fixes


def repair_chunk_positions(db: Session, *, group_id: str | None) -> list[str]:
    """Renumber each source's chunks 0..n-1 in claimed order."""
    fixes: list[str] = []
    source_ids = [row[0] for row in db.query(SourceChunk.source_id).distinct().all()]
    for source_id in source_ids:
        if group_id is not None:
            source_row = db.get(Source, source_id)
            if source_row is None or source_row.group_id != group_id:
                continue
        chunks = (
            db.query(SourceChunk)
            .filter(SourceChunk.source_id == source_id)
            .order_by(SourceChunk.position, SourceChunk.id)
            .all()
        )
        if [chunk.position for chunk in chunks] != list(range(len(chunks))):
            for index, chunk in enumerate(chunks):
                chunk.position = index
            fixes.append(
                f"source {source_id}: chunk positions renumbered "
                f"0..{len(chunks) - 1}"
            )
    return fixes


def repair_flyer_alt_status(db: Session, *, group_id: str | None) -> list[str]:
    """Recompute each flyer's alt-text status from its text."""
    fixes: list[str] = []
    query = db.query(Flyer)
    if group_id is not None:
        query = query.filter(Flyer.group_id == group_id)
    for flyer in query.all():
        genuine = vision_service.is_genuine_alt(flyer.alt_text or "")
        expected = "complete" if genuine else "awaiting_description"
        if flyer.alt_text_status != expected:
            fixes.append(
                f"flyer {flyer.id}: alt_text_status "
                f"{flyer.alt_text_status} -> {expected} (recomputed from its text)"
            )
            flyer.alt_text_status = expected
    return fixes


def repair_stale_digests(db: Session, *, group_id: str | None) -> list[str]:
    """Re-render digest rows whose stored renderings are empty."""
    fixes: list[str] = []
    query = db.query(Digest).filter(
        (Digest.body_text == "") | (Digest.body_html == "")
    )
    if group_id is not None:
        query = query.filter(Digest.group_id == group_id)
    for digest in query.all():
        group = db.get(Group, digest.group_id)
        if group is None:
            fixes.append(f"digest {digest.id}: left alone (its group is gone)")
            continue
        fresh = digest_service.compose_digest(db, group=group, now=digest.week_start)
        digest.subject = fresh.subject
        digest.body_text = fresh.body_text
        digest.body_html = fresh.body_html
        db.delete(fresh)
        fixes.append(
            f"digest {digest.id}: re-rendered for its week "
            f"({digest.week_start.date()} to {digest.week_end.date()})"
        )
    return fixes


def run_repairs(db: Session, *, group_id: str | None = None) -> list[str]:
    """Run every repair once; return the full list of fixes.

    Order is structural-before-content: dangling rows go
    before renumbering reads positions, and digests render
    last because they read the repaired world."""
    fixes: list[str] = []
    fixes += repair_job_progress(db)
    fixes += repair_orphaned_provenance(db, group_id=group_id)
    fixes += repair_chunk_positions(db, group_id=group_id)
    fixes += repair_flyer_alt_status(db, group_id=group_id)
    fixes += repair_stale_digests(db, group_id=group_id)
    return fixes


def handle_data_repair(db: Session, payload: dict) -> None:
    """The ``data_repair`` job handler: fix forward, then say
    exactly what was fixed, in the audit, in one commit."""
    group_id = payload.get("group_id") or None
    fixes = run_repairs(db, group_id=group_id)
    audit.record(
        db,
        actor="system:repair",
        action="repair.completed",
        group_id=group_id,
        entity_type="repair",
        entity_id=group_id or "",
        detail={"fix_count": len(fixes), "fixes": fixes},
    )
    db.commit()


def register_handlers() -> None:
    """Offer the repair job to the chassis (import-time, the
    house pattern every handler-bearing service follows)."""
    job_service.register_handler(REPAIR_JOB_KIND, handle_data_repair)


register_handlers()

__all__ = [
    "REPAIR_JOB_KIND",
    "handle_data_repair",
    "repair_chunk_positions",
    "repair_flyer_alt_status",
    "repair_job_progress",
    "repair_orphaned_provenance",
    "repair_stale_digests",
    "run_repairs",
]
