"""Ask-the-group-memory (roadmap B-115).

Question → prune over the APPROVED history → answer with
citations → stored as a Summary of kind ``ask``. The
acceptance test is the unverifiable-answer test: when the
draft's citations fail B-040's quote check, what comes back
is "I couldn't verify this" — never a confident guess.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai import promptlib, pruning
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import chunk_windows
from gpa.models.groups import Group
from gpa.services import memory as memory_service
from gpa.services import sources as source_service
from test_pruning import record_judgements

QUESTION = "What did we decide about the hall?"
DECISION_TEXT = "Agreed then: the hall, not the school gym, for club nights."
DECISION_QUOTE = "the hall, not the school gym, for club nights"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    ai_client.set_client(None)


def world(client, db_session, *, passes=None):
    """A group whose history holds one hall decision among noise."""
    owner = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])
    label = f"user:{owner['user_id']}"
    from datetime import datetime

    source_service.create_source(
        db_session, actor_label=label, group=group, kind="chat_paste",
        title="Season chat", granted_scope="group-history",
        chunks=[
            {"text": "Quiz rota: Ben brings the cards, Cy scores.",
             "sender": "Ben", "sent_at": datetime(2026, 7, 6, 19, 0)},
            {"text": DECISION_TEXT, "sender": "Ana",
             "sent_at": datetime(2026, 7, 6, 19, 4)},
            {"text": "The tea urn needs descaling before winter.",
             "sender": "Cy", "sent_at": datetime(2026, 7, 6, 19, 9)},
        ],
    )
    if passes is not None:
        response = client.put(
            f"/api/v1/groups/{group.id}/settings",
            json={"prune_max_passes": passes}, headers=bearer(owner),
        )
        assert response.status_code == 200, response.text
        db_session.expire_all()
    corpus = memory_service.approved_corpus(db_session, group)
    return owner, group_json, group, corpus


def record_ask(driver: FixtureDriver, question: str, kept, data: dict):
    schema = promptlib.load_prompt("ask_memory").schema
    prompt = promptlib.render_prompt(
        "ask_memory", QUESTION=question, CHUNKS=pruning.render_chunks(kept)
    )
    driver.add_json(prompt, schema, data)


def ask(client, owner, group_json, question=QUESTION):
    return client.post(
        f"/api/v1/groups/{group_json['id']}/ask",
        json={"question": question}, headers=bearer(owner),
    )


def test_verified_answer_carries_its_citations(client, db_session):
    owner, group_json, group, corpus = world(client, db_session, passes=1)
    driver = FixtureDriver()
    record_judgements(driver, QUESTION, corpus, {1})
    kept = [c for c in corpus if c.position == 1]
    record_ask(driver, QUESTION, kept, {
        "answer": "You decided club nights use the hall, not the school gym.",
        "citations": [{"quote": DECISION_QUOTE}],
    })
    ai_client.set_client(driver)

    response = ask(client, owner, group_json)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is True
    assert "the hall, not the school gym" in body["answer"]
    assert body["citations"] == [
        {"quote": DECISION_QUOTE, "chunk_id": kept[0].chunk_id, "position": 1}
    ]
    # Stored as a Summary of kind ask, with its source range.
    summaries = client.get(
        f"/api/v1/groups/{group_json['id']}/summaries?kind=ask",
        headers=bearer(owner),
    ).json()
    assert len(summaries) == 1
    assert summaries[0]["id"] == body["summary_id"]
    assert summaries[0]["verified"] is True
    assert summaries[0]["source_range"]["prune_run_id"] == body["prune_run_id"]
    assert summaries[0]["source_range"]["chunk_ids"] == [kept[0].chunk_id]


def test_unverifiable_answer_returns_the_honest_refusal(client, db_session):
    """THE acceptance test (B-115): a draft that cites words
    the history never said comes back as "I couldn't verify
    this", with the found material labelled unverified."""
    owner, group_json, group, corpus = world(client, db_session, passes=1)
    driver = FixtureDriver()
    record_judgements(driver, QUESTION, corpus, {1})
    kept = [c for c in corpus if c.position == 1]
    record_ask(driver, QUESTION, kept, {
        "answer": "You decided to rent the castle for club nights.",
        "citations": [{"quote": "we shall rent the castle every Tuesday"}],
    })
    ai_client.set_client(driver)

    response = ask(client, owner, group_json)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is False
    assert body["answer"].startswith("I couldn't verify this")
    # The invented material is shown as found-but-unverified,
    # its citation carrying no chunk — never dressed as proof.
    assert "rent the castle" in body["answer"]
    assert body["citations"][0]["chunk_id"] is None
    # And the stored summary tells the same truth.
    summaries = client.get(
        f"/api/v1/groups/{group_json['id']}/summaries?kind=ask",
        headers=bearer(owner),
    ).json()
    assert summaries[0]["verified"] is False
    assert summaries[0]["body"].startswith("I couldn't verify this")


def test_gap_pass_rescues_the_evidence_and_the_answer_verifies(client, db_session):
    """Doc 07 §7.2 at the ask door: pass 1 keeps a weak chunk,
    the draft from it cannot verify, the gap pass re-prunes the
    dropped set against the failure and rescues the decision —
    the re-draft verifies."""
    owner, group_json, group, corpus = world(client, db_session, passes=2)
    driver = FixtureDriver()
    # Pass 1 keeps only the tea-urn noise (position 2).
    record_judgements(driver, QUESTION, corpus, {2})
    weak = [c for c in corpus if c.position == 2]
    record_ask(driver, QUESTION, weak, {
        "answer": "Club nights use the hall.",
        "citations": [{"quote": DECISION_QUOTE}],  # not in the weak chunk
    })
    # The reconsider pass over the dropped set rescues position 1.
    dropped = [c for c in corpus if c.position != 2]
    schema = promptlib.load_prompt("prune_reconsider").schema
    for window in chunk_windows(dropped):
        kept_positions = [c.position for c in window if c.position == 1]
        # The gap text is the service's own wording — the prompt
        # the fixture must key on is rebuilt from the service's
        # gap composition, so compute it the same way: draft text
        # + the fixed lead. (test composed via the real renderer
        # in test body below instead.)
        driver.add_json(
            pruning.build_prune_prompt(
                QUESTION, window, reconsider=True,
                gaps=(
                    "The draft answer could not be verified. Draft: "
                    "Club nights use the hall. — its claims lack "
                    "surviving evidence."
                ),
            ),
            schema, {"kept": kept_positions},
        )
    rescued = [c for c in corpus if c.position in (1, 2)]
    record_ask(driver, QUESTION, rescued, {
        "answer": "You decided club nights use the hall, not the school gym.",
        "citations": [{"quote": DECISION_QUOTE}],
    })
    ai_client.set_client(driver)

    response = ask(client, owner, group_json)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is True
    assert body["citations"][0]["position"] == 1


def test_nothing_relevant_is_a_refusal_not_a_guess(client, db_session):
    owner, group_json, group, corpus = world(client, db_session, passes=1)
    driver = FixtureDriver()
    record_judgements(
        driver, "What did we decide about parking?", corpus, set()
    )
    ai_client.set_client(driver)

    response = ask(
        client, owner, group_json, question="What did we decide about parking?"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is False
    assert body["answer"].startswith("I couldn't verify this")
    assert body["citations"] == []


def test_a_stranger_cannot_ask_a_private_groups_memory(client, db_session):
    owner, group_json, group, corpus = world(client, db_session)
    stranger = register(client)
    response = client.post(
        f"/api/v1/groups/{group_json['id']}/ask",
        json={"question": QUESTION}, headers=bearer(stranger),
    )
    assert response.status_code == 404
