"""MCP candidates / review-queue tool tests (roadmap B-208).

The promises these guard:

* the queue family travels the same road as every other MCP
  family — one registered internal operation per tool through
  the bridge as caller ``mcp`` — and its reads return the
  queue's own shapes: list rows with the triage filters, and
  one candidate in full (draft, flags, provenance, approval
  progress);
* approval travels the ONE approval law (B-203): the tool
  calls the same service act the HTTP door calls, so a seat
  below the group's approver role meets ``seat_cannot_approve``
  here exactly as there, an organiser seat approves as itself
  (``approved_by`` is the seat's label, never its creator's),
  and a deciding approval mints what the draft becomes;
* the other decisions behave as the queue's law says: an edit
  marks the candidate edited and leaves it UNDECIDED, a
  rejection keeps the row, and a decided candidate refuses a
  second decision with conflict;
* scope is re-checked at call time for this family too
  (B-205): a read+draft principal — a seat's default key —
  reads the queue but cannot decide anything on it, because
  candidates:decide maps only from publish and admin.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import bearer, make_group, register
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server


def _seat(client, owner, group, role="member", scopes=("read", "draft", "publish")):
    """Create one seat through the API; return its JSON body."""
    made = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={"name": "Queue Seat", "role": role, "scopes": list(scopes)},
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


def _world(client, app, settings):
    """An owner, their group, and a server on the app's bridge
    with two candidates drafted into its queue over the MCP
    draft tool itself — the family reviewing its own drafts."""
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    first = _call(
        server,
        "gpa_create_candidate_draft",
        {
            "group_id": group["id"],
            "kind": "decision",
            "title": "We meet on Saturdays",
            "created_by": "seat:queue",
        },
    )
    second = _call(
        server,
        "gpa_create_candidate_draft",
        {
            "group_id": group["id"],
            "kind": "event",
            "title": "Club night",
            "created_by": "seat:queue",
        },
    )
    return owner, group, server, first["candidate_id"], second["candidate_id"]


# --- the reads -----------------------------------------------------------------


def test_queue_tools_list_filter_and_read_in_full(client, app, settings):
    """The list carries both drafts newest-first and honours
    the kind filter; the full read carries the draft, an empty
    provenance for a hand-drafted candidate (no source quotes
    exist — shown as none, never invented), and the approval
    progress under the group's default rule (0 of 1)."""
    owner, group, server, first_id, second_id = _world(client, app, settings)
    data = _call(server, "gpa_list_candidates", {"group_id": group["id"]})
    assert data["count"] == 2
    assert {row["candidate_id"] for row in data["candidates"]} == {first_id, second_id}
    assert all(row["status"] == "pending" for row in data["candidates"])
    only = _call(
        server, "gpa_list_candidates", {"group_id": group["id"], "kind": "decision"}
    )
    assert [row["candidate_id"] for row in only["candidates"]] == [first_id]
    full = _call(
        server,
        "gpa_get_candidate",
        {"group_id": group["id"], "candidate_id": first_id},
    )
    assert full["title"] == "We meet on Saturdays"
    assert full["provenance"] == []
    assert full["approvals_recorded"] == 0 and full["approvals_required"] == 1
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_get_candidate",
            {"group_id": group["id"], "candidate_id": "no-such-candidate"},
        )
    assert "not_found" in str(caught.value)


# --- approval: the one law, over this road ---------------------------------------


def test_approve_tool_mints_through_the_one_approval_law(client, app, settings):
    """A deciding approval by the owner person approves and
    MINTS: the decision candidate becomes a recorded decision,
    linked from the candidate — the same outcome the HTTP
    door's approval produces, because it is the same service."""
    owner, group, server, first_id, _ = _world(client, app, settings)
    data = _call(
        server,
        "gpa_approve_candidate",
        {"consent": True, 
            "group_id": group["id"],
            "candidate_id": first_id,
            "actor": f"user:{owner['user_id']}",
            "role": "owner",
            "user_id": owner["user_id"],
        },
    )
    assert data["status"] == "approved"
    assert data["approved_by"] == f"user:{owner['user_id']}"
    assert data["resulting_entity_type"] == "decision"
    assert data["resulting_entity_id"]


def test_approve_tool_applies_the_seat_law_identically(client, app, settings):
    """B-203 over the bus: a MEMBER seat's approval is refused
    seat_cannot_approve (below the group's approver role), and
    an ORGANISER seat approves as itself — its label, not its
    creator's, is the approver of record."""
    owner, group, server, first_id, second_id = _world(client, app, settings)
    member_seat = _seat(client, owner, group, role="member")
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_approve_candidate",
            {"consent": True, 
                "group_id": group["id"],
                "candidate_id": first_id,
                "actor": f"seat:{member_seat['id']}",
                "role": "member",
                "seat_id": member_seat["id"],
            },
        )
    assert "seat_cannot_approve" in str(caught.value)
    organiser_seat = _seat(client, owner, group, role="organiser")
    data = _call(
        server,
        "gpa_approve_candidate",
        {"consent": True, 
            "group_id": group["id"],
            "candidate_id": first_id,
            "actor": f"seat:{organiser_seat['id']}",
            "role": "organiser",
            "seat_id": organiser_seat["id"],
        },
    )
    assert data["status"] == "approved"
    assert data["approved_by"] == f"seat:{organiser_seat['id']}"


# --- the other decisions ---------------------------------------------------------


def test_edit_marks_edited_and_reject_decides_once(client, app, settings):
    """An edit replaces the draft and marks the candidate
    edited — still pending a decision. A rejection then
    decides it; a second decision on the decided row is a
    conflict, in the backend's own code."""
    owner, group, server, _, second_id = _world(client, app, settings)
    edited = _call(
        server,
        "gpa_edit_candidate",
        {
            "group_id": group["id"],
            "candidate_id": second_id,
            "actor": f"user:{owner['user_id']}",
            "draft": {"fields": {"title": {"value": "Club night, moved"}}},
        },
    )
    assert edited["status"] == "edited"
    rejected = _call(
        server,
        "gpa_reject_candidate",
        {"consent": True, 
            "group_id": group["id"],
            "candidate_id": second_id,
            "actor": f"user:{owner['user_id']}",
        },
    )
    assert rejected["status"] == "rejected"
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_reject_candidate",
            {"consent": True, 
                "group_id": group["id"],
                "candidate_id": second_id,
                "actor": f"user:{owner['user_id']}",
            },
        )
    assert "conflict" in str(caught.value)


# --- scope is re-checked at call time for this family (B-205) ---------------------


def test_decision_tools_refuse_a_draft_scoped_principal(client, app, settings):
    """A read+draft principal (a seat's default key) reads the
    queue and cannot decide: all three decision tools refuse
    at the guard with insufficient_scope, before any operation
    runs — while the same server's list tool answers."""
    owner, group, server, first_id, _ = _world(client, app, settings)
    bridge = InternalBridge(settings, database=app.state.db)
    drafter = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"read", "draft"})
        ),
    )
    assert _call(drafter, "gpa_list_candidates", {"group_id": group["id"]})["count"] == 2
    for name, args in (
        (
            "gpa_approve_candidate",
            {"consent": True, 
                "group_id": group["id"],
                "candidate_id": first_id,
                "actor": "seat:s1",
                "role": "organiser",
                "seat_id": "s1",
            },
        ),
        (
            "gpa_reject_candidate",
            {"consent": True, "group_id": group["id"], "candidate_id": first_id, "actor": "seat:s1"},
        ),
        (
            "gpa_edit_candidate",
            {
                "group_id": group["id"],
                "candidate_id": first_id,
                "actor": "seat:s1",
                "draft": {"fields": {}},
            },
        ),
    ):
        with pytest.raises(Exception) as caught:
            _call(drafter, name, args)
        assert "insufficient_scope" in str(caught.value), name
