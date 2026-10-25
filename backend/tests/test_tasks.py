"""Tasks (roadmap B-083).

Minted from approved task candidates or created directly; the
owner law is the slice's heart: the extraction's owner WORDS
("Ben volunteered") are a suggestion on the task, never an
assignment — an assignment exists only where a person made it
(a direct create naming the owner, a reviewer editing the
draft to name a member, or a later PATCH). Overdue is computed
server-side; the status flow is forward, cancel, reopen.
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


def _field(value, **extra):
    return {"value": value, "confidence": 0.9, "quote": "words", "supported": True, **extra}


def _plant_task_candidate(db_session, group_id, *, title="Bring the projector", fields=None, source_id=None):
    """A pipeline-shaped task candidate, planted directly."""
    fields = fields or {"title": _field(title), "owner": _field("Ben")}
    draft = {"kind": "task", "fields": fields}
    for name, entry in fields.items():
        if entry.get("value") is not None:
            draft[name] = entry["value"]
    row = Candidate(
        group_id=group_id, kind="task", title=title,
        created_by="system:extraction", status="pending",
        draft_json=json.dumps(draft), confidence=0.9, source_id=source_id,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _create_event(client, world, **overrides):
    body = {
        "title": "Harvest Supper",
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


def test_direct_create_lists_and_owner(client, world):
    gid = world["group"]["id"]
    event = _create_event(client, world)
    created = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={
            "title": "Book the band", "event_id": event["id"],
            "owner_id": world["member"]["user_id"],
            "due_at": "2026-11-01T17:00:00-05:00",
        },
        headers=bearer(world["owner"]),
    )
    assert created.status_code == 201, created.text
    task = created.json()
    # A person named the owner at creation: that IS an assignment.
    assert task["owner_id"] == world["member"]["user_id"]
    assert task["owner_label"]  # the member's display words
    assert task["unclaimed"] is False
    assert task["overdue"] is False
    assert task["status"] == "open"

    loose = client.post(
        f"/api/v1/groups/{gid}/tasks", json={"title": "Loose end"},
        headers=bearer(world["member"]),
    )
    assert loose.status_code == 201
    assert loose.json()["unclaimed"] is True

    per_event = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/tasks",
        headers=bearer(world["owner"]),
    )
    assert [row["title"] for row in per_event.json()] == ["Book the band"]

    per_group = client.get(f"/api/v1/groups/{gid}/tasks", headers=bearer(world["owner"]))
    assert {row["title"] for row in per_group.json()} == {"Book the band", "Loose end"}

    unclaimed = client.get(
        f"/api/v1/groups/{gid}/tasks?unclaimed=true", headers=bearer(world["owner"])
    )
    assert [row["title"] for row in unclaimed.json()] == ["Loose end"]

    mine = client.get(
        f"/api/v1/groups/{gid}/tasks?owner_id={world['member']['user_id']}",
        headers=bearer(world["owner"]),
    )
    assert [row["title"] for row in mine.json()] == ["Book the band"]


def test_create_with_nonmember_owner_is_refused(client, world):
    gid = world["group"]["id"]
    refused = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Nope", "owner_id": "not-a-member"},
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 422


def test_minted_task_owner_words_never_assign(client, world, db_session):
    """B-083's core test: the draft says owner "Ben" — words
    from the chat. Approving mints the task with those words as
    a SUGGESTION and nobody assigned. No silent victim."""
    gid = world["group"]["id"]
    candidate = _plant_task_candidate(db_session, gid)
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["resulting_entity_type"] == "task"
    task_id = approved.json()["resulting_entity_id"]

    task = client.get(
        f"/api/v1/groups/{gid}/tasks/{task_id}", headers=bearer(world["owner"])
    ).json()
    assert task["title"] == "Bring the projector"
    assert task["suggested_owner_label"] == "Ben"
    assert task["owner_id"] is None
    assert task["owner_label"] == ""
    assert task["unclaimed"] is True
    assert task["candidate_id"] == candidate.id


def test_mint_with_reviewer_named_owner_assigns(client, world, db_session):
    """The one draft road to assignment: a PERSON edits the
    draft to name a real member (owner_id), then approves."""
    gid = world["group"]["id"]
    candidate = _plant_task_candidate(db_session, gid)
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}",
        headers=bearer(world["owner"]),
    ).json()
    draft = dict(detail["draft"])
    draft["owner_id"] = world["member"]["user_id"]
    edited = client.patch(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}",
        json={"draft": draft}, headers=bearer(world["owner"]),
    )
    assert edited.status_code == 200, edited.text
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    task = client.get(
        f"/api/v1/groups/{gid}/tasks/{approved.json()['resulting_entity_id']}",
        headers=bearer(world["owner"]),
    ).json()
    assert task["owner_id"] == world["member"]["user_id"]
    assert task["unclaimed"] is False
    # The words stay a suggestion alongside the real assignment.
    assert task["suggested_owner_label"] == "Ben"


def test_mint_refuses_a_draft_naming_a_nonmember(client, world, db_session):
    gid = world["group"]["id"]
    candidate = _plant_task_candidate(db_session, gid)
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}",
        headers=bearer(world["owner"]),
    ).json()
    draft = dict(detail["draft"])
    draft["owner_id"] = "ghost-not-in-group"
    client.patch(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}",
        json={"draft": draft}, headers=bearer(world["owner"]),
    )
    refused = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}/approve",
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 422, refused.text
    # The whole approval rolled back: still pending, no task.
    after = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}",
        headers=bearer(world["owner"]),
    ).json()
    assert after["status"] == "edited"
    tasks = client.get(f"/api/v1/groups/{gid}/tasks", headers=bearer(world["owner"]))
    assert tasks.json() == []


def test_minted_task_due_date_is_end_of_local_day(client, world, db_session):
    """A draft whose date resolved to a day only ("due Friday")
    is due at that day's end, group-local (recorded decision)."""
    gid = world["group"]["id"]
    fields = {
        "title": _field("Sort the playlist"),
        "due": {"value": None, "confidence": 0.8, "quote": "by Friday", "supported": True},
    }
    candidate = _plant_task_candidate(
        db_session, gid, title="Sort the playlist", fields=fields
    )
    # The resolver's mirrors ride on the draft.
    row = db_session.get(Candidate, candidate.id)
    draft = json.loads(row.draft_json)
    draft["due_date"] = "2026-10-23"
    row.draft_json = json.dumps(draft)
    db_session.commit()
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    task = client.get(
        f"/api/v1/groups/{gid}/tasks/{approved.json()['resulting_entity_id']}",
        headers=bearer(world["owner"]),
    ).json()
    # Friday 2026-10-23 23:59:59 in New York (EDT, UTC-4) = 03:59:59Z.
    assert task["due_at"] == "2026-10-24T03:59:59Z"


def test_overdue_is_computed_not_stored(client, world):
    gid = world["group"]["id"]
    past = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Late thing", "due_at": "2020-01-01T09:00:00Z"},
        headers=bearer(world["owner"]),
    ).json()
    assert past["overdue"] is True
    future = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Future thing", "due_at": "2999-01-01T09:00:00Z"},
        headers=bearer(world["owner"]),
    ).json()
    assert future["overdue"] is False
    # Done is never overdue, however late it finished.
    done = client.patch(
        f"/api/v1/groups/{gid}/tasks/{past['id']}",
        json={"status": "done"}, headers=bearer(world["owner"]),
    ).json()
    assert done["overdue"] is False
    assert done["completed_at"] is not None


def test_status_flow_forward_cancel_reopen(client, world):
    gid = world["group"]["id"]
    task = client.post(
        f"/api/v1/groups/{gid}/tasks", json={"title": "Flow"},
        headers=bearer(world["owner"]),
    ).json()
    headers = bearer(world["owner"])
    for status in ("in_progress", "done"):
        moved = client.patch(
            f"/api/v1/groups/{gid}/tasks/{task['id']}",
            json={"status": status}, headers=headers,
        )
        assert moved.status_code == 200, moved.text
    # Done cannot be cancelled — the flow refuses in plain words.
    refused = client.patch(
        f"/api/v1/groups/{gid}/tasks/{task['id']}",
        json={"status": "cancelled"}, headers=headers,
    )
    assert refused.status_code == 409
    reopened = client.patch(
        f"/api/v1/groups/{gid}/tasks/{task['id']}",
        json={"status": "open"}, headers=headers,
    )
    assert reopened.status_code == 200
    assert reopened.json()["completed_at"] is None


def test_assign_and_unassign_by_patch(client, world):
    gid = world["group"]["id"]
    task = client.post(
        f"/api/v1/groups/{gid}/tasks", json={"title": "Claim me"},
        headers=bearer(world["owner"]),
    ).json()
    assigned = client.patch(
        f"/api/v1/groups/{gid}/tasks/{task['id']}",
        json={"owner_id": world["member"]["user_id"]},
        headers=bearer(world["owner"]),
    ).json()
    assert assigned["unclaimed"] is False
    unassigned = client.patch(
        f"/api/v1/groups/{gid}/tasks/{task['id']}",
        json={"owner_id": None}, headers=bearer(world["owner"]),
    ).json()
    assert unassigned["owner_id"] is None
    assert unassigned["unclaimed"] is True
