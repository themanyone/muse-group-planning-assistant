"""Event briefings (roadmap B-117).

The acceptance is the landmine: a warning dropped into the
group's chat about three months before the event — phrased
nothing like the event itself — must survive pruning over the
approved history and land in the host's briefing. Plus the
structured present: the past decision and the unclaimed role.
"""

import json
from datetime import datetime

import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai import promptlib, pruning
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.memory import PruneRun
from gpa.models.planning import Decision, RoleSlot
from gpa.services import memory as memory_service
from gpa.services import sources as source_service
from test_pruning import record_judgements

LANDMINE = (
    "Careful: the marquee flooded the last time it rained at "
    "Greenfield, bring the groundsheet and raise the floor."
)
DECISION_TEXT = "we will use the marquee at Greenfield for the autumn social"
BRIEFING_TEXT = (
    "Briefing for the Autumn Social. Warning from an earlier "
    "chat: the marquee flooded the last time it rained at "
    "Greenfield — bring the groundsheet and raise the floor. "
    "The group decided to use the marquee at Greenfield for "
    "the autumn social. The gate keeper role is still open."
)


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    ai_client.set_client(None)


def world(client, db_session):
    owner = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])
    label = f"user:{owner['user_id']}"
    # The history, planted in August for a November event.
    source_service.create_source(
        db_session, actor_label=label, group=group, kind="chat_paste",
        title="Summer chat", granted_scope="group-history",
        chunks=[
            {"text": "The spring picnic rota is settled, thanks everyone.",
             "sender": "Ana", "sent_at": datetime(2026, 8, 3, 19, 0)},
            {"text": LANDMINE, "sender": "Ben",
             "sent_at": datetime(2026, 8, 3, 19, 5)},
            {"text": "Quiz scores from July are finally posted.",
             "sender": "Cy", "sent_at": datetime(2026, 8, 3, 19, 9)},
            {"text": "Right, settled: we will use the marquee at "
                     "Greenfield for the autumn social, rain or shine.",
             "sender": "Ana", "sent_at": datetime(2026, 8, 3, 19, 12)},
        ],
    )
    db_session.add(
        Decision(
            group_id=group.id, text=DECISION_TEXT, status="current",
            created_by=label,
        )
    )
    db_session.commit()
    created = client.post(
        f"/api/v1/groups/{group_json['id']}/events",
        json={
            "title": "Autumn Social",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "place_name": "Greenfield Farm",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    event_json = created.json()
    db_session.add(
        RoleSlot(
            group_id=group.id, event_id=event_json["id"],
            role_name="Gate keeper", created_by=label,
        )
    )
    db_session.commit()
    corpus = memory_service.approved_corpus(db_session, group)
    event = db_session.get(Event, event_json["id"])
    landmine_id = next(
        c.chunk_id for c in corpus if LANDMINE in c.text
    )
    return owner, group_json, group, event_json, event, corpus, landmine_id


def test_briefing_carries_the_landmine(client, db_session):
    owner, group_json, group, event_json, event, corpus, landmine_id = world(
        client, db_session
    )
    driver = FixtureDriver()
    terms = memory_service._event_terms(group, event)
    landmine_position = next(
        c.position for c in corpus if c.chunk_id == landmine_id
    )
    decision_position = next(
        c.position for c in corpus if c.origin == "decision"
    )
    record_judgements(
        driver, terms, corpus, {landmine_position, decision_position}
    )
    kept = [
        c for c in corpus
        if c.position in {landmine_position, decision_position}
    ]
    event_block = "\n".join(
        [
            f"{event.title} — {event.starts_at.isoformat()} ({event.timezone})",
            f"Place: {event.place_name}",
            f"Past decisions: {DECISION_TEXT}",
            "Roles still unclaimed: Gate keeper",
        ]
    )
    briefing_template = promptlib.load_prompt("briefing")
    driver.add_json(
        briefing_template.render(
            EVENT=event_block, CHUNKS=pruning.render_chunks(kept)
        ),
        briefing_template.schema,
        {"text": BRIEFING_TEXT, "open_questions": []},
    )
    ai_client.set_client(driver)

    response = client.post(
        f"/api/v1/groups/{group_json['id']}/events/{event_json['id']}/briefing",
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["summary"]["kind"] == "event_brief"
    # THE landmine, in the briefing the host reads.
    assert "marquee flooded" in body["summary"]["body"]
    assert body["role_gaps"] == ["Gate keeper"]

    # And the run's kept set names the landmine chunk — the
    # briefing's receipt, visible under B-120's transparency.
    run = db_session.get(PruneRun, body["prune_run_id"])
    assert run.purpose == "brief"
    assert landmine_id in json.loads(run.kept_json)
