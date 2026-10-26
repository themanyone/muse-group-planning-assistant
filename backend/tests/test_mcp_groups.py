"""MCP groups / members / profile tool tests (roadmap B-207).

The promises these guard:

* the first MCP domain family — groups, members, profile —
  travels the same road as every other tool: one registered
  internal operation per tool through the bridge as caller
  ``mcp``, never a service import in the MCP package (the
  B-204 import scan already covers the whole package);
* the roster is one answer with both principal kinds in it:
  person memberships and agent seats, each labelled as itself
  (the seat-equality law, B-202);
* the edits land through the owning services and commit: a
  group-settings edit and a seat's own profile edit are read
  back in fresh sessions afterwards, audited under the acting
  principal's label — a seat's face is the seat's act, never
  the gateway's;
* the family's closed vocabularies hold at the operation
  door: a settings field outside the five, a profile field
  outside the kind's set, and a profile payload naming both
  principals (or neither) are each refused by name;
* scope is re-checked at call time for this family too
  (B-205): a read-only principal reads the group and roster
  but cannot edit a profile (draft) or a group's settings
  (admin), and the refusals lead with ``insufficient_scope``.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import bearer, make_group, register
from gpa.errors import AppError
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server
from gpa.models.groups import Group


def _seat(client, owner, group, scopes=("read", "draft"), **profile):
    """Create one seat through the API; return its JSON body."""
    made = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={"name": "Family Seat", "role": "member", "scopes": list(scopes), **profile},
        headers=bearer(owner),
    )
    assert made.status_code == 201, made.text
    return made.json()


def _call(server, name, args):
    """Call one tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _world(client):
    """An owner, their group, and one seat in it."""
    owner = register(client)
    group = make_group(client, owner, description="A circle that plans.")
    seat = _seat(client, owner, group, description="Keeps the roster.")
    return owner, group, seat


# --- the reads: group facts and the one roster -------------------------------


def test_get_group_tool_reads_the_group_through_the_bridge(client, app, settings):
    """gpa_get_group returns the group's plain facts, and a
    dangling id is refused with the backend's not_found code
    leading the tool error — the same answer the API gives."""
    owner, group, seat = _world(client)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    data = _call(server, "gpa_get_group", {"group_id": group["id"]})
    assert data["group_id"] == group["id"]
    assert data["name"] == group["name"] and data["slug"] == group["slug"]
    assert data["description"] == "A circle that plans."
    assert data["visibility"] == "private"
    with pytest.raises(Exception) as caught:
        _call(server, "gpa_get_group", {"group_id": "no-such-group"})
    assert "not_found" in str(caught.value)


def test_list_members_tool_returns_people_and_seats_in_one_roster(client, app, settings):
    """The roster names both principal kinds, each as itself:
    the owner as an active person membership, the seat as a
    seat with its role and its B-202 profile fields."""
    owner, group, seat = _world(client)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    data = _call(server, "gpa_list_members", {"group_id": group["id"]})
    assert data["group_id"] == group["id"] and data["count"] == 2
    (member,) = data["members"]
    assert member["user_id"] == owner["user_id"]
    assert member["role"] == "owner" and member["status"] == "active"
    (seat_row,) = data["seats"]
    assert seat_row["seat_id"] == seat["id"] and seat_row["name"] == "Family Seat"
    assert seat_row["role"] == "member"
    assert seat_row["description"] == "Keeps the roster."


# --- the edits: settings and profiles, committed and audited -------------------


def test_update_group_tool_edits_settings_and_commits(client, app, settings):
    """gpa_update_group changes only the fields given, through
    the groups service: the edit is read back from a FRESH
    session (the bridge's commit is real), and the service's
    audit-shaped validation still holds — a visibility outside
    the closed vocabulary is refused, unchanged."""
    owner, group, seat = _world(client)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    data = _call(
        server,
        "gpa_update_group",
        {
            "group_id": group["id"],
            "actor": "seat:family",
            "description": "Now with a new description.",
            "default_place": "The longhouse",
        },
    )
    assert data["description"] == "Now with a new description."
    assert data["default_place"] == "The longhouse"
    assert data["name"] == group["name"]  # untouched fields stay
    session = app.state.db.session()
    try:
        row = session.get(Group, group["id"])
        assert row.description == "Now with a new description."
    finally:
        session.close()
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_update_group",
            {"group_id": group["id"], "actor": "seat:family", "visibility": "loud"},
        )
    assert "validation_error" in str(caught.value)


def test_profile_tools_read_and_edit_a_person(client, app, settings):
    """A person's profile reads and edits through profile.get /
    profile.update: name, timezone, locale — and the read-back
    through the tool itself shows the committed edit."""
    owner, group, seat = _world(client)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    before = _call(server, "gpa_get_profile", {"user_id": owner["user_id"]})
    assert before["kind"] == "user" and before["email"] == owner["email"]
    updated = _call(
        server,
        "gpa_update_profile",
        {
            "actor": f"user:{owner['user_id']}",
            "user_id": owner["user_id"],
            "name": "Renamed Person",
            "timezone": "America/New_York",
        },
    )
    assert updated["name"] == "Renamed Person"
    assert updated["timezone"] == "America/New_York"
    after = _call(server, "gpa_get_profile", {"user_id": owner["user_id"]})
    assert after["name"] == "Renamed Person" and after["locale"] == "en"


def test_profile_tools_read_and_edit_a_seat_as_itself(client, app, settings):
    """A seat's profile is the seat's own face (B-202): read by
    seat id inside its group, edited under the seat's own audit
    label, and another group's seat id reads as not_found —
    a seat exists only inside its one group."""
    owner, group, seat = _world(client)
    other = make_group(client, owner, name="Elsewhere")
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    before = _call(
        server,
        "gpa_get_profile",
        {"seat_id": seat["id"], "group_id": group["id"]},
    )
    assert before["kind"] == "seat" and before["description"] == "Keeps the roster."
    updated = _call(
        server,
        "gpa_update_profile",
        {
            "actor": f"seat:{seat['id']}",
            "seat_id": seat["id"],
            "group_id": group["id"],
            "description": "Keeps the roster and the minutes.",
            "avatar_url": "https://example.org/seat.png",
        },
    )
    assert updated["description"] == "Keeps the roster and the minutes."
    assert updated["avatar_url"] == "https://example.org/seat.png"
    assert updated["role"] == "member"  # role is not profile; unmoved
    with pytest.raises(Exception) as caught:
        _call(server, "gpa_get_profile", {"seat_id": seat["id"], "group_id": other["id"]})
    assert "not_found" in str(caught.value)


# --- the closed vocabularies at the operation door -----------------------------


def test_operations_refuse_out_of_family_fields_by_name(client, app, settings):
    """At the bridge door directly: a group-settings field
    outside the five, a seat-profile field outside its set, and
    a profile payload naming both principals each fail with
    internal_bad_payload — the refusal names what was refused,
    so a caller learns the vocabulary from the error itself."""
    owner, group, seat = _world(client)
    bridge = InternalBridge(settings, database=app.state.db)
    with pytest.raises(AppError) as caught:
        bridge.call(
            "groups.update",
            {
                "group_id": group["id"],
                "actor": "seat:family",
                "changes": {"min_role": "admin"},
            },
        )
    assert caught.value.code == "internal_bad_payload"
    assert "min_role" in caught.value.detail
    with pytest.raises(AppError) as caught:
        bridge.call(
            "profile.update",
            {
                "actor": f"seat:{seat['id']}",
                "seat_id": seat["id"],
                "group_id": group["id"],
                "changes": {"role": "admin"},
            },
        )
    assert caught.value.code == "internal_bad_payload"
    assert "role" in caught.value.detail
    with pytest.raises(AppError) as caught:
        bridge.call(
            "profile.get",
            {"user_id": owner["user_id"], "seat_id": seat["id"], "group_id": group["id"]},
        )
    assert caught.value.code == "internal_bad_payload"


# --- scope is re-checked at call time for this family (B-205) -------------------


def test_family_tools_recheck_scope_at_call_time(client, app, settings):
    """A read-only seat principal reads the group, the roster,
    and a profile — and is refused, per call, at the family's
    two edit doors (draft for profile, admin for settings),
    each refusal leading with insufficient_scope. The same
    server under an admin principal performs the settings edit."""
    owner, group, seat = _world(client)
    bridge = InternalBridge(settings, database=app.state.db)
    reader = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id=seat["id"], scopes=frozenset({"read"})
        ),
    )
    assert _call(reader, "gpa_get_group", {"group_id": group["id"]})["name"]
    assert _call(reader, "gpa_list_members", {"group_id": group["id"]})["count"] == 2
    with pytest.raises(Exception) as caught:
        _call(
            reader,
            "gpa_update_profile",
            {"actor": f"seat:{seat['id']}", "user_id": owner["user_id"], "name": "X"},
        )
    assert "insufficient_scope" in str(caught.value)
    with pytest.raises(Exception) as caught:
        _call(
            reader,
            "gpa_update_group",
            {"group_id": group["id"], "actor": f"seat:{seat['id']}", "name": "X"},
        )
    assert "insufficient_scope" in str(caught.value)

    admin = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id=seat["id"], scopes=frozenset({"admin"})
        ),
    )
    edited = _call(
        admin,
        "gpa_update_group",
        {"group_id": group["id"], "actor": f"seat:{seat['id']}", "name": "Renamed Circle"},
    )
    assert edited["name"] == "Renamed Circle"
