"""Attendance (roadmap B-109) — aggregates only, by design.

Organiser-marked attendance, queryable ONLY as aggregates for
planning. The deliberate product refusal — no per-person
attendance league tables in the API — is itself under test:
the per-person routes do not exist, and no aggregate response
carries a person's identity anywhere.
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
    member2 = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {
        "owner": owner, "member": member, "member2": member2, "group": group,
    }


def _event(client, world, title, starts, ends, **extra):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json={"title": title, "starts_at": starts, "ends_at": ends, **extra},
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _mark(client, world, event_id, user_id, who=None):
    return client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event_id}/attendance",
        json={"user_id": user_id},
        headers=bearer(who or world["owner"]),
    )


def test_marking_and_headcount(client, world):
    gid = world["group"]["id"]
    night = _event(
        client, world, "Club Night",
        "2026-10-10T19:00:00-04:00", "2026-10-10T22:00:00-04:00",
    )
    marked = _mark(client, world, night["id"], world["member"]["user_id"])
    assert marked.status_code == 201, marked.text
    assert marked.json()["user_id"] == world["member"]["user_id"]
    _mark(client, world, night["id"], world["member2"]["user_id"])

    # Re-marking is the same mark, not a second person.
    again = _mark(client, world, night["id"], world["member"]["user_id"])
    assert again.status_code == 201

    count = client.get(
        f"/api/v1/groups/{gid}/events/{night['id']}/attendance",
        headers=bearer(world["member"]),
    )
    assert count.status_code == 200
    assert count.json() == {"event_id": night["id"], "attendee_count": 2}

    # A member cannot mark; a stranger cannot be marked.
    refused = _mark(
        client, world, night["id"], world["member"]["user_id"],
        who=world["member"],
    )
    assert refused.status_code == 403
    stranger = register(client)
    bad = _mark(client, world, night["id"], stranger["user_id"])
    assert bad.status_code == 422

    # Unmarking corrects the count.
    removed = client.delete(
        f"/api/v1/groups/{gid}/events/{night['id']}/attendance/{world['member2']['user_id']}",
        headers=bearer(world["owner"]),
    )
    assert removed.status_code == 204
    count = client.get(
        f"/api/v1/groups/{gid}/events/{night['id']}/attendance",
        headers=bearer(world["owner"]),
    )
    assert count.json()["attendee_count"] == 1


def test_summary_is_counts_only(client, world):
    gid = world["group"]["id"]
    first = _event(
        client, world, "Club Night One",
        "2026-10-03T19:00:00-04:00", "2026-10-03T22:00:00-04:00",
    )
    second = _event(
        client, world, "Club Night Two",
        "2026-10-10T19:00:00-04:00", "2026-10-10T22:00:00-04:00",
    )
    future = _event(
        client, world, "Club Night Three",
        "2027-01-09T19:00:00-05:00", "2027-01-09T22:00:00-05:00",
    )
    _mark(client, world, first["id"], world["member"]["user_id"])
    _mark(client, world, second["id"], world["member"]["user_id"])
    _mark(client, world, second["id"], world["member2"]["user_id"])
    _mark(client, world, future["id"], world["member"]["user_id"])

    response = client.get(
        f"/api/v1/groups/{gid}/attendance/summary",
        params={"recent": 3}, headers=bearer(world["member"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # The future night has not drawn anyone yet — it is not in
    # the "last nights" the group plans from.
    assert body["events_considered"] == 2
    assert [row["title"] for row in body["events"]] == [
        "Club Night Two", "Club Night One"
    ]
    assert [row["attendee_count"] for row in body["events"]] == [2, 1]
    assert body["average_attendance"] == 1.5

    # Counts only: neither member's identity appears ANYWHERE in
    # the payload — not as an id, not as an email.
    raw = response.text
    for person in (world["member"], world["member2"]):
        assert person["user_id"] not in raw
        assert person["email"] not in raw


def test_the_refusal_no_per_person_reads(client, world):
    """THE B-109 acceptance test: the refusal itself.

    There is no endpoint that lists who attended — not per
    event, not per member, not as a league table. Asking for
    one is a 404, the same answer as any route that was never
    built, because it never will be."""
    gid = world["group"]["id"]
    night = _event(
        client, world, "Club Night",
        "2026-10-10T19:00:00-04:00", "2026-10-10T22:00:00-04:00",
    )
    _mark(client, world, night["id"], world["member"]["user_id"])

    for path in (
        f"/api/v1/groups/{gid}/attendance/members",
        f"/api/v1/groups/{gid}/attendance/leaderboard",
        f"/api/v1/groups/{gid}/members/{world['member']['user_id']}/attendance",
    ):
        response = client.get(path, headers=bearer(world["owner"]))
        assert response.status_code == 404, f"{path} must not exist"

    # The "attendees" spelling lands on the UNMARK route's shape
    # (…/attendance/{user_id}, a write) — so the answer is 405,
    # not a list: no read exists there either way.
    response = client.get(
        f"/api/v1/groups/{gid}/events/{night['id']}/attendance/attendees",
        headers=bearer(world["owner"]),
    )
    assert response.status_code in (404, 405)
    assert world["member"]["user_id"] not in response.text

    # And the one event read that DOES exist is a bare count —
    # no people-shaped keys anywhere in it.
    count = client.get(
        f"/api/v1/groups/{gid}/events/{night['id']}/attendance",
        headers=bearer(world["owner"]),
    )
    assert set(count.json()) == {"event_id", "attendee_count"}
    assert world["member"]["user_id"] not in count.text
