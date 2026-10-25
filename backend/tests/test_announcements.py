"""Announcements board (roadmap B-106).

Organiser-posted, separate from chat and from event discussion,
pinned + expiring. The acceptance, plainly: a member CANNOT
announce (403) — and neither can a seat, because an announcement
needs an organiser account (the B-043 law, restated for humans).
Scoped posts ride the B-105 visibility rule.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def _post(client, world, title, who=None, **extra):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/announcements",
        json={"title": title, "body": f"Body of {title}", **extra},
        headers=bearer(who or world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_member_cannot_announce(client, world):
    """THE B-106 acceptance test."""
    gid = world["group"]["id"]
    refused = client.post(
        f"/api/v1/groups/{gid}/announcements",
        json={"title": "I declare a holiday", "body": "Trust me."},
        headers=bearer(world["member"]),
    )
    assert refused.status_code == 403
    # And nothing reached the board.
    board = client.get(
        f"/api/v1/groups/{gid}/announcements", headers=bearer(world["member"])
    )
    assert board.json() == []


def test_seat_cannot_announce(client, world):
    """An announcement needs an organiser ACCOUNT: even a seat
    holding organiser role + publish scope is refused, in the
    service, so no route can forget it."""
    gid = world["group"]["id"]
    seat = client.post(
        f"/api/v1/groups/{gid}/seats",
        json={"name": "Secretary", "role": "organiser",
              "scopes": ["read", "draft", "publish"]},
        headers=bearer(world["owner"]),
    )
    assert seat.status_code == 201, seat.text
    refused = client.post(
        f"/api/v1/groups/{gid}/announcements",
        json={"title": "Seat speaks", "body": "Beep."},
        headers={"X-Api-Key": seat.json()["key"]},
    )
    assert refused.status_code == 403
    assert "organiser account" in refused.json()["detail"]


def test_board_pinning_expiry_and_editing(client, world):
    gid = world["group"]["id"]
    first = _post(client, world, "AGM next month")
    second = _post(client, world, "Hall keys moved", pinned=True)
    stale = _post(
        client, world, "Yesterday's bake sale",
        expires_at="2020-01-01T00:00:00Z",
    )
    assert stale["expired"] is True

    board = client.get(
        f"/api/v1/groups/{gid}/announcements", headers=bearer(world["member"])
    )
    # Pinned leads; the expired post has left the board.
    assert [row["title"] for row in board.json()] == [
        "Hall keys moved", "AGM next month"
    ]

    # An organiser can still read the expired post when asking.
    with_expired = client.get(
        f"/api/v1/groups/{gid}/announcements",
        params={"include_expired": True}, headers=bearer(world["owner"]),
    )
    assert {row["title"] for row in with_expired.json()} == {
        "Hall keys moved", "AGM next month", "Yesterday's bake sale"
    }
    # A member asking changes nothing.
    member_ask = client.get(
        f"/api/v1/groups/{gid}/announcements",
        params={"include_expired": True}, headers=bearer(world["member"]),
    )
    assert {row["title"] for row in member_ask.json()} == {
        "Hall keys moved", "AGM next month"
    }

    # Editing is the organiser's act too.
    member_patch = client.patch(
        f"/api/v1/groups/{gid}/announcements/{first['id']}",
        json={"pinned": True}, headers=bearer(world["member"]),
    )
    assert member_patch.status_code == 403
    edited = client.patch(
        f"/api/v1/groups/{gid}/announcements/{first['id']}",
        json={"title": "AGM date confirmed"}, headers=bearer(world["owner"]),
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "AGM date confirmed"

    taken_down = client.delete(
        f"/api/v1/groups/{gid}/announcements/{second['id']}",
        headers=bearer(world["owner"]),
    )
    assert taken_down.status_code == 204
    board = client.get(
        f"/api/v1/groups/{gid}/announcements", headers=bearer(world["owner"])
    )
    assert [row["title"] for row in board.json()] == ["AGM date confirmed"]


def test_scoped_announcement_follows_b105(client, world):
    gid = world["group"]["id"]
    choir = client.post(
        f"/api/v1/groups/{gid}/subgroups",
        json={"name": "Choir"}, headers=bearer(world["owner"]),
    ).json()
    _post(client, world, "Choir: new robes", subgroup_id=choir["id"])
    _post(client, world, "Everyone: rent is due")

    # The member is not in the choir — its post does not exist.
    board = client.get(
        f"/api/v1/groups/{gid}/announcements", headers=bearer(world["member"])
    )
    assert [row["title"] for row in board.json()] == ["Everyone: rent is due"]

    enrolled = client.post(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}/members",
        json={"user_id": world["member"]["user_id"]},
        headers=bearer(world["owner"]),
    )
    assert enrolled.status_code == 201
    board = client.get(
        f"/api/v1/groups/{gid}/announcements", headers=bearer(world["member"])
    )
    assert {row["title"] for row in board.json()} == {
        "Choir: new robes", "Everyone: rent is due"
    }
