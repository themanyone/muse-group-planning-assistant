"""Volunteer shifts (roadmap B-107).

Role slots generalised to shifts with start/end + headcount;
claim/release rides the B-088 slot endpoints; the gap report
names what is still missing, in words. The golden test pins the
roadmap's own sentence: "Saturday still needs a driver and a
door person."
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from test_groups import invite_and_accept

NY = "America/New_York"
# Saturday 2026-11-07 in New York.
SAT_START = "2026-11-07T09:00:00-05:00"
SAT_END = "2026-11-07T12:00:00-05:00"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def _event(client, world, title="Hall Working Bee", starts=SAT_START, ends=SAT_END):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json={"title": title, "starts_at": starts, "ends_at": ends},
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _shift(client, world, event_id, role, headcount=1, who=None):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event_id}/shifts",
        json={"role_name": role, "headcount": headcount,
              "starts_at": SAT_START, "ends_at": SAT_END},
        headers=bearer(who or world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_open_shift_creates_headcount_slots_with_window(client, world):
    gid = world["group"]["id"]
    event = _event(client, world)
    shift = _shift(client, world, event["id"], "driver", headcount=2)
    assert shift["role_name"] == "driver"
    assert shift["headcount"] == 2
    assert shift["claimed"] == 0
    assert shift["starts_at"] == "2026-11-07T14:00:00Z"  # 09:00 EST → UTC
    assert len(shift["slots"]) == 2
    assert all(slot["starts_at"] == "2026-11-07T14:00:00Z" for slot in shift["slots"])

    listed = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/shifts",
        headers=bearer(world["member"]),
    )
    assert [row["role_name"] for row in listed.json()] == ["driver"]

    # A backwards window is refused in plain words.
    bad = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/shifts",
        json={"role_name": "driver", "headcount": 1,
              "starts_at": SAT_END, "ends_at": SAT_START},
        headers=bearer(world["owner"]),
    )
    assert bad.status_code == 422


def test_gap_report_golden(client, world):
    """THE B-107 acceptance test: the roadmap's own sentence."""
    gid = world["group"]["id"]
    event = _event(client, world)
    _shift(client, world, event["id"], "driver")
    _shift(client, world, event["id"], "door person")

    report = client.get(
        f"/api/v1/groups/{gid}/shift-gaps",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=bearer(world["owner"]),
    )
    assert report.status_code == 200, report.text
    events = report.json()["events"]
    assert len(events) == 1
    line = events[0]
    assert line["event_title"] == "Hall Working Bee"
    assert line["missing"] == [
        {"role_name": "driver", "count": 1},
        {"role_name": "door person", "count": 1},
    ]
    assert line["summary"] == "Saturday still needs a driver and a door person."


def test_claiming_a_shift_slot_closes_its_gap(client, world):
    gid = world["group"]["id"]
    event = _event(client, world)
    driver = _shift(client, world, event["id"], "driver")
    _shift(client, world, event["id"], "door person")

    slot_id = driver["slots"][0]["id"]
    claimed = client.post(
        f"/api/v1/groups/{gid}/roles/{slot_id}/claim",
        headers=bearer(world["member"]),
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["claimed"] is True
    # The claim carries the shift window — it IS a slot, timed.
    assert claimed.json()["starts_at"] == "2026-11-07T14:00:00Z"

    report = client.get(
        f"/api/v1/groups/{gid}/shift-gaps",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=bearer(world["owner"]),
    )
    line = report.json()["events"][0]
    assert line["missing"] == [{"role_name": "door person", "count": 1}]
    assert line["summary"] == "Saturday still needs a door person."

    # Release reopens the gap (the same B-088 unclaim door).
    released = client.post(
        f"/api/v1/groups/{gid}/roles/{slot_id}/unclaim",
        headers=bearer(world["member"]),
    )
    assert released.status_code == 200
    report = client.get(
        f"/api/v1/groups/{gid}/shift-gaps",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=bearer(world["owner"]),
    )
    assert report.json()["events"][0]["summary"] == (
        "Saturday still needs a driver and a door person."
    )


def test_gap_report_counts_headcount_and_skips_covered_events(client, world):
    gid = world["group"]["id"]
    event = _event(client, world, title="Setup Crew Day")
    _shift(client, world, event["id"], "cook", headcount=3)
    covered = _event(client, world, title="Fully Staffed Social",
                     starts="2026-11-08T18:00:00-05:00",
                     ends="2026-11-08T20:00:00-05:00")
    host = _shift(client, world, covered["id"], "host")
    client.post(
        f"/api/v1/groups/{gid}/roles/{host['slots'][0]['id']}/claim",
        headers=bearer(world["member"]),
    )

    report = client.get(
        f"/api/v1/groups/{gid}/shift-gaps",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=bearer(world["owner"]),
    )
    events = report.json()["events"]
    # Only the event WITH gaps earns a line.
    assert [row["event_title"] for row in events] == ["Setup Crew Day"]
    assert events[0]["missing"] == [{"role_name": "cook", "count": 3}]
    assert events[0]["summary"] == "Saturday still needs 3 cooks."
