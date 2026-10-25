"""Extraction runs (roadmap B-045) + the budget gate (B-047).

Extraction is a JOB per source, never a request-thread act: a
person (organiser+) asks, a durable job runs, and its candidates
land in the review queue stamped with the prompt name + version
that made them, the job id, and "system:extraction" as their
author (the requester is on the job payload and the audit — a
machine's drafts are never attributed to a person).

Re-runs are additive history (B-045): a fresh run creates fresh
candidates and marks the previous run's still-undecided ones
``superseded`` — decided rows (approved / rejected / merged) are
never touched, drafts are never edited in place, and the audit
shows both runs. Sources still waiting on an earlier stage
(transcription, segmentation) are skipped, not failed: nothing
may be drafted from material that is not text yet.

The budget gate (B-047) stands before each kind's step: the
group's day-spend is checked, and at or over the cap the run
defers itself — steps already completed in this run are kept, a
new extract job is queued for the next UTC midnight, and this
job completes, its remaining work honestly postponed rather than
dropped or silently half-done.
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from gpa.ai import client as ai_client
from gpa.ai import extraction as pipeline
from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.groups import Candidate, Group
from gpa.models.sources import Provenance, Source
from gpa.services import audit, jobs as job_service, quality, sources as source_service
from gpa.services import usage as usage_service

JOB_KIND = "extract"
ALL_KINDS = ["event", "task", "decision", "question"]
# Everything a run may be ASKED for. The default set stays the
# four drafting kinds; change detection (B-077) is a named
# kind a run requests explicitly — it drafts against events
# that already exist, and running it over every source by
# default would draft "changes" out of first announcements.
KNOWN_KINDS = ALL_KINDS + ["event_change"]
# Stages from which drafting would be drafting from nothing.
WAITING_STAGES = ("awaiting_transcription", "awaiting_segmentation")
MACHINE_AUTHOR = "system:extraction"


class BudgetPaused(Exception):
    """The group's daily budget ran out mid-run: defer, don't fail."""


def request_extraction(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    source_id: str,
    kinds: list[str] | None,
) -> object:
    """Queue an extraction job for one source (organiser+ at the
    door). Returns the job; the run itself is the worker's."""
    source = source_service.get_source(db, group, source_id)
    chosen = kinds or ALL_KINDS
    unknown = [k for k in chosen if k not in KNOWN_KINDS]
    if unknown:
        raise AppError(
            "validation_error",
            detail=f"Unknown extraction kinds: {', '.join(unknown)}.",
        )
    job = job_service.enqueue(
        db, settings, kind=JOB_KIND,
        payload={
            "group_id": group.id,
            "source_id": source.id,
            "kinds": chosen,
            "requested_by": actor_label,
        },
    )
    # Stamp the job's own id into its payload (enqueue commits
    # first — the id only exists afterwards) so the candidates
    # the run creates can carry the job that made them (B-045).
    payload = json.loads(job.payload_json or "{}")
    payload["job_id"] = job.id
    job.payload_json = json.dumps(payload)
    db.commit()
    audit.record(
        db, actor=actor_label, action="extraction.requested",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"job_id": job.id, "kinds": chosen},
    )
    db.commit()
    return job


def run_extraction(
    db: Session,
    *,
    group: Group,
    source: Source,
    kinds: list[str],
    requested_by: str,
    job_id: str | None,
) -> dict:
    """Run the pipeline over one source and persist the drafts.

    Returns a summary (candidates created, per-kind counts, the
    prompt versions used). Raises BudgetPaused when the budget
    stops the run (the handler defers); ModelError propagates —
    a failed run fails its job visibly (B-045).
    """
    if source.stage in WAITING_STAGES or source.status in WAITING_STAGES:
        audit.record(
            db, actor=requested_by, action="extraction.skipped",
            group_id=group.id, entity_type="source", entity_id=source.id,
            detail={"reason": source.stage or source.status},
        )
        db.commit()
        return {"skipped": source.stage or source.status, "candidates": 0}

    chunks = source_service.list_chunks(db, source)
    client = ai_client.get_client()
    timezone_name = group.home_timezone or "UTC"

    def record(result, prompt_name):
        """Ledger one call in this run's transaction (B-047)."""
        usage_service.record_usage(
            db, result=result, group_id=group.id, source_id=source.id,
            operation=f"extract:{prompt_name}", prompt_name=prompt_name,
            prompt_version=None,
        )

    def budget_gate():
        """Before each kind's step: spent-at-cap means stop now.

        The cap attribute is expired first — a long-lived worker
        session must never pace a day by yesterday's settings."""
        db.expire(group, ["ai_daily_budget_micros"])
        state = usage_service.budget_state(db, group)
        if state["paused"]:
            raise BudgetPaused()

    created: list[Candidate] = []
    versions: dict[str, int] = {}
    for kind in kinds:
        budget_gate()
        extracted = pipeline.extract_kind(
            client,
            kind=kind,
            chunks=chunks,
            group_timezone=timezone_name,
            source_created_at=source.created_at,
            record_usage=record,
        )
        # The supersede rule (B-045): this kind's older undecided
        # drafts FROM THIS SOURCE give way to the new run. Decided
        # rows are history and are never touched.
        stale = (
            db.query(Candidate)
            .filter(
                Candidate.group_id == group.id,
                Candidate.source_id == source.id,
                Candidate.kind == kind,
                Candidate.status.in_(("pending", "edited")),
            )
            .all()
        )
        for old in stale:
            old.status = "superseded"
        for item in extracted:
            versions[item.prompt_name] = item.prompt_version
            title = (
                item.draft.get("fields", {})
                .get(quality.SUBJECT_FIELD.get(kind, "title"), {})
                .get("value")
                or ""
            )
            if kind == "event_change":
                # Link the change to the event it names (B-077):
                # the target is stamped into the draft before
                # the candidate exists, so the queue can show
                # "change to <event>" and approval knows where
                # the change lands. An unmatched name is NOT a
                # failure — it is flagged below
                # (change_target_unknown) for a person to see.
                from gpa.services import matching as matching_service

                target, score = matching_service.match_by_name(
                    db, group, str(title), item.draft
                )
                item.draft["target_event_id"] = target.id if target else None
                item.draft["target_title"] = target.title if target else None
                item.draft["match_score"] = round(score, 4)
            candidate = Candidate(
                group_id=group.id,
                kind=kind,
                title=str(title)[:240],
                created_by=MACHINE_AUTHOR,
                status="pending",
                source_id=source.id,
                draft_json=json.dumps(item.draft),
                confidence=item.confidence,
                prompt_name=item.prompt_name,
                prompt_version=item.prompt_version,
                job_id=job_id,
                flags_json=json.dumps(item.flags),
            )
            db.add(candidate)
            db.flush()  # the id exists before provenance points at it
            for row in item.field_provenance:
                if row["supported"] and row["chunk_id"]:
                    db.add(
                        Provenance(
                            candidate_id=candidate.id,
                            chunk_id=row["chunk_id"],
                            field=row["field"],
                            quote=row["quote"],
                        )
                    )
            created.append(candidate)
        db.flush()

    # Quality flags (B-044) are computed with the whole group in
    # view — including the candidates this run just made, so two
    # sources disagreeing inside one run flag each other too.
    for candidate in created:
        siblings = [
            (other.source_id, _draft(other))
            for other in db.query(Candidate)
            .filter(
                Candidate.group_id == group.id,
                Candidate.kind == candidate.kind,
                Candidate.id != candidate.id,
                Candidate.status.in_(("pending", "edited", "approved")),
            )
            .all()
        ]
        computed = quality.compute_flags(
            _draft(candidate), siblings=siblings, own_source_id=candidate.source_id
        )
        # Stored flags at this point are the pipeline's own
        # (coverage); the computed vocabulary merges in behind
        # them, order-stable, no duplicates.
        merged = list(dict.fromkeys(json.loads(candidate.flags_json or "[]") + computed))
        if (
            candidate.kind == "event_change"
            and not _draft(candidate).get("target_event_id")
            and "change_target_unknown" not in merged
        ):
            # The change names no event this group has (B-077):
            # the draft waits in the queue saying exactly that,
            # instead of landing on the wrong event or vanishing.
            merged.append("change_target_unknown")
        candidate.flags_json = json.dumps(merged)

    # A conflict is a two-sided fact: a pre-existing candidate
    # that the new run now contradicts earns its flag too.
    from gpa.services import candidates as candidate_service

    created_ids = {c.id for c in created}
    touched_kinds = {c.kind for c in created}
    if touched_kinds:
        neighbours = (
            db.query(Candidate)
            .filter(
                Candidate.group_id == group.id,
                Candidate.kind.in_(touched_kinds),
                Candidate.status.in_(("pending", "edited")),
                Candidate.id.notin_(created_ids),
            )
            .all()
        )
        for other in neighbours:
            candidate_service.refresh_flags(db, other)

    audit.record(
        db, actor=requested_by, action="extraction.completed",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={
            "job_id": job_id,
            "candidates": len(created),
            "kinds": kinds,
            "prompt_versions": versions,
        },
    )
    db.commit()
    return {"candidates": len(created), "prompt_versions": versions}


def _draft(candidate: Candidate) -> dict:
    try:
        draft = json.loads(candidate.draft_json or "{}")
    except json.JSONDecodeError:
        return {}
    return draft if isinstance(draft, dict) else {}


def handle_extract(db: Session, payload: dict) -> None:
    """The job handler (B-045). Budget pauses defer the run to the
    next UTC midnight as a NEW job (this one completes — its
    deferral is recorded, and the queue's status endpoint says
    why the wait exists, B-047)."""
    group = db.get(Group, payload.get("group_id", ""))
    source = db.get(Source, payload.get("source_id", ""))
    if group is None or source is None or source.group_id != group.id:
        raise RuntimeError("extract job names a missing group or source")
    kinds = payload.get("kinds") or ALL_KINDS
    requested_by = payload.get("requested_by", MACHINE_AUTHOR)
    # The job row this handler is running under: recover its id
    # from the payload when present (tests enqueue with it), else
    # stamp None — provenance to the job is best-effort metadata,
    # never worth failing a run over.
    job_id = payload.get("job_id")
    try:
        run_extraction(
            db, group=group, source=source, kinds=kinds,
            requested_by=requested_by, job_id=job_id,
        )
    except BudgetPaused:
        from gpa.config import get_settings

        job_service.enqueue(
            db, get_settings(), kind=JOB_KIND,
            payload={**payload, "deferred_from_budget": True},
            run_at=usage_service.next_budget_reset(datetime.now(timezone.utc)),
        )
        audit.record(
            db, actor=requested_by, action="extraction.deferred_budget",
            group_id=group.id, entity_type="source", entity_id=source.id,
            detail={"reason": "daily AI budget spent"},
        )
        db.commit()


def register_handlers() -> None:
    """Offer the extract handler to the job chassis (B-008)."""
    job_service.register_handler(JOB_KIND, handle_extract)


# Registration happens on import, exactly like the transcribe
# handler: any process that loads this module can run the job.
register_handlers()
