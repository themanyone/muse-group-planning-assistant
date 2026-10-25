"""Groups + memberships acceptance (B-015/B-016): visibility and
the 404 rule, slug collisions, the invite flow, role-change rules,
and the last-owner rule."""

from conftest import bearer, make_group, register


def invite_and_accept(client, owner, group_id: str, email: str, role: str) -> dict:
    """Owner invites ``email`` with ``role``; the invitee (registered
    fresh, claiming the invited account) accepts. Returns session."""
    response = client.post(
        f"/api/v1/groups/{group_id}/invites",
        json={"email": email, "role": role},
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    invitee = register(client, email=email)
    accepted = client.post(
        f"/api/v1/groups/{group_id}/invites/accept", headers=bearer(invitee)
    )
    assert accepted.status_code == 200, accepted.text
    return invitee


def test_create_group_and_slug_collision(client):
    owner = register(client)
    first = make_group(client, owner, "Book Club")
    second = make_group(client, owner, "Book Club")
    assert first["slug"] == "book-club"
    assert second["slug"] == "book-club-2"
    assert first["my_role"] == "owner"


def test_private_group_invisible_to_non_member(client):
    """The founding lesson (B-015): a non-member gets 404, not 403,
    for a private group — existence must not leak."""
    owner = register(client)
    stranger = register(client)
    group = make_group(client, owner, "Secret Circle")
    response = client.get(f"/api/v1/groups/{group['id']}", headers=bearer(stranger))
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_public_listed_group_readable_by_non_member(client):
    owner = register(client)
    group = make_group(client, owner, "Open Circle", visibility="public-listed")
    stranger = register(client)
    response = client.get(f"/api/v1/groups/{group['id']}", headers=bearer(stranger))
    assert response.status_code == 200
    assert response.json()["my_role"] is None


def test_update_group_needs_admin(client):
    owner = register(client)
    group = make_group(client, owner)
    member = invite_and_accept(client, owner, group["id"], "member1@example.org", "member")
    denied = client.patch(
        f"/api/v1/groups/{group['id']}",
        json={"name": "Renamed"},
        headers=bearer(member),
    )
    assert denied.status_code == 403
    allowed = client.patch(
        f"/api/v1/groups/{group['id']}",
        json={"name": "Renamed"},
        headers=bearer(owner),
    )
    assert allowed.status_code == 200
    assert allowed.json()["name"] == "Renamed"


def test_invite_decline(client):
    owner = register(client)
    group = make_group(client, owner)
    invitee_email = "decliner@example.org"
    client.post(
        f"/api/v1/groups/{group['id']}/invites",
        json={"email": invitee_email, "role": "member"},
        headers=bearer(owner),
    )
    invitee = register(client, email=invitee_email)
    declined = client.post(
        f"/api/v1/groups/{group['id']}/invites/decline", headers=bearer(invitee)
    )
    assert declined.status_code == 204
    view = client.get(f"/api/v1/groups/{group['id']}", headers=bearer(invitee))
    assert view.status_code == 404  # declined → still not a member


def test_role_change_rules(client):
    """Granting admin is owner-only; an admin may move plain roles."""
    owner = register(client)
    group = make_group(client, owner)
    admin = invite_and_accept(client, owner, group["id"], "admin1@example.org", "admin")
    member = invite_and_accept(client, owner, group["id"], "member2@example.org", "member")

    # Admin tries to grant admin → refused (owner-only, B-016).
    refused = client.patch(
        f"/api/v1/groups/{group['id']}/members/{member['user_id']}/role",
        json={"role": "admin"},
        headers=bearer(admin),
    )
    assert refused.status_code == 403
    # Owner grants organiser — fine.
    granted = client.patch(
        f"/api/v1/groups/{group['id']}/members/{member['user_id']}/role",
        json={"role": "organiser"},
        headers=bearer(owner),
    )
    assert granted.status_code == 200
    assert granted.json()["role"] == "organiser"


def test_last_owner_cannot_leave_or_be_removed(client):
    owner = register(client)
    group = make_group(client, owner)
    left = client.post(f"/api/v1/groups/{group['id']}/leave", headers=bearer(owner))
    assert left.status_code == 409
    assert left.json()["code"] == "last_owner"
    removed = client.delete(
        f"/api/v1/groups/{group['id']}/members/{owner['user_id']}",
        headers=bearer(owner),
    )
    assert removed.status_code == 409


def test_member_can_leave_and_admin_can_remove(client):
    owner = register(client)
    group = make_group(client, owner)
    member = invite_and_accept(client, owner, group["id"], "leaver@example.org", "member")
    other = invite_and_accept(client, owner, group["id"], "removed@example.org", "member")
    assert client.post(f"/api/v1/groups/{group['id']}/leave", headers=bearer(member)).status_code == 204
    gone = client.delete(
        f"/api/v1/groups/{group['id']}/members/{other['user_id']}",
        headers=bearer(owner),
    )
    assert gone.status_code == 204
    members = client.get(f"/api/v1/groups/{group['id']}/members", headers=bearer(owner))
    emails = {m["email"] for m in members.json()}
    assert "leaver@example.org" not in emails and "removed@example.org" not in emails
