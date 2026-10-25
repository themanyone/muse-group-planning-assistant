"""Templates (roadmap B-091).

Save an event and its plan as a template; instantiate it at
new dates; the global starter set (meeting, club night, trip,
fundraiser, class, match, celebration) ships as seed. The
round-trip test pins the shape-shifting: due moments become
day-offsets and come back shifted by exactly those offsets;
claims never travel — a template carries shapes, not promises.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.services.seed import load_seed_data
from test_groups import invite_and_accept

NY = "America/New_York"
STARTERS = {"meeting", "club night", "trip", "fundraiser", "class", "match", "celebration"}


@pytest.fixture()
def world(client, db_session):
    load_seed_data(db_session, {})
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def test_starter_set_ships_as_seed(client, world):
    gid = world["group"]["id"]
    listed = client.get(
        f"/api/v1/groups/{gid}/templates", headers=bearer(world["owner"])
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert {row["name"] for row in rows} == STARTERS
    assert all(row["is_starter"] is True for row in rows)
    assert all(row["group_id"] is None for row in rows)


def test_instantiate_a_starter(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    starters = client.get(f"/api/v1/groups/{gid}/templates", headers=headers).json()
    meeting = next(row for row in starters if row["name"] == "meeting")
    built = client.post(
        f"/api/v1/groups/{gid}/templates/{meeting['id']}/instantiate",
        json={"starts_at": "2026-11-10T10:00:00-05:00", "title": "AGM planning"},
        headers=headers,
    )
    assert built.status_code == 201, built.text
    body = built.json()
    event = client.get(
        f"/api/v1/groups/{gid}/events/{body['event_id']}", headers=headers
    ).json()
    assert event["title"] == "AGM planning"
    assert event["status"] == "published"
    assert event["starts_at"] == "2026-11-10T15:00:00Z"
    assert event["ends_at"] == "2026-11-10T16:00:00Z"  # 60-minute shape
    tasks = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/tasks", headers=headers
    ).json()
    by_title = {row["title"]: row for row in tasks}
    assert set(by_title) == {"Circulate the agenda", "Book the room"}
    # Agenda is due 2 days before; the room 5 — offsets honoured.
    assert by_title["Circulate the agenda"]["due_at"] == "2026-11-08T15:00:00Z"
    assert by_title["Book the room"]["due_at"] == "2026-11-05T15:00:00Z"
    # Instantiation assigns nobody, ever.
    assert all(row["unclaimed"] is True for row in tasks)
    roles = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/roles", headers=headers
    ).json()
    assert {row["role_name"] for row in roles} == {"chair", "note-taker"}


def test_template_round_trip_from_event(client, world):
    """B-091's round-trip: build a plan by hand, save it as a
    template, instantiate it a month later — the same plan,
    dates shifted, claims reset to open."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    event = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Harvest Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:30:00-05:00",
            "place_name": "Village Hall",
        },
        headers=headers,
    ).json()
    task = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={
            "title": "Order the leeks", "event_id": event["id"],
            "due_at": "2026-11-04T09:00:00-05:00",
            "owner_id": world["member"]["user_id"],
        },
        headers=headers,
    ).json()
    assert task["unclaimed"] is False
    client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/checklist",
        json={"label": "Candles", "quantity": 2}, headers=headers,
    )
    client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/roles",
        json={"role_name": "cook", "slots": 2}, headers=headers,
    )

    saved = client.post(
        f"/api/v1/groups/{gid}/templates/from-event/{event['id']}",
        json={"name": "supper shape"}, headers=headers,
    )
    assert saved.status_code == 201, saved.text
    template = saved.json()
    assert template["is_starter"] is False
    payload = template["payload"]
    assert payload["event"]["title"] == "Harvest Supper"
    assert payload["event"]["duration_minutes"] == 180
    # The due moment became an offset: Nov 4 is 2 days before Nov 6.
    assert payload["tasks"][0]["due_offset_days"] == -2
    assert payload["roles"] == [{"role_name": "cook", "slots": 2}]

    built = client.post(
        f"/api/v1/groups/{gid}/templates/{template['id']}/instantiate",
        json={"starts_at": "2026-12-04T18:30:00-05:00"}, headers=headers,
    )
    assert built.status_code == 201, built.text
    new_event_id = built.json()["event_id"]
    new_event = client.get(
        f"/api/v1/groups/{gid}/events/{new_event_id}", headers=headers
    ).json()
    assert new_event["starts_at"] == "2026-12-04T23:30:00Z"
    assert new_event["ends_at"] == "2026-12-05T02:30:00Z"
    assert new_event["place_name"] == "Village Hall"
    new_tasks = client.get(
        f"/api/v1/groups/{gid}/events/{new_event_id}/tasks", headers=headers
    ).json()
    assert len(new_tasks) == 1
    assert new_tasks[0]["due_at"] == "2026-12-02T23:30:00Z"  # start - 2 days
    # The original task was claimed; the template's copy is not.
    assert new_tasks[0]["unclaimed"] is True
    items = client.get(
        f"/api/v1/groups/{gid}/events/{new_event_id}/checklist", headers=headers
    ).json()
    assert [(row["label"], row["quantity"]) for row in items] == [("Candles", 2)]
    roles = client.get(
        f"/api/v1/groups/{gid}/events/{new_event_id}/roles", headers=headers
    ).json()
    assert len(roles) == 2
    assert all(row["claimed"] is False for row in roles)


def test_duplicate_name_and_foreign_template(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    payload = {"event": {"title": "X", "duration_minutes": 30}}
    first = client.post(
        f"/api/v1/groups/{gid}/templates",
        json={"name": "mine", "payload": payload}, headers=headers,
    )
    assert first.status_code == 201, first.text
    again = client.post(
        f"/api/v1/groups/{gid}/templates",
        json={"name": "mine", "payload": payload}, headers=headers,
    )
    assert again.status_code == 409
    # Another group cannot even see it.
    other_owner = register(client)
    other_group = make_group(client, other_owner, home_timezone=NY)
    foreign = client.get(
        f"/api/v1/groups/{other_group['id']}/templates/{first.json()['id']}",
        headers=bearer(other_owner),
    )
    assert foreign.status_code == 404
