"""Seat equality + the one approval law (roadmap B-202/B-203, Phase 19).

B-202's acceptance, in code: the role-matrix suite's participation
actions run against BOTH kinds of citizen — a person with a session
and an agent seat with a key — under one set of expectations. A seat
of a role sees and does what a person of that role sees and does:
profile, group reads, discussion, RSVPs, task ownership, poll votes,
calendar feeds — attributed in audit and projections to the seat
itself, never silently to the person who created it.

B-203's acceptance, in code: the approval law is ONE law. A seat
below the group's approver role is refused (``seat_cannot_approve``,
the seat form of the refusal a person below the role meets); a
draft-scoped seat stops at the door's scope check like anyone's
draft key; an organiser seat holding publish approves through the
same service operation a human organiser's click calls — including
the multi-approver count, where the seat's recorded approval is a
distinct principal's row. No agent-only override exists: every
refusal above is a rule a person is also bound by.

Standing boundaries kept honestly: creating seats and inviting
people stay person acts (a seat creating seats is refused by the
docs-set law, Doc 02 A.5; inviting creates a person's account) —
asserted below as refusals, not parity failures.
"""

import pytest

from conftest import bearer, make_group, register
from test_groups import invite_and_accept

ROLES = ["guest", "member", "organiser", "admin", "owner"]
ALL_SCOPES = ["read", "draft", "publish", "admin"]

# The participation half of the B-022 matrix (tests/test_permissions
# carries the full human matrix, including the person-only doors):
# action -> roles allowed. Identical for both kinds of citizen.
ACTIONS: dict[str, set[str]] = {
    "read_group": {"guest", "member", "organiser", "admin", "owner"},
    "list_members": {"member", "organiser", "admin", "owner"},
    "create_candidate": {"member", "organiser", "admin", "owner"},
    "approve_candidate": {"organiser", "admin", "owner"},
}


@pytest.fixture()
def world(client):
    """One group, one person per role, one seat per role (all
    scopes, so — exactly like a person's session — only the ROLE
    speaks), and one published event for the participation tests."""
    owner = register(client)
    group = make_group(client, owner)
    people = {"owner": owner}
    seats = {}
    for role in ["admin", "organiser", "member", "guest"]:
        people[role] = invite_and_accept(
            client, owner, group["id"], f"eq-{role}@example.org", role
        )
    for role in ROLES:
        made = client.post(
            f"/api/v1/groups/{group['id']}/seats",
            json={"name": f"Seat {role}", "role": role, "scopes": ALL_SCOPES},
            headers=bearer(owner),
        )
        assert made.status_code == 201, made.text
        seats[role] = made.json()
    event = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Equality Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=bearer(owner),
    )
    assert event.status_code == 201, event.text
    return {"group": group, "people": people, "seats": seats, "event": event.json()}


def seat_headers(seat: dict) -> dict:
    return {"X-Api-Key": seat["key"]}


def perform(client, world, action: str, headers: dict):
    """One matrix action under the given headers (either kind)."""
    gid = world["group"]["id"]
    if action == "read_group":
        return client.get(f"/api/v1/groups/{gid}", headers=headers)
    if action == "list_members":
        return client.get(f"/api/v1/groups/{gid}/members", headers=headers)
    if action == "create_candidate":
        return client.post(
            f"/api/v1/groups/{gid}/candidates",
            json={"kind": "task", "title": "Matrix draft"},
            headers=headers,
        )
    if action == "approve_candidate":
        # A fresh pending task candidate per attempt (the matrix
        # is about roles; a task approval exercises the identical
        # permission door without the event mint — B-022's note).
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
def test_role_matrix_runs_for_people(client, world, action, role):
    response = perform(client, world, action, bearer(world["people"][role]))
    if role in ACTIONS[action]:
        assert response.status_code < 300, f"person {role} should {action}: {response.text}"
    else:
        assert response.status_code in (403, 404), f"person {role} must not {action}"


@pytest.mark.parametrize("action", sorted(ACTIONS))
@pytest.mark.parametrize("role", ROLES)
def test_role_matrix_runs_unchanged_for_seats(client, world, action, role):
    """B-202's acceptance: the SAME expectations, a seat principal."""
    response = perform(client, world, action, seat_headers(world["seats"][role]))
    if role in ACTIONS[action]:
        assert response.status_code < 300, f"seat {role} should {action}: {response.text}"
    else:
        assert response.status_code in (403, 404), f"seat {role} must not {action}: {response.status_code}"


def test_organiser_seat_approval_is_attributed_to_the_seat(client, world):
    """An organiser seat's approval lands under the seat's own
    label — in the candidate's approved_by and in the audit —
    never under its creator's name (B-202's attribution law)."""
    gid = world["group"]["id"]
    seat = world["seats"]["organiser"]
    candidate = client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "task", "title": "Seat-approved task"},
        headers=bearer(world["people"]["owner"]),
    ).json()
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=seat_headers(seat),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == f"seat:{seat['id']}"
    audit = client.get(
        f"/api/v1/groups/{gid}/audit", headers=bearer(world["people"]["owner"])
    ).json()
    assert any(
        row["action"] == "candidate.approved" and row["actor"] == f"seat:{seat['id']}"
        for row in audit
    )


def test_draft_scoped_organiser_seat_stops_at_the_scope_door(client, world):
    """B-203: scope binds seats exactly as it binds people — an
    organiser seat whose key lacks publish meets the same
    insufficient_scope refusal as a person's draft key."""
    gid = world["group"]["id"]
    owner = world["people"]["owner"]
    seat = client.post(
        f"/api/v1/groups/{gid}/seats",
        json={"name": "Drafty", "role": "organiser", "scopes": ["read", "draft"]},
        headers=bearer(owner),
    ).json()
    candidate = client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "task", "title": "No deciding"},
        headers=bearer(owner),
    ).json()
    refused = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=seat_headers(seat),
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "insufficient_scope"


def test_seat_counts_as_a_distinct_approver(client, world):
    """B-203 + B-111: with two approvals required, an organiser
    seat's approval is RECORDED as a distinct principal's act
    (candidate stays pending), a repeat by the same seat is a
    conflict, and the owner's approval then decides it."""
    gid = world["group"]["id"]
    owner = world["people"]["owner"]
    changed = client.put(
        f"/api/v1/groups/{gid}/settings",
        json={"review_approvals_required": 2},
        headers=bearer(owner),
    )
    assert changed.status_code == 200, changed.text
    seat = world["seats"]["organiser"]
    candidate = client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "task", "title": "Two pairs of eyes"},
        headers=bearer(owner),
    ).json()
    first = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=seat_headers(seat),
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending"
    assert first.json()["approvals_recorded"] == 1
    repeat = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=seat_headers(seat),
    )
    assert repeat.status_code == 409
    second = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=bearer(owner),
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"


def test_seat_profile_lifecycle(client, world):
    """B-202: a seat has a profile — set at creation, readable by
    members, editable by the seat itself and by an admin person,
    by nobody else; role is not profile and never moves here."""
    gid = world["group"]["id"]
    owner = world["people"]["owner"]
    made = client.post(
        f"/api/v1/groups/{gid}/seats",
        json={
            "name": "Greeter", "role": "member", "scopes": ["read", "draft"],
            "description": "Welcomes newcomers and drafts the plan.",
            "avatar_url": "https://example.org/greeter.png",
        },
        headers=bearer(owner),
    )
    assert made.status_code == 201, made.text
    seat = made.json()
    assert seat["description"] == "Welcomes newcomers and drafts the plan."
    assert seat["avatar_url"] == "https://example.org/greeter.png"

    read = client.get(
        f"/api/v1/groups/{gid}/seats/{seat['id']}",
        headers=bearer(world["people"]["member"]),
    )
    assert read.status_code == 200, read.text
    assert read.json()["name"] == "Greeter"

    edited = client.patch(
        f"/api/v1/groups/{gid}/seats/{seat['id']}",
        json={"description": "Now also keeps the hall list.", "role": "owner"},
        headers=seat_headers(seat),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "Now also keeps the hall list."
    assert edited.json()["role"] == "member"  # role is not profile

    other_seat = world["seats"]["member"]
    refused = client.patch(
        f"/api/v1/groups/{gid}/seats/{seat['id']}",
        json={"description": "Hijacked."},
        headers=seat_headers(other_seat),
    )
    assert refused.status_code == 403
    refused_person = client.patch(
        f"/api/v1/groups/{gid}/seats/{seat['id']}",
        json={"description": "Hijacked."},
        headers=bearer(world["people"]["member"]),
    )
    assert refused_person.status_code == 403
    by_admin = client.patch(
        f"/api/v1/groups/{gid}/seats/{seat['id']}",
        json={"avatar_url": "https://example.org/greeter-2.png"},
        headers=bearer(world["people"]["admin"]),
    )
    assert by_admin.status_code == 200, by_admin.text

    audit = client.get(f"/api/v1/groups/{gid}/audit", headers=bearer(owner)).json()
    seat_edits = [
        row for row in audit
        if row["action"] == "seat.profile_updated" and row["actor"] == f"seat:{seat['id']}"
    ]
    assert seat_edits, "the seat's own profile edit is audited as the seat"


def test_seat_participates_rsvp_poll_discussion_task_feed(client, world):
    """B-202's list, end to end: a member seat RSVPs, votes,
    posts to the discussion, owns a task, and an organiser seat
    holds a calendar feed — each in its own name."""
    gid = world["group"]["id"]
    owner = world["people"]["owner"]
    seat = world["seats"]["member"]
    headers = seat_headers(seat)
    event_id = world["event"]["id"]

    rsvp = client.put(
        f"/api/v1/groups/{gid}/events/{event_id}/rsvp",
        json={"status": "coming", "guests": 0},
        headers=headers,
    )
    assert rsvp.status_code == 200, rsvp.text
    answers = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}/rsvps", headers=bearer(owner)
    ).json()
    mine = [
        row for row in answers["rsvps"]
        if row["principal_label"] == f"seat:{seat['id']}"
    ]
    assert len(mine) == 1 and mine[0]["status"] == "coming"

    poll = client.post(
        f"/api/v1/groups/{gid}/polls",
        json={"question": "Pizza or tacos?", "options": [{"label": "Pizza"}, {"label": "Tacos"}]},
        headers=bearer(owner),
    ).json()
    vote = client.post(
        f"/api/v1/groups/{gid}/polls/{poll['id']}/vote",
        json={"option_id": poll["options"][1]["id"]},
        headers=headers,
    )
    assert vote.status_code == 200, vote.text

    comment = client.post(
        f"/api/v1/groups/{gid}/events/{event_id}/comments",
        json={"body": "I will bring the playlist."},
        headers=headers,
    )
    assert comment.status_code == 201, comment.text
    assert comment.json()["author_label"] == f"seat:{seat['id']}"

    task = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Book the playlist", "owner_id": seat["id"]},
        headers=bearer(owner),
    )
    assert task.status_code == 201, task.text
    assert task.json()["owner_id"] == seat["id"]
    assert task.json()["owner_label"] == seat["name"]

    feed = client.post(
        f"/api/v1/groups/{gid}/calendar/feeds",
        json={},
        headers=seat_headers(world["seats"]["organiser"]),
    )
    assert feed.status_code == 201, feed.text
    assert feed.json()["token"]


def test_seat_cannot_create_seats_or_invite_people(client, world):
    """The standing person-only doors (docs-set law): an admin
    seat administering ITSELF into more seats, or creating a
    person's account by invite, is refused — these are account
    acts, not participation, and B-202 does not move them."""
    gid = world["group"]["id"]
    admin_seat = seat_headers(world["seats"]["admin"])
    made = client.post(
        f"/api/v1/groups/{gid}/seats",
        json={"name": "Spawn", "role": "member", "scopes": ["read"]},
        headers=admin_seat,
    )
    assert made.status_code == 403, made.text
    invited = client.post(
        f"/api/v1/groups/{gid}/invites",
        json={"email": "new@example.org", "role": "member"},
        headers=seat_headers(world["seats"]["organiser"]),
    )
    assert invited.status_code == 403, invited.text
