"""Review-queue routes (roadmap B-043, B-045, B-047).

The queue is the product's centre of gravity: everything the AI
drafts lands here, and only a person's decision moves it. Reads
are member+; decisions (edit / reject / merge) and extraction
requests are organiser+ with the publish scope for key callers —
and agent seats, whatever their role, are refused approval
inside the service (B-018), while these decision routes mirror
the same door: a seat's scopes top out at draft, so a seat key
never carries publish in the first place.

Approval itself is served by routers/groups.py (the B-018 stub's
route, unchanged); this router carries the rest of the queue.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.candidates import (
    CandidateDetailResponse,
    CandidateEditRequest,
    CandidateMergeRequest,
    ExtractAcceptedResponse,
    ExtractRequest,
    MergeEventRequest,
    MergeEventResponse,
    QueueStatusResponse,
    candidate_detail,
    candidate_row,
)
from gpa.schemas.groups import CandidateResponse
from gpa.schemas.sources import VisionReadAcceptedResponse
from gpa.services import candidates as candidate_service
from gpa.services import extraction as extraction_service
from gpa.services import usage as usage_service
from gpa.services import vision as vision_service

router = APIRouter(prefix="/api/v1/groups", tags=["candidates"])


@router.get("/{group_id}/candidates/status", response_model=QueueStatusResponse)
def queue_status(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """How much waits + the budget truth, in plain words (B-047):
    when the group's daily AI budget is spent, ``paused`` is true
    and ``pause_reason`` is "budget" — the queue says
    "paused — budget", it does not dress it up."""
    pending = (
        db.query(candidate_service.Candidate)
        .filter(
            candidate_service.Candidate.group_id == context.group.id,
            candidate_service.Candidate.status.in_(("pending", "edited")),
        )
        .count()
    )
    state = usage_service.budget_state(db, context.group)
    return QueueStatusResponse(
        pending_count=pending,
        paused=state["paused"],
        pause_reason=state["pause_reason"],
        spent_today_micros=state["spent_today_micros"],
        budget_micros=state["budget_micros"],
    )


@router.get("/{group_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The queue: newest first, filterable by kind, status, and
    minimum candidate confidence (B-043)."""
    rows = candidate_service.list_candidates(
        db, context.group, kind=kind, status=status, min_confidence=min_confidence
    )
    return [candidate_row(row) for row in rows]


@router.get(
    "/{group_id}/candidates/{candidate_id}", response_model=CandidateDetailResponse
)
def get_candidate(
    candidate_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """One candidate with its full provenance (B-043): every
    field's quote, chunk, and support verdict — plus the
    cross-source matches (B-075) the queue renders as
    "likely the same event" with the side-by-side merge
    action (scored server-side; the machine proposes, the
    person merges)."""
    from gpa.services import matching as matching_service

    candidate = candidate_service.get_candidate(db, context.group, candidate_id)
    return candidate_detail(
        candidate,
        candidate_service.provenance_of(db, candidate),
        matches=matching_service.match_candidate(db, context.group, candidate),
    )


@router.patch(
    "/{group_id}/candidates/{candidate_id}", response_model=CandidateResponse
)
def edit_candidate(
    candidate_id: str,
    body: CandidateEditRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Edit a draft (organiser+). Status becomes "edited"; flags
    are recomputed by the API, never taken from the client."""
    candidate = candidate_service.edit_candidate(
        db, actor_label=principal.label, group=context.group,
        candidate_id=candidate_id, draft=body.draft,
    )
    return candidate_row(candidate)


@router.post(
    "/{group_id}/candidates/{candidate_id}/reject", response_model=CandidateResponse
)
def reject_candidate(
    candidate_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Reject a draft (organiser+). The draft and its provenance
    stay — rejection is a decision, not an erasure."""
    candidate = candidate_service.reject_candidate(
        db, actor_label=principal.label, group=context.group,
        candidate_id=candidate_id,
    )
    return candidate_row(candidate)


@router.post(
    "/{group_id}/candidates/{candidate_id}/merge", response_model=CandidateResponse
)
def merge_candidate(
    candidate_id: str,
    body: CandidateMergeRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Merge this candidate into the named survivor (organiser+).
    Returns the survivor; the merged row keeps its draft and
    provenance under its new status."""
    survivor = candidate_service.merge_candidates(
        db, actor_label=principal.label, group=context.group,
        candidate_id=candidate_id, into_candidate_id=body.into_candidate_id,
    )
    return candidate_row(survivor)


@router.post(
    "/{group_id}/candidates/{candidate_id}/merge-event",
    response_model=MergeEventResponse,
)
def merge_candidate_into_event(
    candidate_id: str,
    body: MergeEventRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Merge this candidate into an existing event (B-076):
    the field-level merge — better-sourced values win, all
    sources link to the event, and a genuine disagreement the
    merge cannot settle becomes a contradiction on the event
    (B-079). This is the merge action beside the queue's
    "likely the same event" proposal (B-075)."""
    from gpa.schemas.events import project_event
    from gpa.services import calendar_views
    from gpa.services import merging as merging_service

    result = merging_service.merge_candidate_into_event(
        db, actor_label=principal.label, group=context.group,
        candidate_id=candidate_id, event_id=body.event_id,
    )
    event = result["event"]
    return MergeEventResponse(
        event=project_event(
            event, "organiser", flyer=calendar_views.flyer_view(db, event)
        ),
        filled_fields=result["filled"],
        applied_fields=result["applied"],
        agreed_fields=result["agreed"],
        kept_fields=result["kept"],
        skipped_fields=result["skipped"],
        contradiction_ids=[row.id for row in result["contradictions"]],
    )


@router.post(
    "/{group_id}/sources/{source_id}/extract",
    response_model=ExtractAcceptedResponse,
    status_code=202,
)
def request_extraction(
    source_id: str,
    body: ExtractRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Ask for an extraction run over one source (B-045): a
    durable job is queued (202 + its id); the candidates land in
    this queue when the worker runs it."""
    job = extraction_service.request_extraction(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        source_id=source_id, kinds=body.kinds,
    )
    return ExtractAcceptedResponse(
        job_id=job.id, source_id=source_id, kinds=body.kinds or extraction_service.ALL_KINDS
    )


@router.post(
    "/{group_id}/sources/{source_id}/read",
    response_model=VisionReadAcceptedResponse,
    status_code=202,
)
def request_vision_read(
    source_id: str,
    request: Request,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Ask for a vision reading of one flyer/board source
    (Phase 5, B-063/B-064/B-069): a durable job is queued
    (202 + its id). A board is segmented and each poster read;
    the drafts land in this queue when the worker runs it, and
    the job's progress says how far through the posters it is."""
    job = vision_service.request_vision_read(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        source_id=source_id,
    )
    return VisionReadAcceptedResponse(job_id=job.id, source_id=source_id)
