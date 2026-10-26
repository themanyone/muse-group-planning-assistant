"""MCP sources / extraction-request tool tests (roadmap B-210).

The promises these guard:

* filing travels the sources service's own road: a created
  source carries its permission record (granted_by is the
  actor, granted_scope verbatim) and its chunks in order,
  and the full read returns those chunks OPENED through the
  service's one funnel — the words as the group may read
  them, though they rest sealed;
* appending continues the source's one true order
  (positions are the service's, never the caller's);
* the extraction request is doubly gated as at its HTTP
  door — the publish credential at the guard AND the
  organiser role in the operation — and what it returns is
  a QUEUED job's id: the run belongs to the worker, and no
  candidates exist until it runs (the queue stays empty in
  these tests, which is itself the assertion);
* the family's scope split holds at call time (B-205): a
  read+draft principal (a seat's default key) files and
  reads but cannot commission a run.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import make_group, register
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server

CHUNKS = [
    {"text": "Club night this Saturday at the hall", "sender": "Ana"},
    {"text": "I can bring chairs", "sender": "Bo"},
]


def _call(server, name, args):
    """Call one tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _world(client, app, settings):
    """An owner, their group, a server on the app's bridge,
    and one source filed through the create tool itself."""
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    filed = _call(
        server,
        "gpa_create_source",
        {
            "group_id": group["id"],
            "actor": f"user:{owner['user_id']}",
            "kind": "chat_paste",
            "title": "Planning chat",
            "granted_scope": "whole chat, Ana consented",
            "chunks": CHUNKS,
        },
    )
    return owner, group, server, filed


# --- filing and reading -------------------------------------------------------------


def test_create_files_the_permission_record_and_chunks(client, app, settings):
    """The created source answers with the grant exactly as
    filed — by, scope, kind — and a chunk count of two; the
    list shows the same row without opening any chunk."""
    owner, group, server, filed = _world(client, app, settings)
    assert filed["granted_by"] == f"user:{owner['user_id']}"
    assert filed["granted_scope"] == "whole chat, Ana consented"
    assert filed["kind"] == "chat_paste" and filed["chunk_count"] == 2
    listing = _call(server, "gpa_list_sources", {"group_id": group["id"]})
    assert listing["count"] == 1
    assert listing["sources"][0]["source_id"] == filed["source_id"]
    assert "chunks" not in listing["sources"][0]


def test_get_returns_chunks_opened_in_position_order(client, app, settings):
    """The full read returns the chunks in their one true
    order with text and sender OPENED — readable words,
    though the store keeps them sealed (B-167)."""
    owner, group, server, filed = _world(client, app, settings)
    full = _call(
        server,
        "gpa_get_source",
        {"group_id": group["id"], "source_id": filed["source_id"]},
    )
    assert [chunk["position"] for chunk in full["chunks"]] == [0, 1]
    assert full["chunks"][0]["text"] == "Club night this Saturday at the hall"
    assert full["chunks"][0]["sender"] == "Ana"
    assert full["chunks"][1]["sender"] == "Bo"
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_get_source",
            {"group_id": group["id"], "source_id": "no-such-source"},
        )
    assert "not_found" in str(caught.value)


def test_append_continues_the_one_true_order(client, app, settings):
    """Appended chunks take the NEXT positions (2, not 0):
    the order provenance points into never forks."""
    owner, group, server, filed = _world(client, app, settings)
    grown = _call(
        server,
        "gpa_append_source_chunks",
        {
            "group_id": group["id"],
            "source_id": filed["source_id"],
            "chunks": [{"text": "Starts at seven", "sender": "Ana"}],
        },
    )
    assert grown["chunk_count"] == 3
    full = _call(
        server,
        "gpa_get_source",
        {"group_id": group["id"], "source_id": filed["source_id"]},
    )
    assert [chunk["position"] for chunk in full["chunks"]] == [0, 1, 2]
    assert full["chunks"][2]["text"] == "Starts at seven"


def test_create_refuses_an_unnamed_kind(client, app, settings):
    """A kind outside the roadmap's named set is refused by
    the service's own validation — over this surface too."""
    owner, group, server, _ = _world(client, app, settings)
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_create_source",
            {
                "group_id": group["id"],
                "actor": f"user:{owner['user_id']}",
                "kind": "telepathy",
                "chunks": [],
            },
        )
    assert "validation_error" in str(caught.value)


# --- the extraction request: two gates, one queue -------------------------------------


def test_request_queues_a_job_and_needs_the_organiser_role(client, app, settings):
    """A member-role request is refused forbidden by the
    operation; an organiser request returns a job id — and
    the review queue is still EMPTY afterwards, because the
    call queued work rather than performing it."""
    owner, group, server, filed = _world(client, app, settings)
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_request_extraction",
            {
                "group_id": group["id"],
                "source_id": filed["source_id"],
                "actor": f"user:{owner['user_id']}",
                "role": "member",
            },
        )
    assert "forbidden" in str(caught.value)
    queued = _call(
        server,
        "gpa_request_extraction",
        {
            "group_id": group["id"],
            "source_id": filed["source_id"],
            "actor": f"user:{owner['user_id']}",
            "role": "organiser",
            "kinds": ["event"],
        },
    )
    assert queued["job_id"] and queued["kinds"] == ["event"]
    queue = _call(server, "gpa_list_candidates", {"group_id": group["id"]})
    assert queue["count"] == 0


def test_a_draft_principal_files_but_cannot_commission(client, app, settings):
    """Call-time scopes for this family: read+draft reads
    and files (the member acts), and is refused the request
    tool at the guard — commissioning is publish-grade."""
    owner, group, server, filed = _world(client, app, settings)
    bridge = InternalBridge(settings, database=app.state.db)
    drafter = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"read", "draft"})
        ),
    )
    assert _call(drafter, "gpa_list_sources", {"group_id": group["id"]})["count"] == 1
    with pytest.raises(Exception) as caught:
        _call(
            drafter,
            "gpa_request_extraction",
            {
                "group_id": group["id"],
                "source_id": filed["source_id"],
                "actor": "seat:s1",
                "role": "organiser",
            },
        )
    assert "insufficient_scope" in str(caught.value)
