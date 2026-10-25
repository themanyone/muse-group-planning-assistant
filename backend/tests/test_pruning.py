"""Pruning core (roadmap B-113).

Geoffrey's engine: a full model reads the group's own chunks
and judges relevance — NOT embedding RAG, and the first test
below holds the code to saying so. The acceptance is the golden
question over a 500-message corpus: the kept set contains every
chunk a human marked relevant (the recall floor), at no more
than a quarter of the original tokens. Recordings are keyed by
the engine's own prompt rendering, so the fixture driver
replays exactly what production would send.
"""

import inspect
from datetime import datetime, timedelta, timezone

from conftest import make_group, register
from gpa.ai import pruning, promptlib
from gpa.ai.client import estimate_tokens
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import chunk_windows
from gpa.models.groups import Candidate, Group
from gpa.models.memory import PruneRun, Summary
from gpa.models.planning import Decision
from gpa.services import memory as memory_service
from gpa.services import sources as source_service

QUESTION = "What did we decide about the hall?"

RELEVANT_TEXTS = [
    "We need to settle the hall question before the invoice lands.",
    "Ana: I move we take the village hall for club nights, not the gym.",
    "Seconded. The gym double-booked us in March and again in April.",
    "Agreed then: the hall, not the school gym, for club nights.",
    "The hall costs forty pounds a night as of the October invoice.",
    "Hall booking confirmed for the season, Tuesdays, seven till ten.",
    "Reminder: the hall key lives with Ana when she is not travelling.",
    "If the hall floods again we fall back to the scout hut, not the gym.",
    "The hall committee wants our dates for next term by Friday.",
    "Deposit for the hall is paid, receipt is in the tin.",
    "We promised the hall committee no amplified music after ten.",
    "Decision stands: hall for club nights; revisit only if rent rises.",
]

FILLER_TOPICS = [
    "Quiz night rota: {a} brings the question cards, {b} keeps score, "
    "we still need someone on the door and the raffle prizes need "
    "wrapping before Friday evening comes around again.",
    "Lost property: one green scarf, one umbrella with a broken spoke, "
    "and a thermos that has survived three treasurers; claim them "
    "from the cupboard by the kitchen before the end of the month.",
    "The rota for washing up after supper club is posted on the board; "
    "{a} and {b} swap weeks when the school holidays start, and nobody "
    "is allowed to trade out of the big pan more than twice a term.",
    "Bus times changed again: the last one leaves the square at twenty "
    "past ten, so anyone staying for the second half of the film "
    "night needs a lift home — {a} usually has two spare seats.",
    "The noticeboard pins are running low and the cork is crumbling; "
    "{b} said the shop in the high street has the brass ones back in "
    "stock, the ones that do not bend when you push them in.",
]

NAMES = ["Ana", "Ben", "Cy", "Dee", "Eli", "Fay", "Gil", "Hal"]


def golden_corpus() -> tuple[list[pruning.MemoryChunk], set[int]]:
    """500 messages: 12 relevant (human-marked), 488 of the
    group's ordinary noise, in chronological order."""
    start = datetime(2026, 7, 6, 19, 0, tzinfo=timezone.utc)
    relevant_at = {17, 63, 104, 131, 188, 233, 271, 318, 366, 402, 449, 487}
    chunks: list[pruning.MemoryChunk] = []
    relevant_iter = iter(RELEVANT_TEXTS)
    for position in range(500):
        if position in relevant_at:
            text = next(relevant_iter)
        else:
            template = FILLER_TOPICS[position % len(FILLER_TOPICS)]
            text = template.format(
                a=NAMES[position % len(NAMES)],
                b=NAMES[(position + 3) % len(NAMES)],
            )
        chunks.append(
            pruning.MemoryChunk(
                chunk_id=f"golden-{position}",
                position=position,
                text=text,
                sender=NAMES[position % len(NAMES)],
                sent_at=start + timedelta(hours=position * 3),
            )
        )
    return chunks, relevant_at


def record_judgements(driver: FixtureDriver, question: str, chunks, keep_positions: set[int]):
    """Record, per window, the human judgement: keep exactly
    ``keep_positions``. Keyed by the engine's own rendering."""
    schema = promptlib.load_prompt("prune").schema
    for window in chunk_windows(chunks):
        kept = [c.position for c in window if c.position in keep_positions]
        driver.add_json(
            pruning.build_prune_prompt(question, window), schema, {"kept": kept}
        )


def test_the_engine_says_plainly_it_is_not_embedding_rag():
    source = inspect.getsource(pruning)
    assert "not embedding" in source.lower()
    assert "no embeddings, no vector store" in source
    # And the fact behind the words: nothing embedding-shaped is
    # imported by the engine.
    for line in source.splitlines():
        if line.startswith(("import ", "from ")):
            assert "embedding" not in line
            assert "vector" not in line
            assert "faiss" not in line
            assert "numpy" not in line


def test_golden_500_message_corpus_recall_floor_and_token_budget():
    chunks, relevant = golden_corpus()
    driver = FixtureDriver()
    record_judgements(driver, QUESTION, chunks, relevant)

    outcome = pruning.prune_once(driver, question=QUESTION, chunks=chunks)

    kept_positions = {c.position for c in outcome.kept}
    # The recall floor: EVERY human-marked relevant chunk survives.
    assert relevant <= kept_positions
    # The fixture judged exactly, so nothing irrelevant survives either.
    assert kept_positions == relevant
    # Survivors stay in corpus order, and the drop is visible.
    assert [c.position for c in outcome.kept] == sorted(relevant)
    assert {c.position for c in outcome.dropped} == set(range(500)) - relevant
    # The budget claim: kept tokens ≤ 25% of the original.
    corpus_tokens = sum(estimate_tokens(c.text) for c in chunks)
    kept_tokens = sum(estimate_tokens(c.text) for c in outcome.kept)
    assert kept_tokens <= 0.25 * corpus_tokens
    assert outcome.passes == 1


def test_a_run_is_written_with_kept_dropped_model_and_cost(client, db_session):
    owner = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])
    source_service.create_source(
        db_session, actor_label=f"user:{owner['user_id']}", group=group,
        kind="chat_paste", title="July chat", granted_scope="group-history",
        chunks=[
            {"text": "We agreed: the hall, not the gym, for club nights.",
             "sender": "Ana", "sent_at": datetime(2026, 7, 6, 19, 0)},
            {"text": "Quiz rota: Ben brings the cards on Friday.",
             "sender": "Ben", "sent_at": datetime(2026, 7, 6, 19, 5)},
        ],
    )
    corpus = memory_service.approved_corpus(db_session, group)
    assert len(corpus) == 2
    driver = FixtureDriver()
    record_judgements(driver, QUESTION, corpus, {0})

    run, outcome, _ = memory_service.run_prune(
        db_session, actor_label=f"user:{owner['user_id']}", group=group,
        question=QUESTION, client=driver, max_passes=1,
    )
    row = db_session.get(PruneRun, run.id)
    assert row is not None
    assert row.input_chunks == 2
    assert row.passes == 1
    import json

    assert json.loads(row.kept_json) == [corpus[0].chunk_id]
    assert json.loads(row.dropped_json) == [corpus[1].chunk_id]
    assert "fixture" in row.model_id
    assert row.cost_micros == 0  # fixture recordings spend nothing
    # The usage ledger carries the call too (B-047's discipline).
    from gpa.models.ai import AiUsage

    usage = db_session.query(AiUsage).filter(AiUsage.group_id == group.id).all()
    assert len(usage) == 1
    assert usage[0].operation == "prune:prune"


def test_corpus_is_approved_history_only(client, db_session):
    """Pending drafts, rejected candidates, superseded decisions
    and unapproved summaries are NOT the group's past."""
    owner = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])
    label = f"user:{owner['user_id']}"
    source_service.create_source(
        db_session, actor_label=label, group=group, kind="chat_paste",
        title="chat", granted_scope="group-history",
        chunks=[{"text": "Ordinary words the group really said.", "sender": "Ana"}],
    )
    db_session.add(
        Decision(group_id=group.id, text="Settled: the hall for club nights.",
                 status="current", created_by=label)
    )
    db_session.add(
        Decision(group_id=group.id, text="Old truth: the gym, before we knew.",
                 status="superseded", created_by=label)
    )
    db_session.add(
        Candidate(group_id=group.id, kind="decision", title="draft",
                  created_by="system:extraction", status="pending",
                  draft_json='{"text": "PENDING-DRAFT-SECRET"}')
    )
    db_session.add(
        Summary(group_id=group.id, kind="recap", body="A recap nobody approved.",
                status="draft", created_by="system:memory",
                range_json='{"chunk_ids": ["x"]}')
    )
    db_session.add(
        Summary(group_id=group.id, kind="recap", body="Last year's fair, approved.",
                status="approved", created_by="system:memory",
                range_json='{"chunk_ids": ["x"]}')
    )
    db_session.commit()

    corpus = memory_service.approved_corpus(db_session, group)
    texts = [c.text for c in corpus]
    assert "Ordinary words the group really said." in texts
    assert "Settled: the hall for club nights." in texts
    assert "Last year's fair, approved." in texts
    assert not any("PENDING-DRAFT-SECRET" in t for t in texts)
    assert not any("Old truth" in t for t in texts)
    assert not any("nobody approved" in t for t in texts)
    # Positions are the corpus order, 0..N-1 with no gaps.
    assert [c.position for c in corpus] == list(range(len(corpus)))
