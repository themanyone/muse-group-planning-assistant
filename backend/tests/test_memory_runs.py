"""Memory transparency (roadmap B-120).

Every prune/ask run is inspectable — question, kept,
dropped, model, cost — and a group can purge its run
records. The law under test: purging the receipts never
deletes the group's words (summaries stay), and one group's
purge never touches another group's ledger.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai import pruning
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.models.groups import Group
from gpa.services import memory as memory_service
from test_ask import record_ask, world
from test_groups import invite_and_accept
from conftest import unique_email
from test_pruning import record_judgements

QUESTION = "Where do club nights happen?"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    ai_client.set_client(None)


def world_with_runs(client, db_session):
    """A group with two runs on its ledger: one ask, one prune."""
    owner, group_json, group, corpus = world(client, db_session, passes=1)
    driver = FixtureDriver()
    record_judgements(driver, QUESTION, corpus, {1})
    record_ask(
        driver, QUESTION, [corpus[1]],
        {
            "answer": "Club nights use the hall.",
            "citations": [
                {"quote": "the hall, not the school gym, for club nights"}
            ],
        },
    )
    record_judgements(driver, "What food is planned?", corpus, set())
    ai_client.set_client(driver)

    asked = client.post(
        f"/api/v1/groups/{group_json['id']}/ask",
        json={"question": QUESTION}, headers=bearer(owner),
    )
    assert asked.status_code == 200, asked.text
    memory_service.run_prune(
        db_session, actor_label=f"user:{owner['user_id']}", group=group,
        question="What food is planned?", purpose="prune",
    )
    return owner, group_json, group, asked.json()


def runs_url(group_json):
    return f"/api/v1/groups/{group_json['id']}/memory/runs"


def test_runs_are_inspectable(client, db_session):
    owner, group_json, group, asked = world_with_runs(client, db_session)
    response = client.get(runs_url(group_json), headers=bearer(owner))
    assert response.status_code == 200, response.text
    runs = response.json()
    assert len(runs) == 2
    assert {r["purpose"] for r in runs} == {"ask", "prune"}
    ask_run = next(r for r in runs if r["purpose"] == "ask")
    assert ask_run["id"] == asked["prune_run_id"]
    assert ask_run["question"] == QUESTION
    assert len(ask_run["kept_chunk_ids"]) == 1
    assert len(ask_run["dropped_chunk_ids"]) == 2
    assert ask_run["model_id"]
    assert "cost_micros" in ask_run


def test_purge_one_then_all_and_summaries_survive(client, db_session):
    owner, group_json, group, asked = world_with_runs(client, db_session)
    runs = client.get(runs_url(group_json), headers=bearer(owner)).json()
    victim = runs[0]["id"]

    deleted = client.delete(
        f"{runs_url(group_json)}/{victim}", headers=bearer(owner)
    )
    assert deleted.status_code == 204, deleted.text
    remaining = client.get(runs_url(group_json), headers=bearer(owner)).json()
    assert len(remaining) == 1

    # The group's words stay: the ask's summary still reads.
    summaries = client.get(
        f"/api/v1/groups/{group_json['id']}/summaries", headers=bearer(owner)
    )
    assert any(s["id"] == asked["summary_id"] for s in summaries.json())

    wiped = client.delete(runs_url(group_json), headers=bearer(owner))
    assert wiped.status_code == 204, wiped.text
    assert client.get(runs_url(group_json), headers=bearer(owner)).json() == []


def test_purge_needs_organiser_and_stays_in_group(client, db_session):
    owner, group_json, group, asked = world_with_runs(client, db_session)
    member = invite_and_accept(
        client, owner, group_json["id"], unique_email(), "member"
    )
    runs = client.get(runs_url(group_json), headers=bearer(owner)).json()

    refused = client.delete(
        f"{runs_url(group_json)}/{runs[0]['id']}", headers=bearer(member)
    )
    assert refused.status_code == 403, refused.text

    # Another group's organiser cannot purge this group's run:
    # the id is simply not found under their group.
    other_owner = register(client)
    other_group = make_group(client, other_owner)
    missing = client.delete(
        f"/api/v1/groups/{other_group['id']}/memory/runs/{runs[0]['id']}",
        headers=bearer(other_owner),
    )
    assert missing.status_code == 404, missing.text
    assert len(client.get(runs_url(group_json), headers=bearer(owner)).json()) == 2
