"""MCP events / RSVPs / calendar tool tests (roadmap B-209).

The promises these guard:

* the family's reads honour the visibility BRIGHT LINE over
  this surface exactly as at the HTTP doors: a draft is in an
  organiser-role list and absent from a member-role list, and
  reading one below organiser is not_found — the event does
  not exist for that viewer, it is never shown redacted;
* answering travels the RSVP service's own road: a seat
  answers for itself (draft scope — its default key can),
  the answer lands in the list with its summary counts, and
  a cancelled event refuses further answers;
* cancelling is doubly gated here as at its HTTP door: the
  publish credential scope at the guard AND the organiser
  role in the operation — a member-role call is refused
  forbidden even with a publish credential in hand, and a
  cancelled event stays, marked, with its reason;
* the agenda returns the window's occurrences through the
  calendar service's expansion.
"""

from __future__ import annotations

import asyncio

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
            # Direct entry publishes by default; this world
            # wants the DRAFT the bright-line test reads.
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


# --- the bright line over this surface ------------------------------------------


def test_event_reads_honour_the_bright_line(client, app, settings):
    """The draft is visible at organiser role and invisible
    at member role — in the list AND at the single read,
    where below-organiser is not_found, not a redaction."""
    owner, group, server, event_id = _world(client, app, settings)
    as_member = _call(
        server, "gpa_list_events", {"group_id": group["id"], "role": "member"}
    )
    assert as_member["count"] == 0
    as_organiser = _call(
        server, "gpa_list_events", {"group_id": group["id"], "role": "organiser"}
    )
    assert [row["event_id"] for row in as_organiser["events"]] == [event_id]
    assert as_organiser["events"][0]["status"] == "draft"
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_get_event",
            {"group_id": group["id"], "event_id": event_id, "role": "member"},
        )
    assert "not_found" in str(caught.value)
    one = _call(
        server,
        "gpa_get_event",
        {"group_id": group["id"], "event_id": event_id, "role": "organiser"},
    )
    assert one["title"] == "Harvest Supper" and one["place_name"] == "Village Hall"


# --- answering ---------------------------------------------------------------------


def test_rsvp_set_and_list_travel_the_service_road(client, app, settings):
    """Publish, then a seat answers for itself through the
    tool: the answer is the seat's row, the list shows it,
    and the summary counts it as coming."""
    owner, group, server, event_id = _world(client, app, settings)
    _publish(server, group, event_id, owner)
    made_seat = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={"name": "Answering Seat", "role": "member", "scopes": ["read", "draft"]},
        headers=bearer(owner),
    )
    assert made_seat.status_code == 201, made_seat.text
    seat = made_seat.json()
    answer = _call(
        server,
        "gpa_set_rsvp",
        {
            "group_id": group["id"],
            "event_id": event_id,
            "principal_label": f"seat:{seat['id']}",
            "status": "coming",
            "seat_id": seat["id"],
            "guests": 1,
        },
    )
    assert answer["status"] == "coming" and answer["guests"] == 1
    listing = _call(
        server, "gpa_list_rsvps", {"group_id": group["id"], "event_id": event_id}
    )
    assert [row["principal_label"] for row in listing["rsvps"]] == [f"seat:{seat['id']}"]
    assert listing["summary"]["coming_parties"] == 1


# --- cancelling: two gates ---------------------------------------------------------


def test_cancel_needs_the_organiser_role_and_stays_visible(client, app, settings):
    """A member-role cancel is refused forbidden by the
    operation itself (even on this scope-less local server);
    an organiser cancel lands with its reason, the event
    STAYS readable as cancelled, a second cancel is a
    conflict, and the cancelled event refuses new answers."""
    owner, group, server, event_id = _world(client, app, settings)
    _publish(server, group, event_id, owner)
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_cancel_event",
            {"consent": True, 
                "group_id": group["id"],
                "event_id": event_id,
                "actor": f"user:{owner['user_id']}",
                "role": "member",
            },
        )
    assert "forbidden" in str(caught.value)
    cancelled = _call(
        server,
        "gpa_cancel_event",
        {"consent": True, 
            "group_id": group["id"],
            "event_id": event_id,
            "actor": f"user:{owner['user_id']}",
            "role": "organiser",
            "reason": "Hall flooded",
        },
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_reason"] == "Hall flooded"
    still_there = _call(
        server,
        "gpa_get_event",
        {"group_id": group["id"], "event_id": event_id, "role": "member"},
    )
    assert still_there["status"] == "cancelled"
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_cancel_event",
            {"consent": True, 
                "group_id": group["id"],
                "event_id": event_id,
                "actor": f"user:{owner['user_id']}",
                "role": "organiser",
            },
        )
    assert "conflict" in str(caught.value)
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_set_rsvp",
            {
                "group_id": group["id"],
                "event_id": event_id,
                "principal_label": f"user:{owner['user_id']}",
                "status": "coming",
                "user_id": owner["user_id"],
            },
        )
    assert "conflict" in str(caught.value)


# --- the agenda ----------------------------------------------------------------------


def test_agenda_returns_the_window_occurrences(client, app, settings):
    """The published event appears inside its window and not
    outside it — the calendar service's expansion, served
    through the bridge."""
    owner, group, server, event_id = _world(client, app, settings)
    _publish(server, group, event_id, owner)
    page = _call(
        server,
        "gpa_get_agenda",
        {
            "group_id": group["id"],
            "window_start": "2026-11-01T00:00:00-05:00",
            "window_end": "2026-11-30T23:59:59-05:00",
        },
    )
    assert any(item["event_id"] == event_id for item in page["items"])
    empty = _call(
        server,
        "gpa_get_agenda",
        {
            "group_id": group["id"],
            "window_start": "2026-12-01T00:00:00-05:00",
            "window_end": "2026-12-31T23:59:59-05:00",
        },
    )
    assert empty["items"] == []


# --- scope is re-checked at call time for this family (B-205) ------------------------


def test_family_scopes_are_checked_at_call_time(client, app, settings):
    """A read-only principal reads but cannot answer or
    cancel; a read+draft principal (a seat's default key)
    CAN answer — participation is draft-grade — and still
    cannot cancel: cancelling is publish-grade."""
    owner, group, server, event_id = _world(client, app, settings)
    _publish(server, group, event_id, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    reader = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"read"})
        ),
    )
    assert (
        _call(reader, "gpa_list_events", {"group_id": group["id"], "role": "member"})[
            "count"
        ]
        == 1
    )
    with pytest.raises(Exception) as caught:
        _call(
            reader,
            "gpa_set_rsvp",
            {
                "group_id": group["id"],
                "event_id": event_id,
                "principal_label": "seat:s1",
                "status": "coming",
                "seat_id": "s1",
            },
        )
    assert "insufficient_scope" in str(caught.value)
    drafter = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"read", "draft"})
        ),
    )
    answered = _call(
        drafter,
        "gpa_set_rsvp",
        {
            "group_id": group["id"],
            "event_id": event_id,
            "principal_label": "seat:s1",
            "status": "maybe",
            "seat_id": "s1",
        },
    )
    assert answered["status"] == "maybe"
    with pytest.raises(Exception) as caught:
        _call(
            drafter,
            "gpa_cancel_event",
            {"consent": True, 
                "group_id": group["id"],
                "event_id": event_id,
                "actor": "seat:s1",
                "role": "organiser",
            },
        )
    assert "insufficient_scope" in str(caught.value)
