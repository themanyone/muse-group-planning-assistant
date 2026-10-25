"""Demo script → tests (roadmap B-148).

Every click in the submission video script is first an
integration test. The four proposal flows:

- Flow A (flyer → approve → calendar) — tests/test_phase5_gate.py
- Flow B (40-message chat → plan) — tests/test_phase7_gate.py
- Flow C (newsletter → twelve drafts → calendar) — HERE
- Flow D (Saturday night — what's on tonight) — HERE

If it cannot be tested, it is not in the video. Flows A and B
landed with their phase gates; this file carries C and D so
all four demo beats have a test a stranger can run.
"""

from pathlib import Path

from conftest import bearer, make_group, register
from extraction_world import GROUP_TZ, record_answers
from gpa.ai import client as ai_client
from gpa.ai.drivers_fixture import FixtureDriver
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


def _extract_newsletter(client, app, db_session, owner, gid):
    """Upload the golden newsletter and run event extraction."""
    uploaded = client.post(
        f"/api/v1/groups/{gid}/sources/newsletter",
        params={"filename": "newsletter.html"},
        content=GOLDEN_NEWSLETTER.read_bytes(),
        headers={**bearer(owner), "Content-Type": "text/html"},
    )
    assert uploaded.status_code == 201, uploaded.text
    source_id = uploaded.json()["id"]
    from gpa.models.sources import Source

    source = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source)
    answer = {"items": [
        {"fields": {"title": {"value": t, "confidence": 0.9, "quote": t}}}
        for t in EVENT_TITLES
    ]}
    driver = FixtureDriver()
    record_answers(driver, chunks, {"event": ("extraction", answer)})
    ai_client.set_client(driver)
    response = client.post(
        f"/api/v1/groups/{gid}/sources/{source_id}/extract",
        json={"kinds": ["event"]}, headers=bearer(owner),
    )
    assert response.status_code == 202, response.text
    assert job_service.process_one(db_session, app.state.settings, "demo") is True
    return source_id


def _open_candidates(client, owner, gid):
    rows = client.get(
        f"/api/v1/groups/{gid}/candidates", params={"kind": "event"},
        headers=bearer(owner),
    ).json()
    return [r for r in rows if r["status"] in ("pending", "edited")]


def test_flow_c_newsletter_twelve_drafts_then_calendar(client, app, db_session):
    """Flow C: a monthly newsletter arrives with twelve events
    buried in prose; twelve drafts appear; approving one (after
    a reviewer's date edit — the queue's ordinary act) puts it
    on the calendar; re-running extraction never doubles the
    queue (undecided drafts are superseded, not duplicated)."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]

    source_id = _extract_newsletter(client, app, db_session, owner, gid)
    rows = _open_candidates(client, owner, gid)
    assert len(rows) == 12
    assert {r["title"] for r in rows} == set(EVENT_TITLES)

    # Re-extract the SAME source: still twelve open drafts —
    # the re-run supersedes its own undecided drafts (B-045),
    # it never doubles the queue.
    from gpa.models.sources import Source

    source = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source)
    answer = {"items": [
        {"fields": {"title": {"value": t, "confidence": 0.9, "quote": t}}}
        for t in EVENT_TITLES
    ]}
    driver = FixtureDriver()
    record_answers(driver, chunks, {"event": ("extraction", answer)})
    ai_client.set_client(driver)
    again = client.post(
        f"/api/v1/groups/{gid}/sources/{source_id}/extract",
        json={"kinds": ["event"]}, headers=bearer(owner),
    )
    assert again.status_code == 202, again.text
    assert job_service.process_one(db_session, app.state.settings, "demo") is True
    rows = _open_candidates(client, owner, gid)
    assert len(rows) == 12

    # A reviewer gives the first draft its date and approves it.
    first = next(r for r in rows if r["title"] == EVENT_TITLES[0])
    draft = dict(first["draft"])
    draft["starts_at"] = "2026-11-06T18:30:00"
    edited = client.patch(
        f"/api/v1/groups/{gid}/candidates/{first['id']}",
        json={"draft": draft}, headers=bearer(owner),
    )
    assert edited.status_code == 200, edited.text
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{first['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["resulting_entity_id"]

    agenda = client.get(
        f"/api/v1/groups/{gid}/agenda?from=2026-11-06T00:00:00Z&to=2026-11-08T00:00:00Z",
        headers=bearer(owner),
    ).json()
    assert EVENT_TITLES[0] in {row["title"] for row in agenda["items"]}


def test_flow_d_saturday_night_tonight_view(client):
    """Flow D: a member opens the app on Saturday night and sees
    what is on tonight — pinned places first, tomorrow's event
    nowhere in sight."""
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    gid = group["id"]
    headers = bearer(owner)

    def create(**overrides):
        body = {
            "title": "Event", "starts_at": "2026-11-07T19:00:00-05:00",
            "ends_at": "2026-11-07T21:00:00-05:00", **overrides,
        }
        r = client.post(f"/api/v1/groups/{gid}/events", json=body, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()

    create(title="Unpinned Social", starts_at="2026-11-07T18:00:00-05:00",
           ends_at="2026-11-07T19:30:00-05:00")
    create(title="Pinned Gig", lat=42.0, lng=-76.0)
    create(title="Sunday Brunch", starts_at="2026-11-08T11:00:00-05:00",
           ends_at="2026-11-08T12:30:00-05:00")

    rows = client.get(f"/api/v1/groups/{gid}/tonight?date=2026-11-07", headers=headers)
    assert rows.status_code == 200, rows.text
    titles = [r["title"] for r in rows.json()]
    assert titles[0] == "Pinned Gig"  # pinned places lead
    assert "Unpinned Social" in titles
    assert "Sunday Brunch" not in titles
