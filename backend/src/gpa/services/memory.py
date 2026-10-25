"""Group memory (roadmap Phase 10, B-113 → B-122; Doc 04 §12).

The service layer over Geoffrey's pruning engine
(``gpa.ai.pruning``). What lives here:

- **The corpus** — the group's *approved history*, and nothing
  else: source chunks (material a member brought in under a
  permission record), the group's CURRENT decisions (settled
  truth), and summaries a person approved into the memory.
  Pending drafts and rejected candidates are **not** the group's
  past and never enter the corpus — an answer may only ever
  cite what the group actually said or settled.
- **Runs** — every engine run is written down (``PruneRun``:
  input, kept, dropped, passes, model, cost) and every model
  call is ledgered (``AiUsage``, B-047) inside the same
  transaction, committed before any answer is given.
- **The doors built on the engine** — ask (B-115), discussion
  summaries (B-116), briefings (B-117), recaps and the
  newsletter loop (B-118) — each its own function below, each
  storing a ``Summary`` with its source range, so nothing the
  engine says is ever unsourced (B-120).

The engine itself stays pure (ai/pruning.py); the honesty rules
live here and in the prompts: citations are verified against
the kept chunks before an answer is returned, drafts go to the
review queue or sit as draft summaries — the engine never
writes a decision, a task, or a sent message.
"""

import json

from sqlalchemy.orm import Session

from gpa.ai import client as ai_client
from gpa.ai import pruning
from gpa.ai.client import estimate_tokens
from gpa.config import Settings, get_settings
from gpa.models.groups import Group
from gpa.models.memory import PruneRun, Summary
from gpa.models.planning import Decision
from gpa.models.sources import Source
from gpa.services import audit
from gpa.services import sources as source_service
from gpa.services import usage as usage_service

# Machine authors, in the extraction service's vocabulary: a
# machine's words are never attributed to a person.
ASK_AUTHOR = "system:memory"


def approved_corpus(db: Session, group: Group) -> list[pruning.MemoryChunk]:
    """The group's approved history as ordered memory chunks.

    Order is the corpus's own chronology: each chunk's moment
    (a message's sent-at, a decision's making, a summary's
    approval-era creation), ties broken by build order; the
    positions are then assigned 0..N-1 in that order, so a
    citation's position is stable for the whole run.
    """
    staged: list[tuple[object, pruning.MemoryChunk]] = []

    sources = (
        db.query(Source)
        .filter(Source.group_id == group.id)
        .order_by(Source.created_at, Source.id)
        .all()
    )
    for source in sources:
        for chunk in source_service.list_chunks(db, source):
            staged.append(
                (
                    chunk.sent_at or source.created_at,
                    pruning.MemoryChunk(
                        chunk_id=chunk.id,
                        position=0,  # assigned after the sort
                        text=chunk.text,
                        sender=chunk.sender,
                        sent_at=chunk.sent_at,
                        origin="source",
                    ),
                )
            )

    # Settled truth weighs first-class: a CURRENT decision is
    # the group's past in its most distilled form. Superseded
    # decisions are history ABOUT the truth, not the truth —
    # they stay out (the source that superseded them speaks).
    decisions = (
        db.query(Decision)
        .filter(Decision.group_id == group.id, Decision.status == "current")
        .order_by(Decision.created_at, Decision.id)
        .all()
    )
    for decision in decisions:
        staged.append(
            (
                decision.created_at,
                pruning.MemoryChunk(
                    chunk_id=f"decision:{decision.id}",
                    position=0,
                    text=decision.text,
                    sender="the group (decision)",
                    sent_at=decision.created_at,
                    origin="decision",
                ),
            )
        )

    # Summaries a person APPROVED into the memory — the group's
    # own digested past (a recap of last year's fair is exactly
    # what this year's briefing should find). Drafts are not
    # memory yet; ask answers are answers, not history.
    approved = (
        db.query(Summary)
        .filter(
            Summary.group_id == group.id,
            Summary.status == "approved",
            Summary.kind.in_(("discussion", "recap")),
        )
        .order_by(Summary.created_at, Summary.id)
        .all()
    )
    for summary in approved:
        staged.append(
            (
                summary.created_at,
                pruning.MemoryChunk(
                    chunk_id=f"summary:{summary.id}",
                    position=0,
                    text=summary.body,
                    sender="the group (approved summary)",
                    sent_at=summary.created_at,
                    origin="summary",
                ),
            )
        )

    staged.sort(key=lambda pair: (pair[0] is None, pair[0]))
    corpus: list[pruning.MemoryChunk] = []
    for position, (_moment, chunk) in enumerate(staged):
        chunk.position = position
        corpus.append(chunk)
    return corpus


def resolve_client(settings: Settings | None, group: Group):
    """The engine's judge for this group (B-119): the per-group
    ``prune_driver`` setting is the switch — ``local`` builds
    the local-inference driver (the group's own hardware,
    Doc 07 §7.5) for THIS group's memory work, while
    ``hosted`` — and every other AI caller in the process,
    extraction included — keeps the process's configured
    driver. Swapping stays a configuration choice, never a
    code change (B-035); a group that asks for local without
    the endpoint configured gets the plain-words refusal the
    LocalDriver raises, never a silent fallback."""
    if group is not None and group.prune_driver == "local":
        from gpa.ai.drivers_local import LocalDriver

        return LocalDriver(settings or get_settings())
    return ai_client.get_client()


def _record_usage(db: Session, group: Group):
    """The engine's usage callback: ledger each call in the
    caller's transaction (B-047), stamped with its prompt."""

    def record(result, prompt_name):
        usage_service.record_usage(
            db, result=result, group_id=group.id,
            operation=f"prune:{prompt_name}", prompt_name=prompt_name,
            prompt_version=None,
        )

    return record


def write_prune_run(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    question: str,
    purpose: str,
    corpus: list[pruning.MemoryChunk],
    outcome: pruning.PruneOutcome,
) -> PruneRun:
    """Persist one run's account (B-113). NO commit — the
    caller's transaction owns it, so a run record can never
    outlive the answer it accounted for (nor the reverse)."""
    row = PruneRun(
        group_id=group.id,
        purpose=purpose,
        question=question,
        input_chunks=len(corpus),
        input_tokens=sum(estimate_tokens(c.text) for c in corpus),
        kept_json=json.dumps([c.chunk_id for c in outcome.kept]),
        dropped_json=json.dumps([c.chunk_id for c in outcome.dropped]),
        passes=outcome.passes,
        model_id=outcome.model_id,
        cost_micros=outcome.cost_micros,
        created_by=actor_label,
    )
    db.add(row)
    db.flush()  # the id exists before a summary's range names it
    return row


def run_prune(
    db: Session,
    settings: Settings | None = None,
    *,
    actor_label: str,
    group: Group,
    question: str,
    purpose: str = "prune",
    corpus: list[pruning.MemoryChunk] | None = None,
    client=None,
    max_passes: int | None = None,
    token_budget: int | None = None,
) -> tuple[PruneRun, pruning.PruneOutcome, list[pruning.MemoryChunk]]:
    """Run the engine over the group's corpus and write the
    run down. Commits before returning (commit-before-answer:
    the record exists before anyone acts on the outcome).

    Pass cap and budget default to the group's settings-pack
    dials (B-114); the engine enforces its own harder ceiling
    whatever it is handed.
    """
    settings = settings or get_settings()
    corpus = corpus if corpus is not None else approved_corpus(db, group)
    client = client if client is not None else resolve_client(settings, group)
    outcome = pruning.prune_multi(
        client,
        question=question,
        chunks=corpus,
        max_passes=group.prune_max_passes if max_passes is None else max_passes,
        token_budget=group.prune_token_budget if token_budget is None else token_budget,
        record_usage=_record_usage(db, group),
    )
    row = write_prune_run(
        db, actor_label=actor_label, group=group, question=question,
        purpose=purpose, corpus=corpus, outcome=outcome,
    )
    audit.record(
        db, actor=actor_label, action="memory.prune_run",
        group_id=group.id, entity_type="prune_run", entity_id=row.id,
        detail={
            "purpose": purpose,
            "input_chunks": row.input_chunks,
            "kept": len(outcome.kept),
            "dropped": len(outcome.dropped),
            "passes": outcome.passes,
            "budget_stopped": outcome.budget_stopped,
        },
    )
    db.commit()
    return row, outcome, corpus


# --- Ask (B-115) ---------------------------------------------------

# The refusal's first words, verbatim — the frontend and the
# tests both lean on them, and the prompt (ask_memory.md) uses
# the same sentence, so the model's honest answer and the
# server's enforced one read identically.
UNVERIFIED_LEAD = "I couldn't verify this"


def _draft_answer(db, group, client, *, question, kept):
    """Draft an answer FROM THE SURVIVORS ONLY, via the
    ask_memory prompt. Returns (answer_text, [quotes],
    cost_micros) — the run record owes the whole ask's cost,
    drafts included, not just the pruning's.

    The shape is checked like every pipeline's (one repair
    retry, then ModelError): an answer that does not parse is
    not an answer anyone should act on."""
    from gpa.ai import promptlib
    from gpa.ai.client import ModelError

    template = promptlib.load_prompt("ask_memory")
    schema = template.schema or {}
    prompt = template.render(
        QUESTION=question, CHUNKS=pruning.render_chunks(kept)
    )
    cost = 0
    result = client.complete_json(prompt=prompt, schema=schema, tier="wonder")
    cost += result.cost_micros
    usage_service.record_usage(
        db, result=result, group_id=group.id, operation="ask:ask_memory",
        prompt_name="ask_memory", prompt_version=template.version,
    )
    error = _ask_shape_error(result.data)
    if error is not None:
        repair = (
            prompt
            + f"\n\nYour previous answer was not valid: {error}. "
            "Return ONLY valid JSON in the required shape."
        )
        result = client.complete_json(prompt=repair, schema=schema, tier="wonder")
        cost += result.cost_micros
        usage_service.record_usage(
            db, result=result, group_id=group.id, operation="ask:ask_memory",
            prompt_name="ask_memory", prompt_version=template.version,
        )
        error = _ask_shape_error(result.data)
        if error is not None:
            raise ModelError(
                f"Ask produced malformed output twice ({error}); "
                "refusing to answer from it."
            )
    quotes = [
        str(c.get("quote", ""))
        for c in result.data.get("citations", [])
        if isinstance(c, dict)
    ]
    return str(result.data.get("answer", "")), quotes, cost


def _ask_shape_error(data: object) -> str | None:
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        return "the answer must be an object with an 'answer' string"
    citations = data.get("citations", [])
    if not isinstance(citations, list):
        return "'citations' must be a list"
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict) or not isinstance(
            citation.get("quote"), str
        ):
            return f"citation {index} must be an object with a 'quote' string"
    return None


def _verify(quotes: list[str], kept) -> list[dict] | None:
    """Verify every cited quote against the kept chunks
    (B-040's rule, via ai/pruning.find_quote). Returns the
    citation list for the wire — quote + the chunk it provably
    came from — or None when ANY quote fails, or there are no
    citations at all: an uncited answer is an unverifiable one
    by construction."""
    if not quotes:
        return None
    citations: list[dict] = []
    for quote in quotes:
        match = pruning.find_quote(quote, kept)
        if match is None:
            return None
        citations.append(
            {"quote": quote, "chunk_id": match.chunk_id, "position": match.position}
        )
    return citations


def ask_question(
    db: Session,
    settings: Settings | None = None,
    *,
    actor_label: str,
    group: Group,
    question: str,
    client=None,
) -> dict:
    """Ask the group's approved history a question (B-115).

    prune → draft from the survivors → verify every citation →
    (if the draft has gaps and passes remain) prune again
    against the gaps and re-draft → answer WITH citations, or
    the honest refusal. Either way the run and the answer are
    stored (PruneRun + Summary kind ``ask``) and committed
    before this returns — commit-before-answer.
    """
    settings = settings or get_settings()
    client = client if client is not None else resolve_client(settings, group)
    corpus = approved_corpus(db, group)
    record = _record_usage(db, group)
    budget = group.prune_token_budget

    outcome = pruning.prune_once(
        client, question=question, chunks=corpus,
        token_budget=budget, record_usage=record,
    )
    kept, dropped = outcome.kept, outcome.dropped
    passes = outcome.passes
    total_cost = outcome.cost_micros
    model_id = outcome.model_id

    answer_text = ""
    quotes: list[str] = []
    citations: list[dict] | None = None
    if kept:
        answer_text, quotes, draft_cost = _draft_answer(
            db, group, client, question=question, kept=kept
        )
        total_cost += draft_cost
        citations = _verify(quotes, kept)

    # The gap pass (Doc 07 §7.2): the draft could not verify —
    # its failed claims ARE the gaps. Re-prune the dropped set
    # against them, re-draft from the enlarged survivors, and
    # verify once more. Pass and budget caps bind here exactly
    # as they bind the bare engine (B-114).
    if citations is None and kept and passes < group.prune_max_passes and dropped:
        gaps = (
            "The draft answer could not be verified. Draft: "
            + (answer_text or "(no draft)")
            + " — its claims lack surviving evidence."
        )
        left = budget - outcome.input_tokens if budget is not None else None
        rescue = pruning.reconsider_dropped(
            client, question=question, kept=kept, dropped=dropped,
            gaps=gaps, token_budget=left, record_usage=record,
        )
        passes += rescue.passes
        total_cost += rescue.cost_micros
        model_id = rescue.model_id or model_id
        if len(rescue.kept) > len(kept):
            kept, dropped = rescue.kept, rescue.dropped
            answer_text, quotes, draft_cost = _draft_answer(
                db, group, client, question=question, kept=kept
            )
            total_cost += draft_cost
            citations = _verify(quotes, kept)

    verified = citations is not None
    if verified:
        body = answer_text
        wire_citations = citations
    else:
        # The honest refusal (B-115's acceptance behaviour):
        # say what could not be verified, then show what WAS
        # found, labelled unverified — never dressed as fact.
        if kept and answer_text:
            body = (
                f"{UNVERIFIED_LEAD} — the group's history does not "
                "settle this with evidence I can cite.\n\n"
                f"What was found, unverified: {answer_text}"
            )
            wire_citations = [
                {"quote": q, "chunk_id": None, "position": None} for q in quotes
            ]
        elif kept:
            body = (
                f"{UNVERIFIED_LEAD} — nothing the survivors say "
                "settles this question."
            )
            wire_citations = []
        else:
            body = (
                f"{UNVERIFIED_LEAD} — nothing in the group's "
                "approved history speaks to it."
            )
            wire_citations = []

    final = pruning.PruneOutcome(
        kept=kept, dropped=dropped, passes=passes,
        model_id=model_id, cost_micros=total_cost,
        input_tokens=outcome.input_tokens,
        budget_stopped=outcome.budget_stopped,
    )
    run = write_prune_run(
        db, actor_label=actor_label, group=group, question=question,
        purpose="ask", corpus=corpus, outcome=final,
    )
    summary = Summary(
        group_id=group.id,
        kind="ask",
        title=question[:240],
        body=body,
        status="final",
        verified=verified,
        question=question,
        citations_json=json.dumps(wire_citations),
        range_json=json.dumps(
            {
                "chunk_ids": [c.chunk_id for c in kept],
                "prune_run_id": run.id,
            }
        ),
        created_by=actor_label,
    )
    db.add(summary)
    db.flush()
    audit.record(
        db, actor=actor_label, action="memory.ask",
        group_id=group.id, entity_type="summary", entity_id=summary.id,
        detail={
            "prune_run_id": run.id, "verified": verified,
            "kept": len(kept), "passes": passes,
        },
    )
    db.commit()
    return {
        "answer": body,
        "verified": verified,
        "citations": wire_citations,
        "summary_id": summary.id,
        "prune_run_id": run.id,
    }


def list_summaries(
    db: Session, group: Group, *, kind: str | None = None
) -> list[Summary]:
    """The group's summaries, newest first, optionally one kind."""
    query = db.query(Summary).filter(Summary.group_id == group.id)
    if kind is not None:
        query = query.filter(Summary.kind == kind)
    return query.order_by(Summary.created_at.desc(), Summary.id.desc()).all()


def list_runs(db: Session, group: Group) -> list[PruneRun]:
    """Every prune/ask/brief run the engine has made for this
    group, newest first (B-120) — the transparency ledger: what
    was judged, what survived, what it cost."""
    return (
        db.query(PruneRun)
        .filter(PruneRun.group_id == group.id)
        .order_by(PruneRun.created_at.desc(), PruneRun.id.desc())
        .all()
    )


def purge_run(
    db: Session, *, actor_label: str, group: Group, run_id: str
) -> None:
    """Delete ONE run record (B-120). The run ledger is the
    group's to inspect and to clear; the SUMMARIES the runs
    produced are the group's words and are never deleted
    here — purging the receipts does not unwrite the answers
    (their source ranges keep the chunk ids, which name
    sources and decisions that still exist)."""
    from gpa.errors import AppError

    run = (
        db.query(PruneRun)
        .filter(PruneRun.id == run_id, PruneRun.group_id == group.id)
        .first()
    )
    if run is None:
        raise AppError("not_found")
    db.delete(run)
    audit.record(
        db, actor=actor_label, action="memory.run_purged",
        group_id=group.id, entity_type="prune_run", entity_id=run_id,
        detail={"purpose": run.purpose},
    )
    db.commit()


def purge_all_runs(db: Session, *, actor_label: str, group: Group) -> int:
    """Delete ALL of the group's run records (B-120); returns
    how many went. Summaries stay — see purge_run."""
    runs = db.query(PruneRun).filter(PruneRun.group_id == group.id).all()
    count = len(runs)
    for run in runs:
        db.delete(run)
    audit.record(
        db, actor=actor_label, action="memory.runs_purged",
        group_id=group.id, entity_type="prune_run",
        detail={"count": count},
    )
    db.commit()
    return count


def get_summary(db: Session, group: Group, summary_id: str) -> Summary:
    """One of the group's summaries, or the platform's
    missing-object answer — another group's summary is
    indistinguishable from no summary (the privacy grain)."""
    from gpa.errors import AppError

    row = (
        db.query(Summary)
        .filter(Summary.id == summary_id, Summary.group_id == group.id)
        .first()
    )
    if row is None:
        raise AppError("not_found")
    return row


# --- Discussion summaries (B-116) ----------------------------------

SUMMARY_AUTHOR = "system:summary"


def summarise_thread(
    db: Session,
    settings: Settings | None = None,
    *,
    actor_label: str,
    group: Group,
    parent_type: str,
    parent_id: str,
    viewer_role: str | None,
    client=None,
) -> dict:
    """Summarise one thread — and let the summary PROPOSE, never
    dispose (B-116).

    The thread's live comments (hidden and deleted words are
    never summarised into the group's memory — moderation hid
    them for a reason) become the summary prompt's chunks. The
    summary itself is stored as a draft Summary. Then the
    decisions and next actions the summary NAMES are extracted
    — by the ordinary extraction pipeline, over the thread's
    own words, so every candidate's fields carry quotes that
    verify against what people actually said — and land in the
    review queue as candidates (``created_by=system:summary``,
    flagged ``from_summary``). A summary never writes a
    Decision or a Task: the queue disposes.
    """
    from gpa.ai import extraction as pipeline
    from gpa.ai import promptlib
    from gpa.ai.client import ModelError
    from gpa.errors import AppError
    from gpa.models.groups import Candidate
    from gpa.services import discussion as discussion_service
    from gpa.services.quality import SUBJECT_FIELD

    settings = settings or get_settings()
    client = client if client is not None else resolve_client(settings, group)

    comments = discussion_service.list_comments(
        db, group=group, parent_type=parent_type, parent_id=parent_id,
        viewer_role=viewer_role,
    )
    # Whatever the viewer's role, hidden/deleted words stay out
    # of the summary: list_comments shows them to organisers for
    # MODERATION, and moderation material is not memory.
    live = [c for c in comments if c.hidden_at is None and c.deleted_at is None]
    if not live:
        raise AppError(
            "validation_error",
            detail="That thread has no discussion to summarise yet.",
        )
    chunks = [
        pruning.MemoryChunk(
            chunk_id=f"comment:{comment.id}",
            position=index,
            text=comment.body,
            sender=comment.author_label,
            sent_at=comment.created_at,
            origin="comment",
        )
        for index, comment in enumerate(live)
    ]

    template = promptlib.load_prompt("summary")
    schema = template.schema or {}
    prompt = template.render(CHUNKS=pruning.render_chunks(chunks))
    result = client.complete_json(prompt=prompt, schema=schema, tier="wonder")
    usage_service.record_usage(
        db, result=result, group_id=group.id, operation="summary:summary",
        prompt_name="summary", prompt_version=template.version,
    )
    if not isinstance(result.data, dict) or not isinstance(
        result.data.get("text"), str
    ):
        raise ModelError(
            "The summary answer was malformed (no 'text'); refusing to store it."
        )
    text = result.data["text"]

    summary = Summary(
        group_id=group.id,
        kind="discussion",
        title=f"Discussion summary — {parent_type}",
        body=text,
        status="draft",
        verified=True,
        citations_json="[]",
        range_json=json.dumps(
            {
                "chunk_ids": [c.chunk_id for c in chunks],
                "parent_type": parent_type,
                "parent_id": parent_id,
            }
        ),
        created_by=actor_label,
    )
    db.add(summary)
    db.flush()

    # Extraction over the thread's own words. A candidate is
    # proposed only when the summary NAMES its subject — the
    # summary proposes, extraction supplies the receipts, the
    # queue disposes.
    def record(result, prompt_name):
        usage_service.record_usage(
            db, result=result, group_id=group.id,
            operation=f"summary:{prompt_name}", prompt_name=prompt_name,
            prompt_version=None,
        )

    normalised_summary = " ".join(text.lower().split())
    created: list[Candidate] = []
    for kind in ("decision", "task"):
        extracted = pipeline.extract_kind(
            client, kind=kind, chunks=chunks,  # type: ignore[arg-type]
            group_timezone=group.home_timezone or "UTC",
            source_created_at=live[0].created_at, record_usage=record,
        )
        subject_field = SUBJECT_FIELD.get(kind, "title")
        for item in extracted:
            subject = (
                item.draft.get("fields", {}).get(subject_field, {}).get("value")
            )
            if not subject:
                continue
            if " ".join(str(subject).lower().split()) not in normalised_summary:
                continue  # extracted, but the summary did not name it
            flags = list(dict.fromkeys(item.flags + ["from_summary"]))
            candidate = Candidate(
                group_id=group.id,
                kind=kind,
                title=str(subject)[:240],
                created_by=SUMMARY_AUTHOR,
                status="pending",
                source_id=None,
                draft_json=json.dumps(item.draft),
                confidence=item.confidence,
                prompt_name=item.prompt_name,
                prompt_version=item.prompt_version,
                job_id=None,
                flags_json=json.dumps(flags),
            )
            db.add(candidate)
            db.flush()
            created.append(candidate)

    audit.record(
        db, actor=actor_label, action="memory.summary_created",
        group_id=group.id, entity_type="summary", entity_id=summary.id,
        detail={
            "parent_type": parent_type, "parent_id": parent_id,
            "candidates": len(created),
        },
    )
    db.commit()
    return {"summary": summary, "candidates": created}


# --- Event briefings (B-117) ---------------------------------------

BRIEF_AUTHOR = "system:memory"


def _event_terms(group: Group, event) -> str:
    """The pruning question for one event's briefing: the
    event's own words, aimed at what a host needs — decisions,
    lessons, promises, warnings."""
    place = f" at {event.place_name}" if event.place_name else ""
    return (
        f"What does the group know that matters for the event "
        f"'{event.title}'{place} — past decisions about it, last "
        f"time's lessons, promises made, and warnings?"
    )


def brief_event(
    db: Session,
    settings: Settings | None = None,
    *,
    actor_label: str,
    group: Group,
    event,
    client=None,
) -> dict:
    """Assemble the host's briefing for one event (B-117).

    Two sources of truth, woven by the briefing prompt:
    the group's STRUCTURED present — past decisions about this
    event, the open questions still hanging, the role slots
    nobody has claimed — and its PRUNED past: the approved
    history judged relevant to the event's own terms. The
    landmine rule is the point: a warning from three months
    ago, phrased nothing like the event, must survive the
    pruning and land in the briefing — that is what the engine
    is FOR (Doc 07 §7.4).
    """
    from gpa.ai import promptlib
    from gpa.ai.client import ModelError
    from gpa.models.planning import OpenQuestion, RoleSlot

    settings = settings or get_settings()
    client = client if client is not None else resolve_client(settings, group)
    corpus = approved_corpus(db, group)
    question = _event_terms(group, event)
    outcome = pruning.prune_once(
        client, question=question, chunks=corpus,
        token_budget=group.prune_token_budget,
        record_usage=_record_usage(db, group),
    )
    run = write_prune_run(
        db, actor_label=actor_label, group=group, question=question,
        purpose="brief", corpus=corpus, outcome=outcome,
    )

    title_words = {
        word.lower()
        for word in "".join(
            ch if ch.isalnum() or ch.isspace() else " " for ch in event.title
        ).split()
        if len(word) >= 4
    }
    past_decisions = [
        d.text
        for d in db.query(Decision)
        .filter(Decision.group_id == group.id, Decision.status == "current")
        .order_by(Decision.created_at)
        .all()
        if d.event_id == event.id
        or any(word in d.text.lower() for word in title_words)
    ][:5]
    last_recap = (
        db.query(Summary)
        .filter(
            Summary.group_id == group.id, Summary.kind == "recap",
            Summary.status == "approved",
        )
        .order_by(Summary.created_at.desc())
        .first()
    )
    open_questions = [
        q.text
        for q in db.query(OpenQuestion)
        .filter(
            OpenQuestion.group_id == group.id, OpenQuestion.status == "open"
        )
        .order_by(OpenQuestion.created_at)
        .all()
        if q.event_id in (None, event.id)
    ][:5]
    role_gaps = [
        slot.role_name
        for slot in db.query(RoleSlot)
        .filter(RoleSlot.event_id == event.id, RoleSlot.claimed_by_id.is_(None))
        .order_by(RoleSlot.created_at)
        .all()
    ]

    event_block_lines = [
        f"{event.title} — {event.starts_at.isoformat()} ({event.timezone})",
    ]
    if event.place_name:
        event_block_lines.append(f"Place: {event.place_name}")
    if past_decisions:
        event_block_lines.append(
            "Past decisions: " + " | ".join(past_decisions)
        )
    if last_recap is not None:
        event_block_lines.append(f"Last recap: {last_recap.body[:400]}")
    if open_questions:
        event_block_lines.append(
            "Open questions: " + " | ".join(open_questions)
        )
    if role_gaps:
        event_block_lines.append(
            "Roles still unclaimed: " + ", ".join(role_gaps)
        )

    template = promptlib.load_prompt("briefing")
    schema = template.schema or {}
    prompt = template.render(
        EVENT="\n".join(event_block_lines),
        CHUNKS=pruning.render_chunks(outcome.kept),
    )
    result = client.complete_json(prompt=prompt, schema=schema, tier="wonder")
    usage_service.record_usage(
        db, result=result, group_id=group.id, operation="brief:briefing",
        prompt_name="briefing", prompt_version=template.version,
    )
    if not isinstance(result.data, dict) or not isinstance(
        result.data.get("text"), str
    ):
        raise ModelError(
            "The briefing answer was malformed (no 'text'); refusing to store it."
        )
    summary = Summary(
        group_id=group.id,
        kind="event_brief",
        title=f"Briefing — {event.title}",
        body=result.data["text"],
        status="final",
        verified=True,
        citations_json="[]",
        range_json=json.dumps(
            {
                "chunk_ids": [c.chunk_id for c in outcome.kept],
                "prune_run_id": run.id,
                "event_id": event.id,
            }
        ),
        event_id=event.id,
        created_by=actor_label,
    )
    db.add(summary)
    db.flush()
    audit.record(
        db, actor=actor_label, action="memory.briefing_created",
        group_id=group.id, entity_type="summary", entity_id=summary.id,
        detail={"event_id": event.id, "prune_run_id": run.id},
    )
    db.commit()
    return {
        "summary": summary,
        "open_questions": [
            str(q) for q in result.data.get("open_questions", [])
            if isinstance(q, str)
        ],
        "role_gaps": role_gaps,
        "prune_run_id": run.id,
    }


# --- Recaps, approval, and the newsletter loop (B-118) -------------


def recap_event(
    db: Session,
    settings: Settings | None = None,
    *,
    actor_label: str,
    group: Group,
    event,
    client=None,
) -> Summary:
    """Draft one event's recap (B-118) — a DRAFT, always.

    The material is the event's own: its live discussion,
    the attendance count (a count, from the attendance
    service — never a name list), and the group's flyers and
    photos whose alt text is COMPLETE — an image enters the
    group's memory only through its finished description
    (the alt-text law, both ways: write it, and read only
    what was written). A person approves the draft before it
    becomes memory the engine can use.
    """
    from gpa.ai import promptlib
    from gpa.ai.client import ModelError
    from gpa.errors import AppError
    from gpa.models.sources import Flyer
    from gpa.services import attendance as attendance_service
    from gpa.services import discussion as discussion_service

    settings = settings or get_settings()
    client = client if client is not None else resolve_client(settings, group)

    # The recap door is organiser-only, so the discussion is
    # read at that role (a viewer-less read sees no events at
    # all under the visibility bright line); hidden/deleted
    # words are then filtered out below regardless — they are
    # moderation material, not memory.
    comments = discussion_service.list_comments(
        db, group=group, parent_type="event", parent_id=event.id,
        viewer_role="organiser",
    )
    live = [c for c in comments if c.hidden_at is None and c.deleted_at is None]
    chunks = [
        pruning.MemoryChunk(
            chunk_id=f"comment:{comment.id}",
            position=index,
            text=comment.body,
            sender=comment.author_label,
            sent_at=comment.created_at,
            origin="comment",
        )
        for index, comment in enumerate(live)
    ]
    headcount = attendance_service.event_headcount(db, event)
    flyers = (
        db.query(Flyer)
        .filter(
            Flyer.group_id == group.id,
            Flyer.alt_text_status == "complete",
            Flyer.alt_text != "",
        )
        .order_by(Flyer.created_at)
        .all()
    )
    if not chunks and headcount == 0 and not flyers:
        raise AppError(
            "validation_error",
            detail="There is nothing to recap yet — no discussion, "
            "no attendance, no described photos.",
        )

    template = promptlib.load_prompt("recap")
    schema = template.schema or {}
    prompt = template.render(
        EVENT=(
            f"{event.title} — {event.starts_at.isoformat()} "
            f"({event.timezone})"
            + (f" at {event.place_name}" if event.place_name else "")
        ),
        ATTENDANCE=f"{headcount} people were marked present.",
        DISCUSSION=(
            pruning.render_chunks(chunks)
            if chunks
            else "(nothing was said in the event's discussion)"
        ),
        PHOTOS=(
            "\n".join(f"- {flyer.alt_text}" for flyer in flyers)
            if flyers
            else "(no photos with completed descriptions)"
        ),
    )
    result = client.complete_json(prompt=prompt, schema=schema, tier="wonder")
    usage_service.record_usage(
        db, result=result, group_id=group.id, operation="recap:recap",
        prompt_name="recap", prompt_version=template.version,
    )
    if not isinstance(result.data, dict) or not isinstance(
        result.data.get("text"), str
    ):
        raise ModelError(
            "The recap answer was malformed (no 'text'); refusing to store it."
        )
    summary = Summary(
        group_id=group.id,
        kind="recap",
        title=f"Recap — {event.title}",
        body=result.data["text"],
        status="draft",
        verified=True,
        citations_json="[]",
        range_json=json.dumps(
            {"chunk_ids": [c.chunk_id for c in chunks], "event_id": event.id}
        ),
        event_id=event.id,
        created_by=actor_label,
    )
    db.add(summary)
    db.flush()
    audit.record(
        db, actor=actor_label, action="memory.recap_created",
        group_id=group.id, entity_type="summary", entity_id=summary.id,
        detail={"event_id": event.id},
    )
    db.commit()
    return summary


def approve_summary(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    summary: Summary,
) -> Summary:
    """Approve a draft summary — the moment a draft becomes
    group memory (B-118).

    Approving a NEWSLETTER closes the loop the roadmap draws:
    the approved letter becomes a Source of kind ``newsletter``
    in the group's own history, its paragraphs as chunks —
    so next month's pruning and extraction read this month's
    letter like anything else the group said. Approval of any
    other kind simply admits the summary to the corpus
    (discussion and recap summaries join the approved corpus
    by their status alone).
    """
    from gpa.errors import AppError
    from gpa.services import sources as source_service

    if summary.status != "draft":
        raise AppError(
            "conflict",
            detail="That summary is not a draft — it has already been settled.",
        )
    summary.status = "approved"
    db.flush()

    source_id: str | None = None
    if summary.kind == "newsletter":
        paragraphs = [
            part.strip()
            for part in summary.body.split("\n\n")
            if part.strip()
        ] or [summary.body]
        # create_source commits — the approval above rides
        # that commit, which is the honest order anyway: the
        # letter exists as a source BECAUSE it was approved.
        source = source_service.create_source(
            db, actor_label=actor_label, group=group,
            kind="newsletter", title=summary.title,
            granted_scope="group-history",
            chunks=[
                {
                    "text": paragraph,
                    "sender": "newsletter",
                    "sent_at": summary.created_at,
                }
                for paragraph in paragraphs
            ],
        )
        source_id = source.id

    audit.record(
        db, actor=actor_label, action="memory.summary_approved",
        group_id=group.id, entity_type="summary", entity_id=summary.id,
        detail={"kind": summary.kind, "source_id": source_id},
    )
    db.commit()
    return summary


def draft_newsletter(
    db: Session,
    settings: Settings | None = None,
    *,
    actor_label: str,
    group: Group,
    year: int,
    month: int,
    client=None,
) -> Summary:
    """Weave one month's APPROVED recaps into a newsletter
    draft (B-118). Drafts only — the letter becomes memory
    when a person approves it (see approve_summary)."""
    import calendar as calendar_module
    from datetime import datetime

    from gpa.ai import promptlib
    from gpa.ai.client import ModelError
    from gpa.errors import AppError

    settings = settings or get_settings()
    client = client if client is not None else resolve_client(settings, group)

    start = datetime(year, month, 1)
    end = (
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )
    recaps = (
        db.query(Summary)
        .filter(
            Summary.group_id == group.id, Summary.kind == "recap",
            Summary.status == "approved",
            Summary.created_at >= start, Summary.created_at < end,
        )
        .order_by(Summary.created_at)
        .all()
    )
    if not recaps:
        raise AppError(
            "validation_error",
            detail="No approved recaps for that month yet — a "
            "newsletter is woven from approved recaps.",
        )

    template = promptlib.load_prompt("newsletter")
    schema = template.schema or {}
    prompt = template.render(
        MONTH=f"{calendar_module.month_name[month]} {year}",
        RECAPS="\n\n".join(
            f"## {recap.title}\n{recap.body}" for recap in recaps
        ),
    )
    result = client.complete_json(prompt=prompt, schema=schema, tier="wonder")
    usage_service.record_usage(
        db, result=result, group_id=group.id, operation="newsletter:newsletter",
        prompt_name="newsletter", prompt_version=template.version,
    )
    if (
        not isinstance(result.data, dict)
        or not isinstance(result.data.get("subject"), str)
        or not isinstance(result.data.get("body"), str)
    ):
        raise ModelError(
            "The newsletter answer was malformed (no 'subject'/'body'); "
            "refusing to store it."
        )
    summary = Summary(
        group_id=group.id,
        kind="newsletter",
        title=result.data["subject"],
        body=result.data["body"],
        status="draft",
        verified=True,
        citations_json="[]",
        range_json=json.dumps({"recap_ids": [r.id for r in recaps]}),
        created_by=actor_label,
    )
    db.add(summary)
    db.flush()
    audit.record(
        db, actor=actor_label, action="memory.newsletter_drafted",
        group_id=group.id, entity_type="summary", entity_id=summary.id,
        detail={"year": year, "month": month, "recaps": len(recaps)},
    )
    db.commit()
    return summary
