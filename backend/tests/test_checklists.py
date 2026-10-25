"""Checklists + roles (roadmap B-088).

Bring/prepare lines with quantities, claimable by one person;
role slots (host, driver, cook…) claimed or visibly unclaimed;
and the merged supplies view — the week's events folded into
one "buy once" shopping truth, pinned by a golden test.
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


def _event(client, world, title, starts, ends, **extra):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json={"title": title, "starts_at": starts, "ends_at": ends, **extra},
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _add_item(client, world, event_id, label, quantity=1, section="bring"):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event_id}/checklist",
        json={"label": label, "quantity": quantity, "section": section},
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_items_claims_and_done(client, world):
    gid = world["group"]["id"]
    event = _event(client, world, "Supper", "2026-11-06T18:30:00-05:00", "2026-11-06T21:00:00-05:00")
    item = _add_item(client, world, event["id"], "Candles", quantity=2)
    assert item["claimed"] is False
    assert item["quantity"] == 2

    claimed = client.post(
        f"/api/v1/groups/{gid}/checklist/{item['id']}/claim",
        headers=bearer(world["member"]),
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["claimed"] is True
    assert claimed.json()["claimed_by_id"] == world["member"]["user_id"]

    # A second person's claim on the same line is refused.
    refused = client.post(
        f"/api/v1/groups/{gid}/checklist/{item['id']}/claim",
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 409

    # The claimer can release it; then anyone may take it.
    released = client.post(
        f"/api/v1/groups/{gid}/checklist/{item['id']}/unclaim",
        headers=bearer(world["member"]),
    )
    assert released.json()["claimed"] is False

    done = client.patch(
        f"/api/v1/groups/{gid}/checklist/{item['id']}",
        json={"done": True}, headers=bearer(world["owner"]),
    )
    assert done.json()["done"] is True

    listed = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/checklist",
        headers=bearer(world["owner"]),
    ).json()
    assert [row["label"] for row in listed] == ["Candles"]

    bad = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/checklist",
        json={"label": "X", "section": "sideways"},
        headers=bearer(world["owner"]),
    )
    assert bad.status_code == 422


def test_role_slots_claim_and_release(client, world):
    gid = world["group"]["id"]
    event = _event(client, world, "Trip", "2026-11-07T09:00:00-05:00", "2026-11-07T18:00:00-05:00")
    opened = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/roles",
        json={"role_name": "driver", "slots": 2}, headers=bearer(world["owner"]),
    )
    assert opened.status_code == 201, opened.text
    slots = opened.json()
    assert len(slots) == 2
    assert all(row["claimed"] is False for row in slots)

    claimed = client.post(
        f"/api/v1/groups/{gid}/roles/{slots[0]['id']}/claim",
        headers=bearer(world["member"]),
    )
    assert claimed.json()["claimed"] is True
    refused = client.post(
        f"/api/v1/groups/{gid}/roles/{slots[0]['id']}/claim",
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 409
    listed = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/roles",
        headers=bearer(world["owner"]),
    ).json()
    assert [row["claimed"] for row in listed] == [True, False]


def test_merged_supplies_golden(client, world):
    """B-088's golden: two suppers in one window both need
    cups (spelled differently) — one folded line, total 36,
    both events named. A cancelled event's cups never join,
    and a same-named line outside the window stays out."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    first = _event(client, world, "Friday supper", "2026-11-06T18:30:00-05:00", "2026-11-06T21:00:00-05:00")
    second = _event(client, world, "Saturday supper", "2026-11-07T18:30:00-05:00", "2026-11-07T21:00:00-05:00")
    cancelled = _event(client, world, "Off supper", "2026-11-08T18:30:00-05:00", "2026-11-08T21:00:00-05:00")
    later = _event(client, world, "December supper", "2026-12-04T18:30:00-05:00", "2026-12-04T21:00:00-05:00")

    _add_item(client, world, first["id"], "Cups", quantity=24)
    _add_item(client, world, first["id"], "Plates", quantity=10)
    _add_item(client, world, second["id"], " cups ", quantity=12)
    _add_item(client, world, second["id"], "Napkins", quantity=50)
    _add_item(client, world, cancelled["id"], "Cups", quantity=100)
    _add_item(client, world, later["id"], "Cups", quantity=7)

    cancel = client.post(
        f"/api/v1/groups/{gid}/events/{cancelled['id']}/cancel",
        json={"reason": "called off"}, headers=headers,
    )
    assert cancel.status_code == 200, cancel.text

    supplies = client.get(
        f"/api/v1/groups/{gid}/supplies"
        "?start=2026-11-02T00:00:00Z&end=2026-11-09T00:00:00Z",
        headers=headers,
    )
    assert supplies.status_code == 200, supplies.text
    rows = supplies.json()
    assert [row["label"] for row in rows] == ["Cups", "Napkins", "Plates"]
    cups = rows[0]
    assert cups["total_quantity"] == 36
    assert {
        (entry["event_title"], entry["quantity"]) for entry in cups["events"]
    } == {("Friday supper", 24), ("Saturday supper", 12)}
    assert rows[1]["total_quantity"] == 50
    assert rows[2]["total_quantity"] == 10
