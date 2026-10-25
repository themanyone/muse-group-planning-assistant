"""Open questions (roadmap B-086).

Minted from approved question candidates or asked directly;
a question can be asked OF a named member; resolving one can
mint its answer as a decision in the same call. The event
gaps view — "what is still missing?" — aggregates missing
fields, open questions, unclaimed roles, unclaimed tasks.
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


def _ask(client, world, text, **extra):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/questions",
        json={"text": text, **extra}, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _event(client, world, **overrides):
    body = {
        "title": "Half-planned supper",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:00:00-05:00",
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json=body, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_minted_question_from_candidate(client, world, db_session):
    gid = world["group"]["id"]
    draft = {
        "kind": "question",
        "fields": {
            "text": {
                "value": "who is doing the door rota this time?",
                "confidence": 0.9, "quote": "door rota", "supported": True,
            }
        },
        "text": "who is doing the door rota this time?",
    }
    row = Candidate(
        group_id=gid, kind="question", title="Door rota?",
        created_by="system:extraction", status="pending",
        draft_json=json.dumps(draft), confidence=0.9,
    )
    db_session.add(row)
    db_session.commit()
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{row.id}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["resulting_entity_type"] == "question"
    question = client.get(
        f"/api/v1/groups/{gid}/questions/{approved.json()['resulting_entity_id']}",
        headers=bearer(world["owner"]),
    ).json()
    assert question["text"] == "who is doing the door rota this time?"
    assert question["status"] == "open"
    # Minted questions are asked of the group — naming who must
    # answer is a person's act, never the machine's.
    assert question["asked_of_id"] is None


def test_ask_assign_and_drop(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    question = _ask(client, world, "Who is bringing dessert?")
    assert question["asked_of_id"] is None
    assigned = client.patch(
        f"/api/v1/groups/{gid}/questions/{question['id']}",
        json={"asked_of_id": world["member"]["user_id"]}, headers=headers,
    ).json()
    assert assigned["asked_of_id"] == world["member"]["user_id"]
    assert assigned["asked_of_label"]
    refused = client.patch(
        f"/api/v1/groups/{gid}/questions/{question['id']}",
        json={"asked_of_id": "not-a-member"}, headers=headers,
    )
    assert refused.status_code == 422
    dropped = client.patch(
        f"/api/v1/groups/{gid}/questions/{question['id']}",
        json={"drop": True}, headers=headers,
    ).json()
    assert dropped["status"] == "dropped"
    # A settled question cannot be edited further.
    refused = client.patch(
        f"/api/v1/groups/{gid}/questions/{question['id']}",
        json={"text": "changed"}, headers=headers,
    )
    assert refused.status_code == 409


def test_resolve_mints_a_decision_in_the_same_call(client, world):
    """B-086's core test: one resolve call closes the question
    AND records the answer as the group's settled truth."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    question = _ask(client, world, "Where do we hold the supper?")
    resolved = client.post(
        f"/api/v1/groups/{gid}/questions/{question['id']}/resolve",
        json={"decision_text": "The supper is at the village hall"},
        headers=headers,
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None
    decision_id = body["resolution_decision_id"]
    assert decision_id
    current = client.get(
        f"/api/v1/groups/{gid}/decisions?current=true", headers=headers
    ).json()
    assert [row["id"] for row in current] == [decision_id]
    assert current[0]["text"] == "The supper is at the village hall"
    # Resolving twice is refused.
    again = client.post(
        f"/api/v1/groups/{gid}/questions/{question['id']}/resolve",
        json={}, headers=headers,
    )
    assert again.status_code == 409


def test_resolve_without_decision_text(client, world):
    gid = world["group"]["id"]
    question = _ask(client, world, "Settled in chat already")
    resolved = client.post(
        f"/api/v1/groups/{gid}/questions/{question['id']}/resolve",
        json={}, headers=bearer(world["owner"]),
    ).json()
    assert resolved["status"] == "resolved"
    assert resolved["resolution_decision_id"] is None


def test_gaps_golden_half_planned_event(client, world):
    """B-086's golden: an event with no place and no host, one
    open question, one unclaimed role, one unclaimed task (and
    one claimed task that must NOT appear) yields exactly the
    five gaps, in order."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    event = _event(client, world)  # no place_name, no host
    _ask(client, world, "Is the hall accessible?", event_id=event["id"])
    roles = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/roles",
        json={"role_name": "cook", "slots": 1}, headers=headers,
    )
    assert roles.status_code == 201, roles.text
    client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Buy the leeks", "event_id": event["id"]}, headers=headers,
    )
    client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={
            "title": "Owned job", "event_id": event["id"],
            "owner_id": world["member"]["user_id"],
        },
        headers=headers,
    )
    gaps = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/gaps", headers=headers
    )
    assert gaps.status_code == 200, gaps.text
    body = gaps.json()
    assert body["missing_fields"] == ["place", "host"]
    assert [gap["kind"] for gap in body["gaps"]] == [
        "missing_field", "missing_field",
        "open_question", "unclaimed_role", "unclaimed_task",
    ]
    assert [q["text"] for q in body["open_questions"]] == ["Is the hall accessible?"]
    assert [s["role_name"] for s in body["unclaimed_roles"]] == ["cook"]
    assert [t["title"] for t in body["unclaimed_tasks"]] == ["Buy the leeks"]

    # Fill the gaps and they close: place + host named, role
    # claimed, task claimed, question resolved.
    patched = client.patch(
        f"/api/v1/groups/{gid}/events/{event['id']}",
        json={"place_name": "Village Hall", "host_label": "Ana"},
        headers={**headers, "If-Match": event["etag"]},
    )
    assert patched.status_code == 200, patched.text
    slot_id = body["unclaimed_roles"][0]["id"]
    client.post(
        f"/api/v1/groups/{gid}/roles/{slot_id}/claim",
        headers=bearer(world["member"]),
    )
    task_id = body["unclaimed_tasks"][0]["id"]
    client.patch(
        f"/api/v1/groups/{gid}/tasks/{task_id}",
        json={"owner_id": world["member"]["user_id"]}, headers=headers,
    )
    question_id = body["open_questions"][0]["id"]
    client.post(
        f"/api/v1/groups/{gid}/questions/{question_id}/resolve",
        json={}, headers=headers,
    )
    after = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/gaps", headers=headers
    ).json()
    assert after["gaps"] == []
    assert after["missing_fields"] == []
