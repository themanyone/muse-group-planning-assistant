"""Memory wire shapes (roadmap Phase 10, B-115 → B-120).

Ask answers, summaries, and prune runs. The shapes carry the
phase's honesty rules on their face: an ask response says
whether it verified; a citation names the chunk its quote
provably came from (or nulls, when the answer is the refusal
and the found material is labelled unverified); a prune run
names what was kept AND what was dropped.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """A member's question to the group's approved history."""

    question: str = Field(min_length=1, max_length=2000)


class CitationResponse(BaseModel):
    """One claim's receipt. ``chunk_id``/``position`` are null
    only on an unverified answer, where the quote is shown as
    found material, never as proof."""

    quote: str
    chunk_id: str | None
    position: int | None


class AskResponse(BaseModel):
    """The ask answer (B-115). ``verified`` False means
    ``answer`` IS the honest refusal — its first words are
    "I couldn't verify this" — and its citations are labelled
    unverified by their null chunk ids."""

    answer: str
    verified: bool
    citations: list[CitationResponse]
    summary_id: str
    prune_run_id: str


class SummaryResponse(BaseModel):
    """One thing the memory engine said, with its receipts."""

    id: str
    group_id: str
    kind: str
    title: str
    body: str
    status: str
    verified: bool
    question: str
    citations: list[CitationResponse]
    source_range: dict
    event_id: str | None
    created_by: str
    created_at: datetime


class PruneRunResponse(BaseModel):
    """One pruning run's full account (B-120): input, kept,
    dropped, passes, model, cost — dropping is visible."""

    id: str
    group_id: str
    purpose: str
    question: str
    input_chunks: int
    input_tokens: int
    kept_chunk_ids: list[str]
    dropped_chunk_ids: list[str]
    passes: int
    model_id: str
    cost_micros: int
    created_by: str
    created_at: datetime


class SummariseRequest(BaseModel):
    """Which thread to summarise (B-116): the discussion
    parents the thread system knows (B-084)."""

    parent_type: str = Field(pattern="^(event|task|decision|poll)$")
    parent_id: str = Field(min_length=1)


class SummariseResponse(BaseModel):
    """A discussion summary and the candidates it proposed
    (B-116): the summary is a draft; the candidates wait in the
    review queue — the queue disposes."""

    summary: SummaryResponse
    candidate_ids: list[str]


class NewsletterDraftRequest(BaseModel):
    """The month whose approved recaps become a newsletter
    draft (B-118)."""

    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class BriefingResponse(BaseModel):
    """A host briefing (B-117): the woven text, the questions
    the history leaves open (the model's list), and the role
    gaps (the data's list — unclaimed slots, counted, not
    imagined)."""

    summary: SummaryResponse
    open_questions: list[str]
    role_gaps: list[str]
    prune_run_id: str


class RecapResponse(BaseModel):
    """A recap draft (B-118) — a draft, always: it becomes
    memory only through approval."""

    summary: SummaryResponse
