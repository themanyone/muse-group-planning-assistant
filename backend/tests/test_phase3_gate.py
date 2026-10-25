"""The Phase 3 gate (roadmap B-048).

Golden material in at one end — the Doc 07 planning chat and
the Phase 2 twelve-event newsletter — reviewed candidates out
at the other: every field carrying its quote, its confidence,
and its server-computed flags; the event approved in the queue;
the newsletter's twelve events arriving as twelve candidates,
not eleven, not thirteen. Plus the phase's standing promises:
the whole run happens offline (the fixture driver is the only
model in the room), and the mock driver passes the same gate a
person would run by hand (B-036's suite-level acceptance is in
test_ai_client.py; here the demo skeleton runs on it).
"""

from pathlib import Path

import pytest

from conftest import bearer, make_group, register
from extraction_world import (
    GROUP_TZ,
    build_world,
    candidates_by_kind,
    record_answers,
    run_extraction_job,
)
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.services import jobs as job_service
from gpa.services import sources as source_service

GOLDEN_NEWSLETTER = Path(__file__).parent / "golden" / "newsletter.html"

EVENT_TITLES = [
    "Event One — Harvest Supper",
    "Event Two — Projector Night",
    "Event Three — Garden Tidy",
    "Event Four — Book Club",
    "Event Five — Choir Practice",
    "Event Six — Repair Café",
    "Event Seven — Lantern Walk",
    "Event Eight — Baking Morning",
    "Event Nine — Story Night",
    "Event Ten — River Clean-Up",
    "Event Eleven — Games Evening",
    "Event Twelve — Winter Planning",
]


def test_gate_chat_to_approved_candidate(client, app, db_session):
    """Chat -> candidates with quotes, confidences and flags ->
    the event approved in the queue. The full Phase 3 sentence."""
    world = build_world(client, app, db_session)
    run_extraction_job(client, app, db_session, world)
    rows = candidates_by_kind(client, world)
    assert set(rows) == {"event", "task", "decision", "question"}

    event = rows["event"]
    assert event["draft"]["fields"]["starts"]["quote"] == "this Saturday"
    assert event["draft"]["starts_date"] == "2026-10-24"
    assert event["confidence"] == pytest.approx(0.9)
    assert isinstance(event["flags"], list)

    gid = world["group"]["id"]
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{event['id']}",
        headers=bearer(world["owner"]),
    ).json()
    supported = {row["field"] for row in detail["provenance"] if row["supported"]}
    assert {"title", "starts", "place"} <= supported

    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{event['id']}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == f"user:{world['owner']['user_id']}"


def test_gate_newsletter_yields_twelve_candidates(client, app, db_session):
    """The twelve-event newsletter yields twelve candidates —
    each titled from its own section, each quote verified against
    its own chunk (B-041 through the whole stack)."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    uploaded = client.post(
        f"/api/v1/groups/{group['id']}/sources/newsletter",
        params={"filename": "newsletter.html"},
        content=GOLDEN_NEWSLETTER.read_bytes(),
        headers={**bearer(owner), "Content-Type": "text/html"},
    )
    assert uploaded.status_code == 201, uploaded.text
    source_id = uploaded.json()["id"]

    from gpa.models.sources import Source

    source = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source)
    assert len(chunks) == 13  # preamble + twelve sections

    answer = {"items": [
        {"fields": {"title": {
            "value": title, "confidence": 0.9, "quote": title,
        }}}
        for title in EVENT_TITLES
    ]}
    driver = FixtureDriver()
    record_answers(driver, chunks, {"event": ("extraction", answer)})
    from gpa.ai import client as ai_client

    ai_client.set_client(driver)

    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/{source_id}/extract",
        json={"kinds": ["event"]}, headers=bearer(owner),
    )
    assert response.status_code == 202, response.text
    assert job_service.process_one(db_session, app.state.settings, "gate") is True

    rows = client.get(
        f"/api/v1/groups/{group['id']}/candidates",
        params={"kind": "event"}, headers=bearer(owner),
    ).json()
    assert len(rows) == 12
    assert {row["title"] for row in rows} == set(EVENT_TITLES)
    # Each title-only draft honestly earns the one flag it should:
    # an event with no start drafted is missing a required field
    # (B-044 — the API says so, the queue shows it).
    assert all(row["flags"] == ["missing_required:starts"] for row in rows)
    assert all(
        row["draft"]["fields"]["title"]["supported"] is True for row in rows
    )
