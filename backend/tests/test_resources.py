"""Resources + bookings (roadmap B-108) — and B-057's other half.

Items with a keeper, booked per event. A double-booking is a
conflict FLAG, never a block: it shows on the booking that
creates it and in the calendar's conflicts view. The golden
test: the van, two overlapping events, one flag; back-to-back
is a plan, not a clash, and must never flag.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from test_groups import invite_and_accept

NY = "America/New_York"
WINDOW = {"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"}


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def _event(client, world, title, starts, ends):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json={"title": title, "starts_at": starts, "ends_at": ends},
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _resource(client, world, name="The Van", who=None, **extra):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/resources",
        json={"name": name, **extra},
        headers=bearer(who or world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _book(client, world, event_id, resource_id, who=None):
    return client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event_id}/resource-bookings",
        json={"resource_id": resource_id},
        headers=bearer(who or world["owner"]),
    )


def _resource_flags(client, world):
    response = client.get(
        f"/api/v1/groups/{world['group']['id']}/conflicts",
        params=WINDOW, headers=bearer(world["owner"]),
    )
    assert response.status_code == 200, response.text
    return [
        flag for flag in response.json()["flags"]
        if flag["kind"] == "resource_overlap"
    ]


def test_resource_crud_and_keeper_rules(client, world):
    gid = world["group"]["id"]
    van = _resource(
        client, world, keeper_user_id=world["member"]["user_id"]
    )
    # The keeper's words come from their membership.
    assert van["keeper_user_id"] == world["member"]["user_id"]
    assert van["keeper_label"] == "Test Person"

    duplicate = client.post(
        f"/api/v1/groups/{gid}/resources",
        json={"name": "The Van"}, headers=bearer(world["owner"]),
    )
    assert duplicate.status_code == 409

    # A member cannot register items.
    refused = client.post(
        f"/api/v1/groups/{gid}/resources",
        json={"name": "Sneaky Projector"}, headers=bearer(world["member"]),
    )
    assert refused.status_code == 403

    # A keeper who is not a member is refused in plain words.
    stranger = register(client)
    bad_keeper = client.post(
        f"/api/v1/groups/{gid}/resources",
        json={"name": "The Hall", "keeper_user_id": stranger["user_id"]},
        headers=bearer(world["owner"]),
    )
    assert bad_keeper.status_code == 422

    listed = client.get(
        f"/api/v1/groups/{gid}/resources", headers=bearer(world["member"])
    )
    assert [row["name"] for row in listed.json()] == ["The Van"]


def test_double_booking_golden(client, world):
    """THE B-108 acceptance test: golden double-booking."""
    gid = world["group"]["id"]
    van = _resource(client, world)
    morning = _event(
        client, world, "Morning Setup",
        "2026-11-07T09:00:00-05:00", "2026-11-07T12:00:00-05:00",
    )
    overlapping = _event(
        client, world, "Midday Delivery Run",
        "2026-11-07T11:00:00-05:00", "2026-11-07T13:00:00-05:00",
    )
    back_to_back = _event(
        client, world, "Afternoon Teardown",
        "2026-11-07T12:00:00-05:00", "2026-11-07T14:00:00-05:00",
    )

    first = _book(client, world, morning["id"], van["id"])
    assert first.status_code == 201, first.text
    assert first.json()["conflicts"] == []

    # Back-to-back with the morning event: a plan, not a clash.
    third = _book(client, world, back_to_back["id"], van["id"])
    assert third.status_code == 201
    assert third.json()["conflicts"] == []
    assert _resource_flags(client, world) == []

    # The overlapping booking is ALLOWED — and flagged, both on
    # the booking itself and in the calendar's conflicts view.
    second = _book(client, world, overlapping["id"], van["id"])
    assert second.status_code == 201, second.text
    echo = second.json()["conflicts"]
    assert {flag["other_event_title"] for flag in echo} == {
        "Morning Setup", "Afternoon Teardown"
    }
    assert all(flag["resource_name"] == "The Van" for flag in echo)

    flags = _resource_flags(client, world)
    assert flags, "the calendar must carry the resource_overlap flag"
    assert all(flag["resource_name"] == "The Van" for flag in flags)
    assert all(flag["resource_id"] == van["id"] for flag in flags)
    pairs = {
        frozenset((flag["event_id"], flag["other_event_id"])) for flag in flags
    }
    assert frozenset((morning["id"], overlapping["id"])) in pairs
    # Morning ↔ teardown never appears: they do not overlap.
    assert frozenset((morning["id"], back_to_back["id"])) not in pairs

    # Booking the same van twice for one event is a conflict of
    # the ordinary kind.
    twice = _book(client, world, morning["id"], van["id"])
    assert twice.status_code == 409

    # Releasing the overlapping booking clears the flags.
    released = client.delete(
        f"/api/v1/groups/{gid}/resource-bookings/{second.json()['id']}",
        headers=bearer(world["owner"]),
    )
    assert released.status_code == 204
    assert _resource_flags(client, world) == []


def test_booking_a_cancelled_event_is_refused(client, world):
    gid = world["group"]["id"]
    van = _resource(client, world)
    event = _event(
        client, world, "Doomed Picnic",
        "2026-11-07T09:00:00-05:00", "2026-11-07T12:00:00-05:00",
    )
    cancelled = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/cancel",
        json={"reason": "Rain."}, headers=bearer(world["owner"]),
    )
    assert cancelled.status_code == 200, cancelled.text
    refused = _book(client, world, event["id"], van["id"])
    assert refused.status_code == 422
