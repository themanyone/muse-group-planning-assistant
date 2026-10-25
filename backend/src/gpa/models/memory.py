"""Memory tables (roadmap Phase 10, B-113/B-115; Doc 04 §12).

The group's past, made answerable — and accountable. Two tables:

- ``Summary`` — everything the memory engine *says*: an ask
  answer (kind ``ask``), a discussion summary (``discussion``),
  an event briefing (``event_brief``), a post-event recap
  (``recap``), a newsletter draft (``newsletter``). Every row
  carries its **source range** (``range_json``: the chunk ids it
  was built from, and the prune run that chose them) — a summary
  without its source range is an unaccountable one and must
  never exist (B-120). Drafts (recap / newsletter / discussion)
  sit at ``status="draft"`` until a person approves them into
  the group's memory; ask answers and briefings are answers,
  stored ``final`` the moment they are given. An ask answer
  whose citations failed the quote check is stored too — with
  ``verified=False`` and the honest refusal as its body: the
  record of *not knowing* is part of the memory as well.
- ``PruneRun`` — the pruning audit record (B-113): every run of
  Geoffrey's engine writes one, naming its input (chunk count,
  estimated tokens), the chunks it kept and the chunks it
  dropped, the passes it spent, the model that judged, and what
  it cost. Dropping is *visible*, never silent (B-120): a member
  who asks "why didn't the answer mention X?" can be shown the
  run where X was judged and dropped.

Bodies are plain text here; the field-sealing service (Phase
17's crypto module) wraps sensitive bodies when it lands — the
column shape does not change when it does.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc

# The kinds a Summary may carry (roadmap §4 + B-115's ask).
SUMMARY_KINDS: tuple[str, ...] = (
    "ask",
    "discussion",
    "event_brief",
    "recap",
    "newsletter",
)

# Lifecycle: drafts wait for a person; finals are answers.
SUMMARY_STATUSES: tuple[str, ...] = ("final", "draft", "approved")


class Summary(Base):
    """One thing the memory engine said, with its receipts."""

    __tablename__ = "summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    # final (an answer given) | draft (awaits a person) |
    # approved (a draft a person let into the group's memory).
    status: Mapped[str] = mapped_column(String(16), default="final", index=True)
    # Ask answers only: did every citation survive the quote
    # check (B-040's rule)? A False here means the body IS the
    # honest refusal, and the citations (if any) are labelled
    # unverified wherever they are shown.
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    # For kind="ask": the question the member asked, verbatim.
    question: Mapped[str] = mapped_column(Text, default="")
    # Citations as JSON: [{"quote", "chunk_id", "position"}] —
    # each quote was found, whitespace-normalised, in the chunk
    # it names before the answer was given (or the answer is the
    # refusal and this list is what little was found, unverified).
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    # The source range as JSON: {"chunk_ids": [...],
    # "prune_run_id": ...} — the material this summary was
    # built from. Never empty for a stored summary (B-120).
    range_json: Mapped[str] = mapped_column(Text, default="{}")
    # The event a briefing/recap is about, when it is about one.
    # Plain string, the Candidate.source_id discipline: deleting
    # an event must not cascade-delete the group's memory of it.
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class PruneRun(Base):
    """One run of the pruning engine, fully accounted (B-113).

    ``kept_json`` / ``dropped_json`` hold chunk ids in corpus
    order — source chunk ids verbatim, settled-truth chunks as
    ``decision:<id>`` / ``summary:<id>`` (the corpus builder's
    naming, services/memory.py). A group may purge its runs
    (B-120); the summaries a run produced keep their own
    citations, so purging the audit never unmakes an answer.
    """

    __tablename__ = "prune_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    # What the run was for: "ask" | "brief" | "prune" (a bare
    # engine run, e.g. the evaluation harness's measurement).
    purpose: Mapped[str] = mapped_column(String(24), default="prune", index=True)
    # The question (or the briefing's terms) the run judged
    # relevance against, verbatim.
    question: Mapped[str] = mapped_column(Text, default="")
    input_chunks: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    kept_json: Mapped[str] = mapped_column(Text, default="[]")
    dropped_json: Mapped[str] = mapped_column(Text, default="[]")
    passes: Mapped[int] = mapped_column(Integer, default=1)
    model_id: Mapped[str] = mapped_column(String(120), default="")
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
