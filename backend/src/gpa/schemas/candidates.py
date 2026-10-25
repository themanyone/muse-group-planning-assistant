"""Candidate / review-queue schemas (roadmap B-043, B-047).

The queue is the frontend's most important screen, so the shapes
here are the contract it is built against: the list row carries
everything a reviewer needs to triage (draft, confidence, flags,
prompt provenance); the detail adds the full per-field provenance
— every field's quote, chunk, and support verdict (B-040).
"""

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from gpa.schemas.events import EventResponse

if TYPE_CHECKING:  # annotations only
    from gpa.models.groups import Candidate


class ProvenanceEntry(BaseModel):
    """One field's evidence: the quote, its chunk, and whether the
    pipeline verified it (an unsupported field is shown, never
    hidden — that is the queue's whole point). For a picture's
    fields the evidence is a REGION instead (B-063): the box
    [x, y, w, h] the field was read from, so the queue can
    highlight the rectangle on the poster."""

    field: str
    chunk_id: str | None = None
    chunk_position: int | None = None
    quote: str = ""
    region: list[float] | None = None
    supported: bool = False
    sent_at: str | None = None


class MatchView(BaseModel):
    """One scored match of this candidate against an existing
    event (B-075): the event's face, the total score, the
    per-component scores (title / time / place / host), and the
    ``likely`` verdict — at or above the threshold the queue
    shows "likely the same event" beside the merge action."""

    event_id: str
    event_title: str = ""
    event_starts_at: str = ""
    event_place_name: str = ""
    event_host_label: str = ""
    event_status: str = ""
    score: float = 0.0
    likely: bool = False
    components: dict = Field(default_factory=dict)


class CandidateDetailResponse(BaseModel):
    """The full candidate: the list row plus provenance rows."""

    id: str
    group_id: str
    kind: str
    title: str
    status: str
    created_by: str
    approved_by: str | None = None
    draft: dict = Field(default_factory=dict)
    confidence: float | None = None
    flags: list[str] = Field(default_factory=list)
    source_id: str | None = None
    prompt_name: str = ""
    prompt_version: int | None = None
    job_id: str | None = None
    merged_into_id: str | None = None
    # The published entity approval minted — NULL until Phase 4
    # (B-049) exists; approval records the decision only (B-043's
    # honest boundary, stated in FRONTEND_HANDOFF.md too).
    resulting_entity_type: str | None = None
    resulting_entity_id: str | None = None
    provenance: list[ProvenanceEntry] = Field(default_factory=list)
    # Cross-source matches against the group's existing events
    # (B-075), best first; only meaningful for event-kind
    # candidates in an open state — empty otherwise.
    matches: list[MatchView] = Field(default_factory=list)


class CandidateEditRequest(BaseModel):
    """A reviewer's edit of the draft (organiser+). The draft is
    the same JSON shape the pipeline wrote; the reviewer replaces
    field values, and the API recomputes flags afterwards (B-044
    — flags are the API's to decide, never the client's)."""

    draft: dict


class CandidateMergeRequest(BaseModel):
    """Merge this candidate INTO the named survivor."""

    into_candidate_id: str


class MergeEventRequest(BaseModel):
    """Merge this candidate into the named existing EVENT
    (B-076): the field-level merge — better-sourced values
    win, provenance becomes the event's links."""

    event_id: str


class MergeEventResponse(BaseModel):
    """What a merge did, field by field (B-076): the event as
    it now stands, which fields were filled / applied (won) /
    agreed without moving / kept (the event's value stood),
    which were skipped as unsupported or unresolved, and the
    contradictions the merge opened (B-079)."""

    event: "EventResponse"
    filled_fields: list[str] = Field(default_factory=list)
    applied_fields: list[str] = Field(default_factory=list)
    agreed_fields: list[str] = Field(default_factory=list)
    kept_fields: list[str] = Field(default_factory=list)
    skipped_fields: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)


class ExtractRequest(BaseModel):
    """Ask for an extraction run over one source (B-045). Kinds
    default to all four; a subset re-runs just those kinds."""

    kinds: list[str] | None = None


class ExtractAcceptedResponse(BaseModel):
    """202: the run is a job; poll the job or watch the stream."""

    job_id: str
    source_id: str
    kinds: list[str]


class QueueStatusResponse(BaseModel):
    """The queue's plain-words state (B-047): how much waits, and
    — when the group's daily AI budget is spent — that extraction
    is "paused — budget", said exactly that plainly."""

    pending_count: int
    paused: bool
    pause_reason: str | None = None
    spent_today_micros: int
    budget_micros: int | None = None


def candidate_row(candidate: "Candidate"):
    """The list-row shape (schemas.groups.CandidateResponse) —
    one builder so the queue's rows agree everywhere they are
    served from."""
    from gpa.schemas.groups import CandidateResponse

    try:
        draft = json.loads(candidate.draft_json or "{}")
    except json.JSONDecodeError:
        draft = {}
    try:
        flags = json.loads(candidate.flags_json or "[]")
    except json.JSONDecodeError:
        flags = []
    return CandidateResponse(
        id=candidate.id,
        group_id=candidate.group_id,
        kind=candidate.kind,
        title=candidate.title,
        status=candidate.status,
        created_by=candidate.created_by,
        approved_by=candidate.approved_by,
        draft=draft if isinstance(draft, dict) else {},
        confidence=candidate.confidence,
        flags=flags if isinstance(flags, list) else [],
        source_id=candidate.source_id,
        prompt_name=candidate.prompt_name,
        prompt_version=candidate.prompt_version,
        job_id=candidate.job_id,
        merged_into_id=candidate.merged_into_id,
        resulting_entity_type=candidate.resulting_entity_type,
        resulting_entity_id=candidate.resulting_entity_id,
    )


def candidate_detail(
    candidate: "Candidate",
    provenance: list[dict],
    matches: list[dict] | None = None,
) -> CandidateDetailResponse:
    """Build the detail response from a candidate row + its
    provenance dicts (the service's shape), with the
    cross-source matches (B-075) when the caller computed
    them."""
    try:
        draft = json.loads(candidate.draft_json or "{}")
    except json.JSONDecodeError:
        draft = {}
    try:
        flags = json.loads(candidate.flags_json or "[]")
    except json.JSONDecodeError:
        flags = []
    return CandidateDetailResponse(
        id=candidate.id,
        group_id=candidate.group_id,
        kind=candidate.kind,
        title=candidate.title,
        status=candidate.status,
        created_by=candidate.created_by,
        approved_by=candidate.approved_by,
        draft=draft if isinstance(draft, dict) else {},
        confidence=candidate.confidence,
        flags=flags if isinstance(flags, list) else [],
        source_id=candidate.source_id,
        prompt_name=candidate.prompt_name,
        prompt_version=candidate.prompt_version,
        job_id=candidate.job_id,
        merged_into_id=candidate.merged_into_id,
        resulting_entity_type=candidate.resulting_entity_type,
        resulting_entity_id=candidate.resulting_entity_id,
        provenance=[ProvenanceEntry(**row) for row in provenance],
        matches=[MatchView(**row) for row in (matches or [])],
    )
