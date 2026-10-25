"""Search across planning (B-092) + planning digest data
(B-093).

One search over events, tasks, decisions, questions — ranked
deterministically (primary text outweighs secondary; starting
with the term outweighs containing it), every hit linked back
to its candidate/source. The digest is the "state of the
plan": this week's events, unclaimed tasks, open questions,
undecided polls — pinned by a golden snapshot at a fixed
moment so the week window is exact.
"""

import json
from datetime import datetime

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.groups import Candidate, Group
from gpa.services import planning as planning_service
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def _event(client, world, title, starts, *, status="published", description=""):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json={
            "title": title, "starts_at": starts,
            "ends_at": "2026-11-06T21:00:00-05:00",
            "status": status, "description": description,
        },
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_search_golden_ranking_and_types(client, world):
    """B-092's golden: 'harvest' lives in four kinds of thing.
    The event whose title STARTS with the word outranks the
    three whose text merely contains it; ties break by type,
    then title — the order is exact, not vibes."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    _event(client, world, "Harvest Supper", "2026-11-06T18:30:00-05:00",
           description="Bring harvest food")
    client.post(f"/api/v1/groups/{gid}/tasks",
                json={"title": "Book the harvest band"}, headers=headers)
    client.post(f"/api/v1/groups/{gid}/decisions",
                json={"text": "We harvest on the Sunday"}, headers=headers)
    client.post(f"/api/v1/groups/{gid}/questions",
                json={"text": "Who brings harvest cider?"}, headers=headers)
    client.post(f"/api/v1/groups/{gid}/tasks",
                json={"title": "Fix the fence"}, headers=headers)

    results = client.get(f"/api/v1/groups/{gid}/search?q=harvest", headers=headers)
    assert results.status_code == 200, results.text
    hits = results.json()["results"]
    assert [hit["type"] for hit in hits] == ["event", "decision", "question", "task"]
    assert hits[0]["title"] == "Harvest Supper"
    assert hits[0]["score"] > hits[1]["score"]
    assert hits[1]["score"] == hits[2]["score"] == hits[3]["score"]

    fence = client.get(f"/api/v1/groups/{gid}/search?q=fence", headers=headers).json()
    assert [(hit["type"], hit["title"]) for hit in fence["results"]] == [
        ("task", "Fix the fence")
    ]
    nothing = client.get(f"/api/v1/groups/{gid}/search?q=zeppelin", headers=headers).json()
    assert nothing["results"] == []


def test_search_hit_links_back_to_its_source(client, world, db_session):
    """A minted entity's hit carries its candidate and the
    source the candidate was drawn from (B-092's link)."""
    gid = world["group"]["id"]
    draft = {
        "kind": "task",
        "fields": {
            "title": {"value": "Bring the projector", "confidence": 0.9,
                      "quote": "projector", "supported": True},
        },
        "title": "Bring the projector",
    }
    row = Candidate(
        group_id=gid, kind="task", title="Bring the projector",
        created_by="system:extraction", status="pending",
        draft_json=json.dumps(draft), confidence=0.9, source_id="source-xyz",
    )
    db_session.add(row)
    db_session.commit()
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{row.id}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    hits = client.get(
        f"/api/v1/groups/{gid}/search?q=projector", headers=bearer(world["owner"])
    ).json()["results"]
    assert len(hits) == 1
    assert hits[0]["candidate_id"] == row.id
    assert hits[0]["source_id"] == "source-xyz"


def test_digest_golden_snapshot(client, world, db_session):
    """B-093's golden at a fixed moment — Wednesday
    2026-10-21, New York: the week is Mon Oct 19 → Mon Oct 26
    local. In: the Friday event, the unclaimed task, the open
    question, the open poll. Out: next week's event, the
    draft, the claimed task, the resolved question, the
    closed poll."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    _event(client, world, "Friday supper", "2026-10-23T18:30:00-04:00")
    _event(client, world, "Next week supper", "2026-10-30T18:30:00-04:00")
    _event(client, world, "Draft supper", "2026-10-22T18:30:00-04:00", status="draft")
    client.post(f"/api/v1/groups/{gid}/tasks",
                json={"title": "Unclaimed job"}, headers=headers)
    client.post(f"/api/v1/groups/{gid}/tasks",
                json={"title": "Claimed job", "owner_id": world["member"]["user_id"]},
                headers=headers)
    open_q = client.post(f"/api/v1/groups/{gid}/questions",
                         json={"text": "Who brings cups?"}, headers=headers).json()
    settled_q = client.post(f"/api/v1/groups/{gid}/questions",
                            json={"text": "Settled already"}, headers=headers).json()
    client.post(f"/api/v1/groups/{gid}/questions/{settled_q['id']}/resolve",
                json={}, headers=headers)
    open_poll = client.post(
        f"/api/v1/groups/{gid}/polls",
        json={"question": "Pizza or tacos?",
              "options": [{"label": "Pizza"}, {"label": "Tacos"}]},
        headers=headers,
    ).json()
    closed_poll = client.post(
        f"/api/v1/groups/{gid}/polls",
        json={"question": "Soup or salad?",
              "options": [{"label": "Soup"}, {"label": "Salad"}]},
        headers=headers,
    ).json()
    client.post(f"/api/v1/groups/{gid}/polls/{closed_poll['id']}/close", headers=headers)

    group_row = db_session.get(Group, gid)
    digest = planning_service.planning_digest(
        db_session, group_row, now=datetime(2026, 10, 21, 12, 0)
    )
    assert digest["week_start"] == datetime(2026, 10, 19, 4, 0)   # Mon 00:00 EDT
    assert digest["week_end"] == datetime(2026, 10, 26, 4, 0)
    assert [event.title for event in digest["events"]] == ["Friday supper"]
    assert [task.title for task in digest["unclaimed_tasks"]] == ["Unclaimed job"]
    assert [q.id for q in digest["open_questions"]] == [open_q["id"]]
    assert [poll.id for poll in digest["undecided_polls"]] == [open_poll["id"]]


def test_digest_endpoint_shape(client, world):
    """The HTTP surface carries the same four sections."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    client.post(f"/api/v1/groups/{gid}/tasks",
                json={"title": "Loose end"}, headers=headers)
    body = client.get(f"/api/v1/groups/{gid}/planning-digest", headers=headers)
    assert body.status_code == 200, body.text
    data = body.json()
    assert set(data) == {
        "week_start", "week_end", "events_this_week",
        "unclaimed_tasks", "open_questions", "undecided_polls",
    }
    assert [task["title"] for task in data["unclaimed_tasks"]] == ["Loose end"]
    assert data["unclaimed_tasks"][0]["unclaimed"] is True
