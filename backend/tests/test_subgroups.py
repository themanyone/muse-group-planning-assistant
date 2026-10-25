"""Sub-groups + chapters (roadmap B-105) — and the leak tests.

Named sub-groups inside a group; membership inherited from the
group; events and tasks scoped to a sub-group are visible ONLY
to its enrolled members and to group organisers. The leak tests
are the acceptance: a member of sub-group A must not see
sub-group B's scoped items — not in lists, not by id, not in
the calendar, not through search.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member_a = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    member_b = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    member_c = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {
        "owner": owner, "group": group,
        "a": member_a, "b": member_b, "c": member_c,
    }


def _subgroup(client, world, name, who=None):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/subgroups",
        json={"name": name},
        headers=bearer(who or world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _enroll(client, world, subgroup_id, user_id, who=None, role=None):
    body = {"user_id": user_id}
    if role is not None:
        body["role"] = role
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/subgroups/{subgroup_id}/members",
        json=body,
        headers=bearer(who or world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _event(client, world, title, starts, ends, **extra):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json={"title": title, "starts_at": starts, "ends_at": ends, **extra},
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_subgroup_crud_and_unique_names(client, world):
    gid = world["group"]["id"]
    choir = _subgroup(client, world, "Choir")
    assert choir["member_count"] == 0
    assert choir["name"] == "Choir"

    duplicate = client.post(
        f"/api/v1/groups/{gid}/subgroups",
        json={"name": "Choir"}, headers=bearer(world["owner"]),
    )
    assert duplicate.status_code == 409

    # A plain member cannot open a sub-group.
    refused = client.post(
        f"/api/v1/groups/{gid}/subgroups",
        json={"name": "Sneaky"}, headers=bearer(world["a"]),
    )
    assert refused.status_code == 403

    renamed = client.patch(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}",
        json={"name": "Chamber Choir"}, headers=bearer(world["owner"]),
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Chamber Choir"

    listed = client.get(f"/api/v1/groups/{gid}/subgroups", headers=bearer(world["a"]))
    assert [row["name"] for row in listed.json()] == ["Chamber Choir"]

    deleted = client.delete(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}",
        headers=bearer(world["owner"]),
    )
    assert deleted.status_code == 204


def test_enrolment_rules(client, world):
    gid = world["group"]["id"]
    choir = _subgroup(client, world, "Choir")

    # The organiser enrols A and grants a per-sub-group role.
    row = _enroll(client, world, choir["id"], world["a"]["user_id"], role="organiser")
    assert row["group_role"] == "member"
    assert row["subgroup_role"] == "organiser"
    assert row["effective_role"] == "organiser"

    # Twice is a conflict, not a second row.
    again = client.post(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}/members",
        json={"user_id": world["a"]["user_id"]}, headers=bearer(world["owner"]),
    )
    assert again.status_code == 409

    # A stranger to the group cannot be enrolled at all
    # (membership is inherited — there is nothing to inherit).
    stranger = register(client)
    refused = client.post(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}/members",
        json={"user_id": stranger["user_id"]}, headers=bearer(world["owner"]),
    )
    assert refused.status_code == 422

    # A member may enrol THEMSELVES — but may not enrol someone
    # else, and may not grant themselves a role.
    own = client.post(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}/members",
        json={"user_id": world["b"]["user_id"]}, headers=bearer(world["b"]),
    )
    assert own.status_code == 201
    other = client.post(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}/members",
        json={"user_id": world["c"]["user_id"]}, headers=bearer(world["b"]),
    )
    assert other.status_code == 403
    kitchen = _subgroup(client, world, "Kitchen Crew")
    grant = client.post(
        f"/api/v1/groups/{gid}/subgroups/{kitchen['id']}/members",
        json={"user_id": world["c"]["user_id"], "role": "organiser"},
        headers=bearer(world["c"]),
    )
    assert grant.status_code == 403

    members = client.get(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}/members",
        headers=bearer(world["owner"]),
    )
    assert {m["user_id"] for m in members.json()} == {
        world["a"]["user_id"], world["b"]["user_id"]
    }

    # Leaving is the member's own act too.
    left = client.delete(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}/members/{world['b']['user_id']}",
        headers=bearer(world["b"]),
    )
    assert left.status_code == 204


def test_scoped_event_leak(client, world):
    gid = world["group"]["id"]
    choir = _subgroup(client, world, "Choir")
    kitchen = _subgroup(client, world, "Kitchen Crew")
    _enroll(client, world, choir["id"], world["a"]["user_id"])
    _enroll(client, world, kitchen["id"], world["b"]["user_id"])

    scoped = _event(
        client, world, "Choir Rehearsal",
        "2026-11-07T18:00:00-05:00", "2026-11-07T20:00:00-05:00",
        subgroup_id=choir["id"],
    )
    # The scope stamps the display word from the sub-group's name.
    assert scoped["subgroup_id"] == choir["id"]
    assert scoped["sub_group"] == "Choir"
    _event(
        client, world, "Open Social",
        "2026-11-08T18:00:00-05:00", "2026-11-08T20:00:00-05:00",
    )

    def titles(who):
        rows = client.get(f"/api/v1/groups/{gid}/events", headers=bearer(who))
        assert rows.status_code == 200
        return {row["title"] for row in rows.json()}

    assert titles(world["a"]) == {"Choir Rehearsal", "Open Social"}
    # B is in the OTHER sub-group; C is in none. Both see only
    # the group-wide event — the scoped one does not exist.
    assert titles(world["b"]) == {"Open Social"}
    assert titles(world["c"]) == {"Open Social"}
    # The organiser's view is unrestricted.
    assert titles(world["owner"]) == {"Choir Rehearsal", "Open Social"}

    # By id: 404 for the outsiders, 200 for the enrolled.
    for outsider in (world["b"], world["c"]):
        got = client.get(
            f"/api/v1/groups/{gid}/events/{scoped['id']}", headers=bearer(outsider)
        )
        assert got.status_code == 404
    got = client.get(
        f"/api/v1/groups/{gid}/events/{scoped['id']}", headers=bearer(world["a"])
    )
    assert got.status_code == 200

    # The calendar agrees with the list: B's agenda has no trace.
    agenda_b = client.get(
        f"/api/v1/groups/{gid}/agenda",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=bearer(world["b"]),
    )
    assert {item["title"] for item in agenda_b.json()["items"]} == {"Open Social"}
    agenda_a = client.get(
        f"/api/v1/groups/{gid}/agenda",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=bearer(world["a"]),
    )
    assert {item["title"] for item in agenda_a.json()["items"]} == {
        "Choir Rehearsal", "Open Social"
    }

    # And search is not the side door around scoping.
    found_b = client.get(
        f"/api/v1/groups/{gid}/search", params={"q": "Rehearsal"},
        headers=bearer(world["b"]),
    )
    assert found_b.json()["results"] == []
    found_a = client.get(
        f"/api/v1/groups/{gid}/search", params={"q": "Rehearsal"},
        headers=bearer(world["a"]),
    )
    assert [hit["title"] for hit in found_a.json()["results"]] == ["Choir Rehearsal"]


def test_scoped_task_leak(client, world):
    gid = world["group"]["id"]
    kitchen = _subgroup(client, world, "Kitchen Crew")
    _enroll(client, world, kitchen["id"], world["b"]["user_id"])

    created = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Sharpen the kitchen knives", "subgroup_id": kitchen["id"]},
        headers=bearer(world["owner"]),
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["subgroup_id"] == kitchen["id"]

    def task_titles(who):
        rows = client.get(f"/api/v1/groups/{gid}/tasks", headers=bearer(who))
        assert rows.status_code == 200
        return {row["title"] for row in rows.json()}

    assert task_titles(world["b"]) == {"Sharpen the kitchen knives"}
    assert task_titles(world["a"]) == set()
    got = client.get(
        f"/api/v1/groups/{gid}/tasks/{task['id']}", headers=bearer(world["a"])
    )
    assert got.status_code == 404

    # A member cannot file work into a sub-group they cannot see.
    refused = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Sneak in", "subgroup_id": kitchen["id"]},
        headers=bearer(world["a"]),
    )
    assert refused.status_code == 403

    # Re-scoping an existing task validates the target.
    plain = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": "Group-wide chore"}, headers=bearer(world["owner"]),
    ).json()
    bad = client.patch(
        f"/api/v1/groups/{gid}/tasks/{plain['id']}",
        json={"subgroup_id": "no-such-subgroup"}, headers=bearer(world["owner"]),
    )
    assert bad.status_code == 404


def test_delete_subgroup_refused_while_scoped(client, world):
    gid = world["group"]["id"]
    choir = _subgroup(client, world, "Choir")
    event = _event(
        client, world, "Choir Rehearsal",
        "2026-11-07T18:00:00-05:00", "2026-11-07T20:00:00-05:00",
        subgroup_id=choir["id"],
    )
    refused = client.delete(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}",
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 409
    assert "1 events" in refused.json()["detail"]

    # Un-scope the event (the organiser's deliberate act), then
    # the delete goes through.
    read = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(world["owner"])
    )
    etag = read.headers["etag"]
    patched = client.patch(
        f"/api/v1/groups/{gid}/events/{event['id']}",
        json={"subgroup_id": None},
        headers={**bearer(world["owner"]), "If-Match": etag},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["subgroup_id"] is None
    assert patched.json()["sub_group"] == ""

    deleted = client.delete(
        f"/api/v1/groups/{gid}/subgroups/{choir['id']}",
        headers=bearer(world["owner"]),
    )
    assert deleted.status_code == 204
