"""Decisions log (roadmap B-085).

Minted from approved decision candidates or recorded directly.
A later decision SUPERSEDES an earlier one: both rows are kept,
the chain links both ways and is queryable, and
``?current=true`` is the group's settled truth.
"""

import json

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.groups import Candidate
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def _decide(client, world, text, **extra):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/decisions",
        json={"text": text, **extra}, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_minted_decision_from_candidate(client, world, db_session):
    gid = world["group"]["id"]
    draft = {
        "kind": "decision",
        "fields": {
            "text": {
                "value": "the quiz is postponed to November",
                "confidence": 0.92, "quote": "postponed", "supported": True,
            }
        },
        "text": "the quiz is postponed to November",
    }
    row = Candidate(
        group_id=gid, kind="decision", title="Quiz postponed",
        created_by="system:extraction", status="pending",
        draft_json=json.dumps(draft), confidence=0.92,
    )
    db_session.add(row)
    db_session.commit()
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{row.id}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["resulting_entity_type"] == "decision"
    decision = client.get(
        f"/api/v1/groups/{gid}/decisions/{approved.json()['resulting_entity_id']}",
        headers=bearer(world["owner"]),
    ).json()
    assert decision["text"] == "the quiz is postponed to November"
    assert decision["status"] == "current"
    assert decision["candidate_id"] == row.id


def test_supersede_chain_and_settled_truth(client, world):
    """B-085's core test: A is replaced by B is replaced by C.
    History keeps all three; current truth is C alone; the
    chain from any member of it reads oldest → newest."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    first = _decide(client, world, "We meet on Thursdays")
    second = _decide(client, world, "We meet on Fridays", supersedes_id=first["id"])
    third = _decide(client, world, "We meet on Saturdays", supersedes_id=second["id"])

    assert second["supersedes_id"] == first["id"]
    assert third["supersedes_id"] == second["id"]

    everything = client.get(f"/api/v1/groups/{gid}/decisions", headers=headers).json()
    by_id = {row["id"]: row for row in everything}
    assert by_id[first["id"]]["status"] == "superseded"
    assert by_id[first["id"]]["superseded_by_id"] == second["id"]
    assert by_id[second["id"]]["status"] == "superseded"
    assert by_id[second["id"]]["superseded_by_id"] == third["id"]
    assert by_id[third["id"]]["status"] == "current"

    current = client.get(
        f"/api/v1/groups/{gid}/decisions?current=true", headers=headers
    ).json()
    assert [row["id"] for row in current] == [third["id"]]

    chain = client.get(
        f"/api/v1/groups/{gid}/decisions/{second['id']}/chain", headers=headers
    ).json()
    assert [row["id"] for row in chain] == [first["id"], second["id"], third["id"]]
    assert [row["text"] for row in chain] == [
        "We meet on Thursdays", "We meet on Fridays", "We meet on Saturdays",
    ]


def test_superseding_a_superseded_decision_is_refused(client, world):
    first = _decide(client, world, "Original")
    _decide(client, world, "Replacement", supersedes_id=first["id"])
    refused = client.post(
        f"/api/v1/groups/{world['group']['id']}/decisions",
        json={"text": "Sneaky", "supersedes_id": first["id"]},
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 409


def test_decisions_are_group_scoped(client, world):
    """Another group's decision is a 404, never a leak."""
    other_owner = register(client)
    other_group = make_group(client, other_owner, home_timezone=NY)
    mine = _decide(client, world, "Ours alone")
    foreign = client.get(
        f"/api/v1/groups/{other_group['id']}/decisions/{mine['id']}",
        headers=bearer(other_owner),
    )
    assert foreign.status_code == 404
