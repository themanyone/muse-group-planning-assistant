"""Phase 19 gate (roadmap B-220): the parity proof.

One scripted story, twice — plan a club night:

* a HUMAN does it through the API: flyer in, vision read,
  draft in the review queue, approval, published event,
  RSVP, discussion post, digest mention;
* an AI AGENT then does the equivalent story through MCP
  ONLY, as an organiser SEAT of the same approver role the
  human exercised: source in, extraction requested (the
  worker drain between request and queue is infrastructure,
  exactly as a vision job's drain is in the human story —
  no story act travels any other surface), candidate,
  approval by the seat under the one approval law, published
  event, RSVP as the seat, digest preview through the digest
  tool.

Both runs end in the same state — a published club night at
the hall, one coming answer on it, its name in a digest —
and both leave a complete audit trail: the service audit
rows name the actor who acted (the person in one run, the
seat in the other), and the MCP run additionally leaves its
hash-chained ``internal.*`` lines for every operation it
travelled.

Two steps of the human story have NO MCP equivalent today,
and this gate pins that honestly instead of papering over
it (the B-219 Inspector discipline): there is no MCP tool
for image/flyer ingest (the agent's source arrives as a
chat source — the MCP ingest form that exists), and no MCP
tool for discussion posting. ``test_mcp_surface_gaps_are_named``
asserts the catalogue still carries neither, by name pattern,
so the day either tool lands this gate must be revisited
and the MCP story extended to cover it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta

from conftest import bearer, make_group, register, unique_email
from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.ai.vision import build_vision_prompt
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.catalogue import tool_catalogue
from gpa.mcp.server import build_server
from gpa.models.core import AuditLog
from gpa.models.groups import Group
from gpa.models.sources import Source
from gpa.services import digests as digest_service
from gpa.services import jobs as job_service
from gpa.services import sources as source_service
from test_groups import invite_and_accept
from vision_world import flyer_answer, poster_bytes, request_read, run_one_job, upload_image

NY = "America/New_York"
RED = (208, 32, 32)


def _club_night_date() -> tuple[date, str]:
    """The next Saturday (today if today is one), and its
    plain date phrase — "Oct 31" — as a flyer or a chat line
    would carry it. Computed, never hard-coded, so the gate
    runs on any day: a Saturday always sits inside the digest
    windows this gate checks (its own week, or the week ahead
    the digest also spans)."""
    today = date.today()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    return saturday, f"{saturday:%b} {saturday.day}"


def _call(server, name, args):
    """Call one MCP tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _audit_actions(db_session, group_id):
    """Every audit action recorded for one group."""
    return {
        row.action
        for row in db_session.query(AuditLog)
        .filter(AuditLog.group_id == group_id)
        .all()
    }


# --- the human story, through the API -------------------------------------------


def test_human_story_via_api(client, app, db_session):
    """Flyer in → read → queue → approval → published event →
    RSVP → discussion post → digest mention, all at the API
    doors, with the service audit naming the human actors."""
    saturday, phrase = _club_night_date()
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    ana = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    headers = bearer(owner)
    gid = group["id"]

    # Flyer in, and its fixture reading recorded against the
    # stored bytes (the Phase 5 gate's discipline).
    detail = upload_image(client, headers, gid, poster_bytes(RED))
    flyer_id = detail["flyer"]["id"]
    stored = app.state.storage.get_bytes(
        db_session.get(Source, detail["id"]).storage_key
    )
    fixture = FixtureDriver()
    fixture.add_image(
        build_vision_prompt(),
        stored,
        flyer_answer(
            title="Club Night", starts_phrase=phrase, starts_time="7pm",
            place="Village Hall", bring="a dish to share",
            transcript=f"CLUB NIGHT — {phrase}, 7pm, Village Hall.",
            alt_text="A red poster for a club night at the Village Hall "
                     f"on {phrase} at 7pm, bring a dish to share.",
        ),
        schema=promptlib.load_prompt("flyer_reading").schema,
    )
    ai_client.set_client(fixture)

    # Read → draft in the queue.
    request_read(client, headers, gid, detail["id"])
    assert run_one_job(app, db_session) is True
    queue = client.get(f"/api/v1/groups/{gid}/candidates", headers=headers).json()
    (candidate,) = queue
    assert candidate["title"] == "Club Night"

    # Approval → the published event, flyer attached by the mint.
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == f"user:{owner['user_id']}"
    event_id = approved.json()["resulting_entity_id"]
    event = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=headers
    ).json()
    assert event["status"] == "published"
    assert event["title"] == "Club Night"
    assert event["place_name"] == "Village Hall"
    assert event["flyer"]["flyer_id"] == flyer_id

    # RSVP: Ana is coming.
    rsvp = client.put(
        f"/api/v1/groups/{gid}/events/{event_id}/rsvp",
        json={"status": "coming"}, headers=bearer(ana),
    )
    assert rsvp.status_code == 200, rsvp.text

    # Discussion: Ana posts on the event's thread.
    posted = client.post(
        f"/api/v1/groups/{gid}/events/{event_id}/comments",
        json={"body": "See you there — bringing the projector."},
        headers=bearer(ana),
    )
    assert posted.status_code == 201, posted.text
    thread = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}/comments", headers=headers
    ).json()
    assert [row["body"] for row in thread] == [
        "See you there — bringing the projector."
    ]

    # Digest mention: the digest for the event's own week
    # names it (the service seam's injected moment is the
    # Phase 8 gate's fake-clock pattern).
    starts = datetime.fromisoformat(event["starts_at"].replace("Z", "+00:00"))
    monday = starts.date() - timedelta(days=starts.date().weekday())
    digest = digest_service.run_digest(
        db_session, actor_label=f"user:{owner['user_id']}",
        group=db_session.get(Group, gid),
        now=datetime.combine(monday, time(12, 0)),
    )
    assert "Club Night" in digest.body_text

    # The audit trail for the human run: approval and mint
    # both stand, naming the person's acts in the stream.
    actions = _audit_actions(db_session, gid)
    assert {"candidate.approved", "event.minted", "digest.sent"} <= actions


# --- the agent story, through MCP only ------------------------------------------


def _field(value, confidence, quote, **extra):
    """One extraction field, in the pipeline's answer shape."""
    return {"value": value, "confidence": confidence, "quote": quote, **extra}


def test_agent_story_via_mcp_only(client, app, db_session, settings):
    """The equivalent story with every story act travelling
    an MCP tool, as an organiser seat (read+draft+publish —
    the seat's own credential, scope-checked at call time):
    source in → extraction requested → candidate → approval
    by the seat → published event → RSVP as the seat →
    digest preview. Setup (group, seat) is administration,
    done at the API before the story starts, as a seat's
    creation always is."""
    saturday, phrase = _club_night_date()
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    gid = group["id"]
    made_seat = client.post(
        f"/api/v1/groups/{gid}/seats",
        json={
            "name": "Gate Seat", "role": "organiser",
            "scopes": ["read", "draft", "publish"],
        },
        headers=bearer(owner),
    )
    assert made_seat.status_code == 201, made_seat.text
    seat = made_seat.json()
    seat_label = f"seat:{seat['id']}"

    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id=seat["id"], group_id=gid, role="organiser",
            scopes=frozenset({"read", "draft", "publish"}),
        ),
    )

    # Source in: the club-night chat, filed through the
    # sources tool with its permission record.
    chat_line = f"Club night is on! {phrase} at the hall, doors 7pm"
    filed = _call(
        server,
        "gpa_create_source",
        {
            "group_id": gid,
            "actor": seat_label,
            "kind": "chat_paste",
            "title": "Planning chat",
            "granted_scope": "whole chat, Ana consented",
            "chunks": [
                {"text": chat_line, "sender": "Ana"},
                {"text": "I can bring the projector", "sender": "Ben"},
            ],
        },
    )
    assert filed["granted_by"] == seat_label
    source_id = filed["source_id"]

    # The fixture driver is recorded against the chunks as
    # stored, under the exact prompt the pipeline will send.
    source_row = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source_row)
    answer = {
        "items": [
            {
                "fields": {
                    "title": _field("Club Night", 0.95, "Club night is on!"),
                    "starts": _field(
                        f"{phrase} 7pm", 0.9, phrase,
                        date_phrase=phrase, time_phrase="7pm",
                    ),
                    "place": _field("the hall", 0.85, "at the hall"),
                    "host": _field(None, 0.0, ""),
                }
            }
        ]
    }
    driver = FixtureDriver()
    driver.add_json(
        build_prompt("extraction", chunks, NY),
        promptlib.load_prompt("extraction").schema,
        answer,
    )
    ai_client.set_client(driver)

    # Extraction requested through the tool (organiser role —
    # the operation's own law), then the worker drain: the
    # request queued a job, and running jobs is infrastructure
    # in both stories, never a story surface.
    queued = _call(
        server,
        "gpa_request_extraction",
        {
            "group_id": gid, "source_id": source_id,
            "actor": seat_label, "role": "organiser", "kinds": ["event"],
        },
    )
    assert queued["job_id"]
    assert job_service.process_one(db_session, app.state.settings, "gate-worker") is True

    # The draft stands in the queue, read back through MCP.
    queue = _call(
        server, "gpa_list_candidates", {"group_id": gid, "kind": "event"}
    )
    (candidate,) = queue["candidates"]
    assert candidate["title"] == "Club Night"
    assert candidate["status"] == "pending"

    # Approval by the seat, under the one approval law: it
    # decides and mints, and the approver of record is the
    # seat itself — never its creator.
    approved = _call(
        server,
        "gpa_approve_candidate",
        {
            "consent": True,
            "group_id": gid,
            "candidate_id": candidate["candidate_id"],
            "actor": seat_label,
            "role": "organiser",
            "seat_id": seat["id"],
        },
    )
    assert approved["status"] == "approved"
    assert approved["approved_by"] == seat_label
    event_id = approved["resulting_entity_id"]
    assert approved["resulting_entity_type"] == "event"

    # The same end state, read through MCP: a published club
    # night at the hall on the Saturday the chat named.
    event = _call(
        server,
        "gpa_get_event",
        {"group_id": gid, "event_id": event_id, "role": "member"},
    )
    assert event["status"] == "published"
    assert event["title"] == "Club Night"
    assert event["place_name"] == "the hall"

    # RSVP as the seat: the seat answers for itself.
    answer_rsvp = _call(
        server,
        "gpa_set_rsvp",
        {
            "group_id": gid, "event_id": event_id,
            "principal_label": seat_label, "status": "coming",
            "seat_id": seat["id"],
        },
    )
    assert answer_rsvp["status"] == "coming"
    listing = _call(
        server, "gpa_list_rsvps", {"group_id": gid, "event_id": event_id}
    )
    assert [row["principal_label"] for row in listing["rsvps"]] == [seat_label]

    # Digest mention: the preview tool composes this week's
    # digest (unsent, by its contract) and the club night —
    # this week's Saturday — is named in its text.
    preview = _call(
        server, "gpa_preview_digest", {"group_id": gid, "role": "organiser"}
    )
    assert preview["sent_at"] is None
    assert "Club Night" in preview["body_text"]

    # The audit trail for the agent run, both layers: the
    # service rows name the seat's own acts (approval, mint),
    # and every operation the story travelled left its
    # hash-chained internal line under the MCP caller's name.
    actions = _audit_actions(db_session, gid)
    assert {"candidate.approved", "event.minted"} <= actions
    seat_rows = [
        row for row in db_session.query(AuditLog)
        .filter(AuditLog.group_id == gid, AuditLog.actor == seat_label)
        .all()
    ]
    assert any(row.action == "candidate.approved" for row in seat_rows)
    internal_ops = set()
    for row in db_session.query(AuditLog).filter(
        AuditLog.group_id == gid, AuditLog.action.like("internal.%")
    ):
        internal_ops.add(json.loads(row.detail_json)["operation"])
    assert {
        "sources.create", "extraction.request", "candidates.list",
        "candidates.approve", "events.get", "rsvps.set",
        "rsvps.list", "digests.preview",
    } <= internal_ops


# --- the gaps, pinned by name ------------------------------------------------------


def test_mcp_surface_gaps_are_named():
    """The two human-story steps with no MCP equivalent are
    pinned, not hidden: the served catalogue carries no
    discussion/comment tool and no flyer/image-ingest tool.
    When either lands, this test fails — deliberately — and
    the MCP story above must grow to cover the new step."""
    names = [entry["name"] for entry in tool_catalogue()]
    assert not any("comment" in name or "discuss" in name for name in names), names
    assert not any("flyer" in name or "image" in name for name in names), names
