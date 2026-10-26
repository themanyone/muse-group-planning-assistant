"""MCP notifications / digests / announcements tests
(roadmap B-212).

The promises these guard:

* announcing over this surface obeys the backend's person
  law in the service's own words: a SEAT caller is refused
  ("a seat drafts for the review queue, it does not
  announce"), a member-role person is refused by the
  operation's role law, and an organiser PERSON announces —
  the board then lists the post, pinned first;
* inboxes and preferences are a person's: the inbox read
  answers for the named user (empty, honestly, before
  anything was sent to them) and the preferences read
  returns the effective policy — every switch, defaults
  filled in — that the service computes;
* the digest tool is a PREVIEW by contract: composing
  stores an UNSENT digest (sent_at empty) that the list
  then carries; a member role is refused the composing;
* both write tools are publish at call time (B-205): a
  read+draft principal reads the board and cannot announce
  or compose.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import bearer, make_group, register
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server


def _call(server, name, args):
    """Call one tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _world(client, app, settings):
    """An owner, their group, and a server on the app's
    bridge. Nothing is announced or sent yet."""
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    return owner, group, build_server(bridge)


def _announce(server, group, owner, **overrides):
    """Announce as the owner person through the tool."""
    args = {
        "group_id": group["id"],
        "actor": f"user:{owner['user_id']}",
        "role": "owner",
        "title": "Hall booked",
        "body": "The hall is ours for Saturday.",
        "user_id": owner["user_id"],
    }
    args.update(overrides)
    return _call(server, "gpa_create_announcement", args)


# --- announcing: the person law, twice gated -----------------------------------------


def test_an_organiser_person_announces_and_the_board_lists_it(client, app, settings):
    """The owner's post lands with them as its author of
    record; a second, pinned post sorts first on the board
    — the service's order, served whole."""
    owner, group, server = _world(client, app, settings)
    posted = _announce(server, group, owner)
    assert posted["title"] == "Hall booked"
    assert posted["author_user_id"] == owner["user_id"]
    _announce(server, group, owner, title="Read first", pinned=True)
    board = _call(
        server, "gpa_list_announcements", {"group_id": group["id"], "role": "member"}
    )
    assert [row["title"] for row in board["announcements"]] == [
        "Read first",
        "Hall booked",
    ]


def test_a_seat_cannot_announce_in_the_services_own_words(client, app, settings):
    """B-043's law over the bus: the create call names a
    seat, and the refusal that comes back is the SERVICE's
    (forbidden, 'does not announce') — this surface adds no
    paraphrase of its own, and no post reaches the board."""
    owner, group, server = _world(client, app, settings)
    made_seat = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={"name": "Board Seat", "role": "organiser", "scopes": ["read", "publish"]},
        headers=bearer(owner),
    )
    assert made_seat.status_code == 201, made_seat.text
    seat = made_seat.json()
    with pytest.raises(Exception) as caught:
        _announce(
            server,
            group,
            owner,
            actor=f"seat:{seat['id']}",
            user_id=None,
            seat_id=seat["id"],
        )
    assert "does not announce" in str(caught.value)
    board = _call(
        server, "gpa_list_announcements", {"group_id": group["id"], "role": "member"}
    )
    assert board["count"] == 0


def test_a_member_role_person_cannot_announce(client, app, settings):
    """The operation's role law is the other gate: a person
    named at member role is refused forbidden before the
    service is ever asked."""
    owner, group, server = _world(client, app, settings)
    with pytest.raises(Exception) as caught:
        _announce(server, group, owner, role="member")
    assert "forbidden" in str(caught.value)


# --- a person's inbox and policy --------------------------------------------------------


def test_inbox_and_preferences_reads_belong_to_the_person(client, app, settings):
    """The owner's inbox is honestly empty (nothing has been
    sent to them in this world) with unread stated as 0;
    their preferences view names the group and carries the
    full switch grid the service computes."""
    owner, group, server = _world(client, app, settings)
    inbox = _call(server, "gpa_get_inbox", {"user_id": owner["user_id"]})
    assert inbox["items"] == [] and inbox["unread"] == 0
    prefs = _call(
        server,
        "gpa_get_notification_preferences",
        {"group_id": group["id"], "user_id": owner["user_id"]},
    )
    assert prefs["group_id"] == group["id"]
    assert prefs["switches"]  # the full channel × category grid
    with pytest.raises(Exception) as caught:
        _call(server, "gpa_get_inbox", {"user_id": "no-such-person"})
    assert "not_found" in str(caught.value)


# --- the digest preview ---------------------------------------------------------------------


def test_digest_preview_composes_and_stores_unsent(client, app, settings):
    """An organiser-role preview composes the week's digest
    (subject names the group) and stores it UNSENT; the
    list carries it with sent_at empty. A member role is
    refused the composing."""
    owner, group, server = _world(client, app, settings)
    with pytest.raises(Exception) as caught:
        _call(
            server, "gpa_preview_digest", {"group_id": group["id"], "role": "member"}
        )
    assert "forbidden" in str(caught.value)
    preview = _call(
        server, "gpa_preview_digest", {"group_id": group["id"], "role": "organiser"}
    )
    assert preview["sent_at"] is None
    assert group["name"] in preview["subject"]
    listing = _call(server, "gpa_list_digests", {"group_id": group["id"]})
    assert [row["digest_id"] for row in listing["digests"]] == [preview["digest_id"]]


# --- the write tools are publish at call time -------------------------------------------------


def test_announce_and_compose_refuse_a_draft_principal(client, app, settings):
    """A read+draft principal reads the board and the digest
    list, and is refused both write tools at the guard."""
    owner, group, server = _world(client, app, settings)
    bridge = InternalBridge(settings, database=app.state.db)
    drafter = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"read", "draft"})
        ),
    )
    assert (
        _call(
            drafter,
            "gpa_list_announcements",
            {"group_id": group["id"], "role": "member"},
        )["count"]
        == 0
    )
    with pytest.raises(Exception) as caught:
        _call(
            drafter,
            "gpa_create_announcement",
            {
                "group_id": group["id"],
                "actor": "seat:s1",
                "role": "organiser",
                "title": "Nope",
                "body": "Nope.",
                "seat_id": "s1",
            },
        )
    assert "insufficient_scope" in str(caught.value)
    with pytest.raises(Exception) as caught:
        _call(
            drafter, "gpa_preview_digest", {"group_id": group["id"], "role": "organiser"}
        )
    assert "insufficient_scope" in str(caught.value)
