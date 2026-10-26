"""Phase gate material (B-014/B-017/B-018/B-022): the role matrix,
agent-seat rules, API-key scope rules, and the audit trail.

The matrix below is the contract of Phase 1: every role × every
action, allowed or denied, asserted end to end.
"""

import pytest

from conftest import bearer, make_group, register
from test_groups import invite_and_accept

ROLES = ["guest", "member", "organiser", "admin", "owner"]

# action -> roles allowed (the ladder at work). All principals here
# are people with session tokens, so scopes never interfere.
ACTIONS: dict[str, set[str]] = {
    "read_group": {"guest", "member", "organiser", "admin", "owner"},
    "list_members": {"member", "organiser", "admin", "owner"},
    "invite": {"organiser", "admin", "owner"},
    "update_group": {"admin", "owner"},
    "create_seat": {"admin", "owner"},
    "read_audit": {"admin", "owner"},
    "create_candidate": {"member", "organiser", "admin", "owner"},
    "approve_candidate": {"organiser", "admin", "owner"},
}


@pytest.fixture()
def world(client):
    """One group with one active person per role, + one candidate."""
    owner = register(client)
    group = make_group(client, owner)
    people = {"owner": owner}
    for role in ["admin", "organiser", "member", "guest"]:
        people[role] = invite_and_accept(
            client, owner, group["id"], f"{role}@example.org", role
        )
    candidate = client.post(
        f"/api/v1/groups/{group['id']}/candidates",
        json={"kind": "event", "title": "Harvest Supper"},
        headers=bearer(owner),
    ).json()
    return {"group": group, "people": people, "candidate": candidate}


def perform(client, world, action: str, session: dict):
    """Run one matrix action as one person; return the response."""
    gid = world["group"]["id"]
    headers = bearer(session)
    if action == "read_group":
        return client.get(f"/api/v1/groups/{gid}", headers=headers)
    if action == "list_members":
        return client.get(f"/api/v1/groups/{gid}/members", headers=headers)
    if action == "invite":
        return client.post(
            f"/api/v1/groups/{gid}/invites",
            json={"email": f"new-{session['user_id'][:6]}@example.org", "role": "member"},
            headers=headers,
        )
    if action == "update_group":
        return client.patch(
            f"/api/v1/groups/{gid}", json={"description": "Updated"}, headers=headers
        )
    if action == "create_seat":
        return client.post(
            f"/api/v1/groups/{gid}/seats",
            json={"name": "Helper", "role": "member", "scopes": ["read", "draft"]},
            headers=headers,
        )
    if action == "read_audit":
        return client.get(f"/api/v1/groups/{gid}/audit", headers=headers)
    if action == "create_candidate":
        return client.post(
            f"/api/v1/groups/{gid}/candidates",
            json={"kind": "task", "title": "Book the hall"},
            headers=headers,
        )
    if action == "approve_candidate":
        # Each approver needs their own pending candidate. Kind
        # "task" on purpose: since Phase 4 (B-049), approving an
        # event candidate MINTS an event and a title-only draft
        # (no resolved date) is refused in plain words — the
        # matrix is about ROLES, and a task approval exercises
        # the identical permission door without the mint.
        candidate = client.post(
            f"/api/v1/groups/{gid}/candidates",
            json={"kind": "task", "title": "Approve me"},
            headers=bearer(world["people"]["owner"]),
        ).json()
        return client.post(
            f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
            headers=headers,
        )
    raise AssertionError(f"unknown action {action}")


@pytest.mark.parametrize("action", sorted(ACTIONS))
@pytest.mark.parametrize("role", ROLES)
def test_role_matrix(client, world, action, role):
    """B-022's gate: the whole matrix, green."""
    response = perform(client, world, action, world["people"][role])
    if role in ACTIONS[action]:
        assert response.status_code < 300, f"{role} should be able to {action}: {response.text}"
    else:
        assert response.status_code in (403, 404), f"{role} must not {action}: {response.status_code}"


def test_seat_can_draft_cannot_approve(client, world):
    """SUPERSEDED at Phase 19 (B-203) — the B-018 blanket ban this
    test first pinned (an organiser seat with publish refused
    approval outright) is replaced by the one approval law: a
    seat approves under exactly its role's rules. What survives
    of the original promise, asserted here at full strength: a
    MEMBER seat — below the group's approver role — creates
    candidates and can never approve, even with the publish
    scope on its key. The organiser-seat half of the new law
    lives in tests/test_seat_equality.py."""
    gid = world["group"]["id"]
    owner = world["people"]["owner"]
    seat = client.post(
        f"/api/v1/groups/{gid}/seats",
        json={"name": "Secretary", "role": "member", "scopes": ["read", "draft", "publish"]},
        headers=bearer(owner),
    )
    assert seat.status_code == 201, seat.text
    seat_headers = {"X-Api-Key": seat.json()["key"]}

    drafted = client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "event", "title": "Seat's draft"},
        headers=seat_headers,
    )
    assert drafted.status_code == 201
    assert drafted.json()["created_by"].startswith("seat:")

    approval = client.post(
        f"/api/v1/groups/{gid}/candidates/{drafted.json()['id']}/approve",
        headers=seat_headers,
    )
    assert approval.status_code == 403
    assert approval.json()["code"] == "seat_cannot_approve"


def test_seat_activity_audited_as_seat(client, world):
    """A seat's work appears in the audit under the seat's name,
    never its creator's (B-018/B-019)."""
    gid = world["group"]["id"]
    owner = world["people"]["owner"]
    seat = client.post(
        f"/api/v1/groups/{gid}/seats",
        json={"name": "Audited", "role": "member", "scopes": ["read", "draft"]},
        headers=bearer(owner),
    ).json()
    client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "task", "title": "Seat task"},
        headers={"X-Api-Key": seat["key"]},
    )
    audit = client.get(f"/api/v1/groups/{gid}/audit", headers=bearer(owner)).json()
    seat_actions = [row for row in audit if row["actor"] == f"seat:{seat['id']}"]
    assert any(row["action"] == "candidate.created" for row in seat_actions)


def test_read_key_cannot_create_candidate(client, world):
    """B-014's canonical case: a read-scoped key creating a
    candidate gets 403 with the insufficient_scope code."""
    gid = world["group"]["id"]
    member = world["people"]["member"]
    key = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "reader", "scopes": ["read"]},
        headers=bearer(member),
    ).json()["key"]
    read = client.get(f"/api/v1/groups/{gid}", headers={"X-Api-Key": key})
    assert read.status_code == 200
    create = client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "event", "title": "Nope"},
        headers={"X-Api-Key": key},
    )
    assert create.status_code == 403
    assert create.json()["code"] == "insufficient_scope"


def test_audit_survives_entity_deletion(client, world):
    """B-019: audit rows outlive the entity they describe — removing
    a member keeps the record of everything that happened."""
    gid = world["group"]["id"]
    owner = world["people"]["owner"]
    member = world["people"]["member"]
    client.delete(
        f"/api/v1/groups/{gid}/members/{member['user_id']}", headers=bearer(owner)
    )
    audit = client.get(f"/api/v1/groups/{gid}/audit", headers=bearer(owner)).json()
    actions = {row["action"] for row in audit}
    assert "group.created" in actions
    assert "membership.removed" in actions
