"""Availability finder (roadmap B-090).

Members mark can/can't against PROPOSED slots — nothing from
any private calendar is read or stored; the endpoint ranks
slots by attendance. The golden test pins the ranking for a
group of five exactly: most can first, fewest can't breaks
the tie, earliest breaks what is left.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from test_groups import invite_and_accept

NY = "America/New_York"

NAMES = ["Ana", "Ben", "Cleo", "Dan", "Eli"]


@pytest.fixture()
def world(client):
    owner = register(client, name="Ana")
    group = make_group(client, owner, home_timezone=NY)
    members = [owner]
    for name in NAMES[1:]:
        email = unique_email()
        invited = register(client, email=email, name=name)
        response = client.post(
            f"/api/v1/groups/{group['id']}/invites",
            json={"email": email, "role": "member"}, headers=bearer(owner),
        )
        assert response.status_code == 201, response.text
        accepted = client.post(
            f"/api/v1/groups/{group['id']}/invites/accept", headers=bearer(invited)
        )
        assert accepted.status_code == 200, accepted.text
        members.append(invited)
    return {"owner": owner, "members": members, "group": group}


def _slot(client, world, starts, ends, label=""):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/availability/slots",
        json={"starts_at": starts, "ends_at": ends, "label": label},
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _mark(client, world, who, slot_id, can):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/availability/slots/{slot_id}/mark",
        json={"can": can}, headers=bearer(who),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_ranking_golden_group_of_five(client, world):
    """B-090's golden: slot B has all five; slots A and C have
    four — C wins the tie on zero can'ts; A is last."""
    gid = world["group"]["id"]
    slot_a = _slot(client, world, "2026-11-07T10:00:00-05:00", "2026-11-07T12:00:00-05:00", "Saturday morning")
    slot_b = _slot(client, world, "2026-11-07T14:00:00-05:00", "2026-11-07T16:00:00-05:00", "Saturday afternoon")
    slot_c = _slot(client, world, "2026-11-08T10:00:00-05:00", "2026-11-08T12:00:00-05:00", "Sunday morning")
    ana, ben, cleo, dan, eli = world["members"]
    for who in world["members"]:
        _mark(client, world, who, slot_b["id"], True)
    for who in (ana, ben, cleo, dan):
        _mark(client, world, who, slot_a["id"], True)
    _mark(client, world, eli, slot_a["id"], False)
    for who in (ana, ben, cleo, eli):
        _mark(client, world, who, slot_c["id"], True)

    ranked = client.get(
        f"/api/v1/groups/{gid}/availability", headers=bearer(world["owner"])
    )
    assert ranked.status_code == 200, ranked.text
    rows = ranked.json()
    assert [row["id"] for row in rows] == [slot_b["id"], slot_c["id"], slot_a["id"]]
    assert [(row["can_count"], row["cant_count"]) for row in rows] == [
        (5, 0), (4, 0), (4, 1),
    ]
    assert sorted(rows[0]["can_members"]) == ["Ana", "Ben", "Cleo", "Dan", "Eli"]
    assert rows[2]["cant_members"] == ["Eli"]
    # The response holds marks and nothing else — no calendar
    # fields exist in this shape to leak (B-090's boundary).
    assert set(rows[0]) == {
        "id", "group_id", "event_id", "label", "starts_at", "ends_at",
        "can_count", "cant_count", "can_members", "cant_members", "created_at",
    }


def test_remark_updates_the_one_mark(client, world):
    slot = _slot(client, world, "2026-11-07T10:00:00-05:00", "2026-11-07T12:00:00-05:00")
    member = world["members"][1]
    _mark(client, world, member, slot["id"], True)
    after = _mark(client, world, member, slot["id"], False)
    assert after["can_count"] == 0
    assert after["cant_count"] == 1


def test_slot_must_end_after_it_starts(client, world):
    refused = client.post(
        f"/api/v1/groups/{world['group']['id']}/availability/slots",
        json={
            "starts_at": "2026-11-07T12:00:00-05:00",
            "ends_at": "2026-11-07T10:00:00-05:00",
        },
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 422


def test_members_mark_but_cannot_propose(client, world):
    refused = client.post(
        f"/api/v1/groups/{world['group']['id']}/availability/slots",
        json={
            "starts_at": "2026-11-07T10:00:00-05:00",
            "ends_at": "2026-11-07T12:00:00-05:00",
        },
        headers=bearer(world["members"][1]),
    )
    assert refused.status_code == 403
