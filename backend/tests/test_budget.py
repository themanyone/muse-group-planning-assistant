"""Budget-lite (roadmap B-089).

Per-event cost lines and a per-member balance summary —
integer cents end to end, NEVER floats: the request schema
takes an int, the column is an Integer, the summary adds
ints. The golden test pins the arithmetic exactly; the float
test pins the refusal. Deliberately no ledger, no payments.
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
    event = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=bearer(owner),
    )
    assert event.status_code == 201, event.text
    return {"owner": owner, "member": member, "group": group, "event": event.json()}


def _cost(client, world, who, **body):
    return client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/costs",
        json=body, headers=bearer(who),
    )


def test_budget_arithmetic_golden(client, world):
    """B-089's golden: 1250 + 375 + 99 = 1724, exactly, in
    integer cents, with balances per payer sorted by paid."""
    gid = world["group"]["id"]
    first = _cost(client, world, world["owner"], label="Hall hire", amount_cents=1250)
    assert first.status_code == 201, first.text
    # The payer defaults to the caller.
    assert first.json()["paid_by_id"] == world["owner"]["user_id"]
    second = _cost(
        client, world, world["owner"], label="Leeks", amount_cents=375,
        paid_by_id=world["member"]["user_id"],
    )
    assert second.status_code == 201, second.text
    third = _cost(
        client, world, world["owner"], label="Candles", amount_cents=99,
        paid_by_label="Cash Pete",
    )
    assert third.status_code == 201, third.text
    assert third.json()["paid_by_id"] is None

    budget = client.get(
        f"/api/v1/groups/{gid}/events/{world['event']['id']}/budget",
        headers=bearer(world["owner"]),
    )
    assert budget.status_code == 200, budget.text
    body = budget.json()
    assert body["total_cents"] == 1724
    assert type(body["total_cents"]) is int
    assert [row["label"] for row in body["lines"]] == ["Hall hire", "Leeks", "Candles"]
    balances = {
        (row["member_id"], row["member_label"]): row["paid_cents"]
        for row in body["balances"]
    }
    assert balances[(world["owner"]["user_id"], body["balances"][0]["member_label"])] == 1250
    assert [row["paid_cents"] for row in body["balances"]] == [1250, 375, 99]
    member_row = next(
        row for row in body["balances"] if row["member_id"] == world["member"]["user_id"]
    )
    assert member_row["paid_cents"] == 375
    guest_row = next(row for row in body["balances"] if row["member_id"] is None)
    assert guest_row["member_label"] == "Cash Pete"
    assert guest_row["paid_cents"] == 99


def test_float_amount_is_refused_at_the_door(client, world):
    """B-089's law at the schema: 12.5 is not an amount."""
    refused = _cost(client, world, world["owner"], label="Half cents", amount_cents=12.5)
    assert refused.status_code == 422
    listed = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/costs",
        headers=bearer(world["owner"]),
    )
    assert listed.json() == []


def test_payer_must_be_a_member(client, world):
    refused = _cost(
        client, world, world["owner"], label="Ghost paid", amount_cents=100,
        paid_by_id="not-a-member",
    )
    assert refused.status_code == 422
