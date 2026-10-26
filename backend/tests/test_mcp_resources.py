"""MCP resource tests (roadmap B-213).

The promises these guard:

* the shelf's shape: the six group templates and the docs
  template are listed with their correct MIME types, and
  the two static resources (the OpenAPI document, the docs
  index) are listed as resources;
* the calendar's two forms agree: the same published event
  is in the JSON document and in the iCalendar rendering
  (UTC moment, escaped summary), and a DRAFT event is in
  neither — resources read at member role, so the bright
  line holds on the shelf exactly as at the doors;
* the single-event resource is the bright line in one read:
  a draft is not_found, the published event reads whole;
* decisions carry standing truth only, the queue resource
  summarises (totals + the pending rows), and the
  memory-map counts the group's visible shape;
* the static shelf serves the product's own paper: the
  OpenAPI document parses as the contract, the docs index
  names the chapters, a chapter reads verbatim — and a
  name that is not a chapter (including a path trick) is
  not_found, never the filesystem;
* reading is scope-checked per read (B-205's law for the
  shelf): a principal without the read scope is refused
  insufficient_scope at the resource, not served quietly.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import bearer, make_group, register
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server

START = "2026-11-06T18:30:00-05:00"
END = "2026-11-06T21:00:00-05:00"


def _call(server, name, args):
    """Call one tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _read(server, uri):
    """Read one resource through the server's own dispatch."""

    async def run():
        """The one read, on the server's event loop."""
        return await server.read_resource(uri)

    contents = asyncio.run(run())
    return contents[0].content


def _list(server, templates: bool):
    """List the shelf (resources or templates) as dicts."""

    async def run():
        """The one listing, on the server's event loop."""
        if templates:
            return await server.list_resource_templates()
        return await server.list_resources()

    return asyncio.run(run())


def _world(client, app, settings):
    """An owner, their group, one DRAFT event entered at the
    HTTP door, and a server on the app's bridge."""
    owner = register(client)
    group = make_group(client, owner)
    made = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Harvest Supper",
            "starts_at": START,
            "ends_at": END,
            "place_name": "Village Hall",
            "status": "draft",
        },
        headers=bearer(owner),
    )
    assert made.status_code == 201, made.text
    bridge = InternalBridge(settings, database=app.state.db)
    return owner, group, build_server(bridge), made.json()["id"]


def _publish(server, group, event_id, owner):
    """Publish the draft through B-204's own tool."""
    return _call(
        server,
        "gpa_publish_event",
        {"consent": True, 
            "group_id": group["id"],
            "event_id": event_id,
            "actor": f"user:{owner['user_id']}",
        },
    )


# --- the shelf's shape ----------------------------------------------------------


def test_the_shelf_lists_its_templates_and_statics_with_mime_types(
    client, app, settings
):
    """Every template the roadmap names is listed, with the
    MIME type a client renders by; the two statics are
    listed as plain resources."""
    _owner, _group, server, _event_id = _world(client, app, settings)
    templates = {t.uriTemplate: t.mimeType for t in _list(server, templates=True)}
    assert templates["gpa://groups/{group_id}/calendar.json"] == "application/json"
    assert templates["gpa://groups/{group_id}/calendar.ics"] == "text/calendar"
    assert (
        templates["gpa://groups/{group_id}/events/{event_id}.json"]
        == "application/json"
    )
    assert templates["gpa://groups/{group_id}/decisions.json"] == "application/json"
    assert templates["gpa://groups/{group_id}/queue.json"] == "application/json"
    assert (
        templates["gpa://groups/{group_id}/memory-map.json"] == "application/json"
    )
    assert templates["gpa://docs/{chapter}"] == "text/markdown"
    statics = {str(r.uri): r.mimeType for r in _list(server, templates=False)}
    assert statics["gpa://openapi.json"] == "application/json"
    assert statics["gpa://docs.json"] == "application/json"


# --- the calendar, both forms -----------------------------------------------------


def test_calendar_forms_agree_and_drafts_are_absent(client, app, settings):
    """One fetch, two renderings: the published event is in
    both (its 18:30 -05:00 start is 23:30 UTC on the iCal
    line); the still-draft event is in neither — member
    view, the bright line, on the shelf."""
    owner, group, server, event_id = _world(client, app, settings)
    draft_uri = f"gpa://groups/{group['id']}/calendar.json"
    assert json.loads(_read(server, draft_uri))["count"] == 0
    _publish(server, group, event_id, owner)
    document = json.loads(_read(server, draft_uri))
    assert [row["event_id"] for row in document["events"]] == [event_id]
    ical = _read(server, f"gpa://groups/{group['id']}/calendar.ics")
    assert "BEGIN:VEVENT" in ical and "SUMMARY:Harvest Supper" in ical
    assert "DTSTART:20261106T233000Z" in ical
    assert "LOCATION:Village Hall" in ical


def test_event_resource_is_the_bright_line_in_one_read(client, app, settings):
    """The draft's own URI answers not_found (it does not
    exist for a member), and after publishing the same URI
    reads the event whole."""
    owner, group, server, event_id = _world(client, app, settings)
    uri = f"gpa://groups/{group['id']}/events/{event_id}.json"
    with pytest.raises(Exception) as caught:
        _read(server, uri)
    assert "not_found" in str(caught.value)
    _publish(server, group, event_id, owner)
    event = json.loads(_read(server, uri))
    assert event["title"] == "Harvest Supper" and event["status"] == "published"


# --- truth, queue, and the map --------------------------------------------------------


def test_decisions_queue_and_memory_map(client, app, settings):
    """Decisions start honestly empty (nothing stands yet);
    a drafted candidate appears in the queue summary's
    pending rows and totals; the memory-map counts the
    group's visible shape and names its sibling URIs."""
    owner, group, server, event_id = _world(client, app, settings)
    _publish(server, group, event_id, owner)
    _call(
        server,
        "gpa_create_candidate_draft",
        {
            "group_id": group["id"],
            "kind": "task",
            "title": "Book the hall",
            "created_by": f"user:{owner['user_id']}",
            "draft": {"title": "Book the hall"},
        },
    )
    decisions = json.loads(_read(server, f"gpa://groups/{group['id']}/decisions.json"))
    assert decisions["count"] == 0 and decisions["decisions"] == []
    queue = json.loads(_read(server, f"gpa://groups/{group['id']}/queue.json"))
    assert queue["count"] == 1 and queue["by_status"] == {"pending": 1}
    assert queue["by_kind"] == {"task": 1}
    assert [row["title"] for row in queue["pending"]] == ["Book the hall"]
    memory_map = json.loads(
        _read(server, f"gpa://groups/{group['id']}/memory-map.json")
    )
    assert memory_map["group"]["group_id"] == group["id"]
    assert memory_map["counts"]["members"] == 1
    assert memory_map["counts"]["events"] == 1
    assert memory_map["counts"]["queue_pending"] == 1
    assert memory_map["counts"]["decisions_current"] == 0
    assert (
        memory_map["resources"]["calendar_ics"]
        == f"gpa://groups/{group['id']}/calendar.ics"
    )


# --- the static shelf --------------------------------------------------------------------


def test_the_static_shelf_serves_the_products_own_paper(client, app, settings):
    """The OpenAPI resource IS the contract (it parses and
    carries paths); the docs index names the user-guide
    chapter; the chapter reads verbatim from its title."""
    _owner, _group, server, _event_id = _world(client, app, settings)
    contract = json.loads(_read(server, "gpa://openapi.json"))
    assert contract["openapi"].startswith("3.") and contract["paths"]
    index = json.loads(_read(server, "gpa://docs.json"))
    names = [row["name"] for row in index["chapters"]]
    assert "01-user-guide-start-and-groups.md" in names
    chapter = _read(server, "gpa://docs/01-user-guide-start-and-groups.md")
    assert chapter.startswith("# User guide I — Start + groups")


def test_the_docs_shelf_refuses_names_that_are_not_chapters(client, app, settings):
    """The allow-list is the directory itself: a made-up
    chapter, a parent-path trick, and another file's name
    are all not_found — the shelf serves the manual only."""
    _owner, _group, server, _event_id = _world(client, app, settings)
    for uri in (
        "gpa://docs/99-no-such-chapter.md",
        "gpa://docs/README.txt",
    ):
        with pytest.raises(Exception) as caught:
            _read(server, uri)
        assert "not_found" in str(caught.value), uri
    # The parent-path trick never even reaches the chapter
    # function: the shelf's router refuses the URI itself as
    # unknown — refused either way, and never served.
    with pytest.raises(Exception) as caught:
        _read(server, "gpa://docs/../OPENAPI.json")
    assert "Unknown resource" in str(caught.value)


# --- the read-scope guard ---------------------------------------------------------------------


def test_a_principal_without_read_scope_cannot_read_the_shelf(
    client, app, settings
):
    """A draft-only principal (a seat's writing key without
    its reading half, say) is refused insufficient_scope at
    the resource — the guard runs per read, not at connect."""
    _owner, group, _server, _event_id = _world(client, app, settings)
    bridge = InternalBridge(settings, database=app.state.db)
    writer = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"draft"})
        ),
    )
    with pytest.raises(Exception) as caught:
        _read(writer, f"gpa://groups/{group['id']}/calendar.json")
    assert "insufficient_scope" in str(caught.value)
    with pytest.raises(Exception) as caught:
        _read(writer, "gpa://openapi.json")
    assert "insufficient_scope" in str(caught.value)
