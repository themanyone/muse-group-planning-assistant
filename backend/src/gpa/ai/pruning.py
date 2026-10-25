"""The context-pruning engine (roadmap B-113, B-114).

Geoffrey's engine, built properly — and the first thing to say
about it is the thing it is **not**: this is **not embedding
RAG**. There are no embeddings, no vector store, no similarity
index anywhere in this module or anywhere it is used.
Retrieval-by-embedding guesses which chunks *look like* the
question; this engine instead has a full model **read** the
group's own chunks and judge, chunk by chunk, what is
*relevant to the question actually asked* — including the chunk
whose words share nothing with the question but whose meaning
settles it (three-month-old landmines are rarely phrased like
the questions they answer).

What it is: a pruning pipeline over an ordered list of chunks
(the same citable units extraction reads). One pass windows the
chunks and asks the answering-class model to judge each window;
survivors are kept **in their original order, with provenance**
and the rest are dropped *visibly* — the outcome names both
sets, and the service layer writes the whole account into a
``PruneRun`` row (B-113), so "the model ignored it" is always
checkable after the fact.

Multi-pass (B-114): the engine can be *reapplied* — a second
pass re-judges only the dropped set, aimed at the gaps a draft
answer exposed (or, without a draft, as a plain second look).
Pass count and token budget are hard caps, enforced HERE, in
the engine, whatever the caller asks for: a pass that would
start over budget never starts, and its unjudged chunks stay
exactly where they were (dropped, and visibly so).

This module is pure — no database, no storage — like
``ai/extraction.py``. The service layer (services/memory.py)
scopes the corpus, feeds it chunks, ledgers the usage, and
persists the runs.
"""

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Callable

from gpa.ai import promptlib
from gpa.ai.client import MetaModelClient, ModelError, ModelResult, estimate_tokens

# Pruning judges with the answering-class model: the tier that
# will have to answer *from* the survivors decides what the
# answer needs. (Extraction uses the same tier to draft.)
PRUNE_TIER = "wonder"

# The absolute ceiling on passes, whatever a group's settings
# say (B-114: settings have hard caps — this is the hardest one).
MAX_PASSES_CEILING = 3

# Called after every model call so the service can ledger the
# usage inside its own transaction (B-047), exactly as the
# extraction pipeline's recorder does.
UsageRecorder = Callable[[ModelResult, str], None]


@dataclass
class MemoryChunk:
    """One citable unit of the group's past, whatever it was.

    A source chunk keeps its own id; settled truth rides as
    ``decision:<id>`` and digested past as ``summary:<id>`` (the
    corpus builder's naming, services/memory.py). ``position``
    is the corpus order — survivors are always returned in it,
    and prompts name chunks by it, so a citation's position is
    stable for the whole run.
    """

    chunk_id: str
    position: int
    text: str
    sender: str | None = None
    sent_at: datetime | None = None
    origin: str = "source"  # source | decision | summary | comment

    @property
    def id(self) -> str:
        """The extraction pipeline's name for a chunk's id
        (B-040 reads ``chunk.id``) — one chunk, two pipelines,
        no translation layer."""
        return self.chunk_id


@dataclass
class PrunePass:
    """One pass's judgement over the chunks it was shown."""

    kept: list[MemoryChunk] = dataclass_field(default_factory=list)
    dropped: list[MemoryChunk] = dataclass_field(default_factory=list)
    model_id: str = ""
    cost_micros: int = 0
    input_tokens: int = 0


@dataclass
class PruneOutcome:
    """A whole (possibly multi-pass) run's account."""

    kept: list[MemoryChunk] = dataclass_field(default_factory=list)
    dropped: list[MemoryChunk] = dataclass_field(default_factory=list)
    passes: int = 0
    model_id: str = ""
    cost_micros: int = 0
    input_tokens: int = 0
    # True when the token budget, not the model's judgement,
    # ended the run early — the run record says so, so a member
    # can see the answer was pruned by money, not by meaning.
    budget_stopped: bool = False


def render_chunks(chunks: list[MemoryChunk]) -> str:
    """The exact chunk block placed into a prompt.

    The same shape extraction renders (``[chunk N · sender ·
    stamp]`` + text), shared in spirit and in format: a chunk
    means the same thing to every pipeline that reads it, and a
    test can key a fixture recording by the very text production
    sends.
    """
    blocks = []
    for chunk in chunks:
        sender = chunk.sender or "unknown sender"
        stamp = chunk.sent_at.isoformat() if chunk.sent_at else "no timestamp"
        blocks.append(f"[chunk {chunk.position} · {sender} · {stamp}]\n{chunk.text}")
    return "\n\n".join(blocks)


def build_prune_prompt(
    question: str,
    chunks: list[MemoryChunk],
    *,
    reconsider: bool = False,
    gaps: str = "",
) -> str:
    """Render the judging prompt for one window of chunks.

    Pass 1 uses ``prune``; later passes use
    ``prune_reconsider`` with the draft's gaps named — the two
    prompts hash differently, so a fixture recording can never
    silently serve both.
    """
    if reconsider:
        return promptlib.render_prompt(
            "prune_reconsider",
            QUESTION=question,
            GAPS=gaps or "the draft could not be completed from the survivors alone",
            CHUNKS=render_chunks(chunks),
        )
    return promptlib.render_prompt(
        "prune", QUESTION=question, CHUNKS=render_chunks(chunks)
    )


def _shape_error(data: object) -> str | None:
    """Validate a judging answer: {"kept": [positions]}.

    Hand-rolled like extraction's shape check — the shapes are
    few, owned here, and the error words feed the repair retry.
    """
    if not isinstance(data, dict) or not isinstance(data.get("kept"), list):
        return "the answer must be an object with a 'kept' list"
    for index, position in enumerate(data["kept"]):
        if not isinstance(position, int) or isinstance(position, bool):
            return f"kept entry {index} must be a chunk position number"
    return None


def _judge_window(
    client: MetaModelClient,
    *,
    question: str,
    window: list[MemoryChunk],
    reconsider: bool,
    gaps: str,
    record_usage: UsageRecorder | None,
) -> tuple[set[int], ModelResult, str]:
    """One window, judged. Returns (kept positions, result,
    prompt name). A malformed answer earns exactly one repair
    retry naming the fault; a second failure raises ModelError
    — judging fails visibly, never by silently keeping or
    dropping everything (the extraction discipline, B-040)."""
    prompt_name = "prune_reconsider" if reconsider else "prune"
    template = promptlib.load_prompt(prompt_name)
    schema = template.schema or {}
    prompt = build_prune_prompt(
        question, window, reconsider=reconsider, gaps=gaps
    )
    result = client.complete_json(prompt=prompt, schema=schema, tier=PRUNE_TIER)
    if record_usage is not None:
        record_usage(result, prompt_name)
    error = _shape_error(result.data)
    if error is not None:
        repair = (
            prompt
            + f"\n\nYour previous answer was not valid: {error}. "
            "Return ONLY valid JSON in the required shape."
        )
        result = client.complete_json(prompt=repair, schema=schema, tier=PRUNE_TIER)
        if record_usage is not None:
            record_usage(result, prompt_name)
        error = _shape_error(result.data)
        if error is not None:
            raise ModelError(
                f"Pruning produced malformed output twice ({error}); "
                "refusing to prune on it."
            )
    kept_positions = {p for p in result.data["kept"]}
    return kept_positions, result, prompt_name


def _judge_all(
    client: MetaModelClient,
    *,
    question: str,
    chunks: list[MemoryChunk],
    reconsider: bool,
    gaps: str,
    budget_left: int | None,
    record_usage: UsageRecorder | None,
) -> tuple[PrunePass, bool]:
    """Judge every chunk, window by window, inside a budget.

    The budget is checked BEFORE each window's call, on the
    rendered prompt's estimated tokens: a window that would
    start over budget never starts. Its chunks keep their
    incoming state — in pass 1 that means dropped; in a
    reconsider pass it means still dropped (a rescue that never
    happened cannot invent one). Returns (pass, budget_stopped).
    """
    from gpa.ai.extraction import chunk_windows

    outcome = PrunePass()
    spent = 0
    budget_stopped = False
    for window in chunk_windows(chunks):  # type: ignore[arg-type]
        prompt = build_prune_prompt(
            question, window, reconsider=reconsider, gaps=gaps
        )
        estimate = estimate_tokens(prompt)
        if budget_left is not None and spent + estimate > budget_left:
            budget_stopped = True
            # Unjudged chunks stay as they were: dropped.
            outcome.dropped.extend(window)
            continue
        kept_positions, result, _name = _judge_window(
            client, question=question, window=window,
            reconsider=reconsider, gaps=gaps, record_usage=record_usage,
        )
        spent += result.input_tokens or estimate
        outcome.model_id = result.model_id
        outcome.cost_micros += result.cost_micros
        outcome.input_tokens += result.input_tokens or estimate
        for chunk in window:
            if chunk.position in kept_positions:
                outcome.kept.append(chunk)
            else:
                outcome.dropped.append(chunk)
    return outcome, budget_stopped


def prune_once(
    client: MetaModelClient,
    *,
    question: str,
    chunks: list[MemoryChunk],
    token_budget: int | None = None,
    record_usage: UsageRecorder | None = None,
) -> PruneOutcome:
    """Pass 1: judge the whole corpus once (B-113)."""
    if not chunks:
        return PruneOutcome()
    first, stopped = _judge_all(
        client, question=question, chunks=chunks, reconsider=False,
        gaps="", budget_left=token_budget, record_usage=record_usage,
    )
    return PruneOutcome(
        kept=first.kept, dropped=first.dropped, passes=1,
        model_id=first.model_id, cost_micros=first.cost_micros,
        input_tokens=first.input_tokens, budget_stopped=stopped,
    )


def reconsider_dropped(
    client: MetaModelClient,
    *,
    question: str,
    kept: list[MemoryChunk],
    dropped: list[MemoryChunk],
    gaps: str = "",
    token_budget: int | None = None,
    record_usage: UsageRecorder | None = None,
) -> PruneOutcome:
    """One more pass, over the dropped set only (B-114).

    Rescued chunks rejoin the survivors **in corpus order** —
    provenance order is a property of the corpus, not of the
    pass that happened to find the chunk.
    """
    if not dropped:
        return PruneOutcome(kept=list(kept), dropped=[], passes=0)
    second, stopped = _judge_all(
        client, question=question, chunks=dropped, reconsider=True,
        gaps=gaps, budget_left=token_budget, record_usage=record_usage,
    )
    merged = sorted(kept + second.kept, key=lambda c: c.position)
    still_dropped = sorted(second.dropped, key=lambda c: c.position)
    return PruneOutcome(
        kept=merged, dropped=still_dropped, passes=1,
        model_id=second.model_id, cost_micros=second.cost_micros,
        input_tokens=second.input_tokens, budget_stopped=stopped,
    )


def prune_multi(
    client: MetaModelClient,
    *,
    question: str,
    chunks: list[MemoryChunk],
    max_passes: int = 2,
    token_budget: int | None = None,
    gaps: str = "",
    record_usage: UsageRecorder | None = None,
) -> PruneOutcome:
    """The engine reapplied (B-114): pass 1, then reconsider
    passes over the dropped set until the pass cap, the budget,
    or an empty dropped set stops it.

    ``max_passes`` is clamped to the ceiling here — a caller
    asking for ten passes gets three, and the outcome's
    ``passes`` says how many actually ran.
    """
    cap = max(1, min(int(max_passes), MAX_PASSES_CEILING))
    outcome = prune_once(
        client, question=question, chunks=chunks,
        token_budget=token_budget, record_usage=record_usage,
    )
    spent = outcome.input_tokens
    passes_run = outcome.passes
    while (
        passes_run < cap
        and outcome.dropped
        and not outcome.budget_stopped
    ):
        left = None if token_budget is None else token_budget - spent
        if left is not None and left <= 0:
            outcome.budget_stopped = True
            break
        dropped_before = len(outcome.dropped)
        nxt = reconsider_dropped(
            client, question=question, kept=outcome.kept,
            dropped=outcome.dropped, gaps=gaps, token_budget=left,
            record_usage=record_usage,
        )
        outcome.kept, outcome.dropped = nxt.kept, nxt.dropped
        outcome.cost_micros += nxt.cost_micros
        outcome.input_tokens += nxt.input_tokens
        outcome.model_id = nxt.model_id or outcome.model_id
        outcome.budget_stopped = outcome.budget_stopped or nxt.budget_stopped
        spent += nxt.input_tokens
        passes_run += 1
        if len(outcome.dropped) == dropped_before:
            # The pass rescued nothing; judging the same dropped
            # set again with the same words would rescue nothing
            # either. Stop — the cap is a ceiling, not a quota.
            break
    outcome.passes = passes_run
    return outcome


def _normalise(text: str) -> str:
    """Whitespace-normalised form for quote matching — B-040's
    rule, word for word: a quote split across a line wrap still
    matches."""
    return " ".join(text.split())


def find_quote(quote: str, chunks: list[MemoryChunk]) -> MemoryChunk | None:
    """The chunk a quote provably came from, or None (B-040's
    quote-existence check, reused by B-115 — the roadmap's own
    words). An empty quote proves nothing and matches nothing."""
    needle = _normalise(quote)
    if not needle:
        return None
    for chunk in chunks:
        if needle in _normalise(chunk.text):
            return chunk
    return None


def verify_citations(
    quotes: list[str], chunks: list[MemoryChunk]
) -> list[MemoryChunk | None]:
    """Per quote: the chunk that backs it, or None. The caller
    decides what failure means (B-115: any failure converts the
    whole answer into the honest refusal)."""
    return [find_quote(quote, chunks) for quote in quotes]
