"""Phase 5 gate (roadmap B-074).

Flow A — the demo's spine — for flyers: photograph → read →
review queue → approve → calendar event, carrying its picture.
It runs end-to-end on a fresh database, offline, TWICE:

- with the fixture driver: the full flow lands — candidate with
  region provenance, approval mints the event with its flyer
  attached by the REAL mint (Phase 4's by-hand stand-in is
  lifted), the agenda shows the event;
- with the mock driver: the flow runs honestly to its boundary
  — the job completes, no candidate is invented, the flyer stays
  on the wall flagged awaiting_description.

The roadmap's third run — the live driver — awaits a real Meta
vision endpoint: none exists in this environment, and a faked
"live run" would be a lie in the test report. Recorded here and
in PROJECT_MEMORY_MAP.md, plainly.
"""

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.vision import build_vision_prompt
from vision_world import (
    flyer_answer,
    poster_bytes,
    request_read,
    run_one_job,
    upload_image,
)

RED = (208, 32, 32)
FIXTURE_ANSWER = flyer_answer(
    title="Harvest Supper", starts_phrase="Nov 6", starts_time="6.30pm",
    place="Village Hall", cost="£6", bring="a dish to share",
    transcript="HARVEST SUPPER — Nov 6, 6.30pm, Village Hall. £6.",
    alt_text="A red poster for a harvest supper at the Village Hall "
             "on Nov 6 at 6.30pm, £6, bring a dish to share.",
)


def test_flow_a_with_the_fixture_driver(client, app, db_session):
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    headers = bearer(owner)
    gid = group["id"]

    # Photograph → stored source + flyer.
    detail = upload_image(client, headers, gid, poster_bytes(RED))
    flyer_id = detail["flyer"]["id"]

    # Record the fixture reading against the STORED bytes — the
    # ingest pipeline normalises the upload, so the recording is
    # keyed by what the reader will actually be handed.
    # (B-141 supersession: responses no longer carry storage
    # keys — the source's row names it, as storage's callers do.)
    from gpa.models.sources import Source

    stored = app.state.storage.get_bytes(
        db_session.get(Source, detail["id"]).storage_key
    )
    fixture = FixtureDriver()
    fixture.add_image(
        build_vision_prompt(), stored, FIXTURE_ANSWER,
        schema=promptlib.load_prompt("flyer_reading").schema,
    )
    ai_client.set_client(fixture)

    # Read → review queue.
    job_id = request_read(client, headers, gid, detail["id"])
    assert job_id
    assert run_one_job(app, db_session) is True
    queue = client.get(f"/api/v1/groups/{gid}/candidates", headers=headers).json()
    (candidate,) = queue
    assert candidate["title"] == "Harvest Supper"
    detail_view = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}", headers=headers
    ).json()
    regions = {row["field"]: row["region"] for row in detail_view["provenance"]}
    assert regions["title"] is not None  # region provenance, B-063

    # Approve → the event is minted WITH its flyer (B-049+B-063:
    # no hand-set stand-in anywhere in this flow).
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    event_id = approved.json()["resulting_entity_id"]
    event = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=headers
    ).json()
    assert event["status"] == "published"
    assert event["flyer"]["flyer_id"] == flyer_id
    assert event["flyer"]["alt_text_status"] == "complete"

    # Calendar: the agenda covering the event shows it.
    agenda = client.get(
        f"/api/v1/groups/{gid}/agenda"
        "?from=2026-11-01T00:00:00Z&to=2026-12-01T00:00:00Z",
        headers=headers,
    ).json()
    assert any(
        row["event_id"] == event_id for row in agenda["items"]
    ), agenda


def test_flow_a_with_the_mock_driver_is_honest(client, app, db_session):
    """The mock driver cannot read — so the flow's honest end is:
    job done, no invented candidate, flyer flagged on the wall."""
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    headers = bearer(owner)
    gid = group["id"]

    detail = upload_image(client, headers, gid, poster_bytes(RED))
    request_read(client, headers, gid, detail["id"])
    assert run_one_job(app, db_session) is True

    queue = client.get(f"/api/v1/groups/{gid}/candidates", headers=headers).json()
    assert queue == []  # nothing invented
    source = client.get(
        f"/api/v1/groups/{gid}/sources/{detail['id']}", headers=headers
    ).json()
    assert source["stage"] == "done"  # the job did its work
    wall = client.get(f"/api/v1/groups/{gid}/flyers", headers=headers).json()
    (entry,) = wall["entries"]
    assert entry["alt_text_status"] == "awaiting_description"
    assert entry["review_status"] == "pending_review"  # visible, flagged
