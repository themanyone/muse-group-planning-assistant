"""The group settings pack (roadmap B-111).

One validated schema for the group's dials — review-queue
rules, reminder defaults, digest day, template set, AI budget
— audited on change. The acceptance is one story told in one
test: a settings change lands, the audit row says what moved,
and the very next approval behaves by the new rules.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from test_groups import invite_and_accept


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner)
    organiser = invite_and_accept(
        client, owner, group["id"], unique_email(), "organiser"
    )
    member = invite_and_accept(
        client, owner, group["id"], unique_email(), "member"
    )
    return {
        "owner": owner, "organiser": organiser,
        "member": member, "group": group,
    }


def _settings(client, world, who=None):
    return client.get(
        f"/api/v1/groups/{world['group']['id']}/settings",
        headers=bearer(who or world["owner"]),
    )


def _put(client, world, changes, who=None):
    return client.put(
        f"/api/v1/groups/{world['group']['id']}/settings",
        json=changes, headers=bearer(who or world["owner"]),
    )


def _candidate(client, world):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/candidates",
        json={"kind": "note", "title": "Borrow the hall keys"},
        headers=bearer(world["member"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client, world, candidate_id, who):
    return client.post(
        f"/api/v1/groups/{world['group']['id']}/candidates/{candidate_id}/approve",
        headers=bearer(who),
    )


def test_defaults_are_the_founding_rules(client, world):
    response = _settings(client, world)
    assert response.status_code == 200, response.text
    pack = response.json()
    assert pack["review_min_role"] == "organiser"
    assert pack["review_approvals_required"] == 1
    assert pack["template_set"] == "starter"
    assert pack["digest_enabled"] is True
    assert pack["ai_daily_budget_micros"] is None
    # The roadmap defaults, in the shared ReminderRule wire
    # shape (morning_of carries days: null — schemas/groups).
    assert pack["reminder_rules"] == [
        {"kind": "days_before", "days": 7},
        {"kind": "days_before", "days": 1},
        {"kind": "morning_of", "days": None},
    ]


def test_settings_change_audits_then_governs_approval(client, world):
    gid = world["group"]["id"]

    # The change: two distinct approvers before anything is decided.
    changed = _put(client, world, {"review_approvals_required": 2})
    assert changed.status_code == 200, changed.text
    assert changed.json()["review_approvals_required"] == 2

    # The audit row names the move, old to new.
    audit = client.get(
        f"/api/v1/groups/{gid}/audit", headers=bearer(world["owner"])
    ).json()
    pack_rows = [row for row in audit if row["action"] == "group.settings_updated"]
    assert pack_rows, audit
    assert pack_rows[0]["detail"]["review_approvals_required"] == {
        "from": 1, "to": 2,
    }

    # The new behaviour, immediately: the owner's approval is
    # RECORDED and the candidate stays pending...
    candidate = _candidate(client, world)
    first = _approve(client, world, candidate["id"], world["owner"])
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending"
    assert first.json()["approvals_recorded"] == 1
    assert first.json()["approvals_required"] == 2

    # ...the same person twice is not two approvers...
    twice = _approve(client, world, candidate["id"], world["owner"])
    assert twice.status_code == 409

    # ...and a second distinct approver decides it.
    second = _approve(client, world, candidate["id"], world["organiser"])
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"
    assert second.json()["approvals_recorded"] == 2

    audit = client.get(
        f"/api/v1/groups/{gid}/audit", headers=bearer(world["owner"])
    ).json()
    assert any(
        row["action"] == "candidate.approval_recorded" for row in audit
    )


def test_review_floor_opens_and_closes(client, world):
    candidate = _candidate(client, world)
    # Founding rule: a member may not approve.
    refused = _approve(client, world, candidate["id"], world["member"])
    assert refused.status_code == 403

    # Open the floor to members and the same act decides.
    opened = _put(client, world, {"review_min_role": "member"})
    assert opened.status_code == 200, opened.text
    allowed = _approve(client, world, candidate["id"], world["member"])
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "approved"

    # Raise the floor to admins and even an organiser is refused.
    _put(client, world, {"review_min_role": "admin"})
    other = _candidate(client, world)
    refused = _approve(client, world, other["id"], world["organiser"])
    assert refused.status_code == 403


def test_pack_validation_and_doors(client, world):
    # Out-of-vocabulary values are refused, whole.
    assert _put(client, world, {"review_approvals_required": 5}).status_code == 422
    assert _put(client, world, {"template_set": "deluxe"}).status_code == 422
    assert _put(client, world, {"digest_time": "25:99"}).status_code == 422
    assert _put(client, world, {"review_min_role": "guest"}).status_code == 422

    # Doors: reading the pack is organiser work, writing it admin.
    assert _settings(client, world, who=world["member"]).status_code == 403
    assert _put(
        client, world, {"digest_enabled": False}, who=world["organiser"]
    ).status_code == 403

    # Nothing above changed anything.
    pack = _settings(client, world).json()
    assert pack["review_approvals_required"] == 1
    assert pack["template_set"] == "starter"
    assert pack["digest_enabled"] is True


def test_partial_pack_and_budget_clearing(client, world):
    # One dial moves; the rest of the pack is untouched.
    moved = _put(client, world, {"ai_daily_budget_micros": 500})
    assert moved.status_code == 200
    assert moved.json()["ai_daily_budget_micros"] == 500
    assert moved.json()["review_min_role"] == "organiser"

    # An explicit null in the pack clears the cap — present
    # governs, absent leaves alone (the pack's stated rule).
    cleared = _put(client, world, {"ai_daily_budget_micros": None})
    assert cleared.status_code == 200
    assert cleared.json()["ai_daily_budget_micros"] is None
