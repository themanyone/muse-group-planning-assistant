"""Phase 9 gate (roadmap B-112): the nonprofit story, end to end.

import members → flyer in → event out → shifts claimed →
digest sent — one community hall, one crew, one supper:

1. the organiser imports the paper roster (B-110): three
   people become invited members, two of them enrolled in the
   Kitchen Crew the import itself creates (B-105);
2. a photographed flyer is read by the fixture driver and
   approved (the Phase 5 road), minting the supper (B-063);
3. the event is scoped to the Kitchen Crew — the enrolled
   member sees it, the unenrolled member does not (B-105's
   leak rule, inside the story);
4. a driver shift opens on it, the gap report names the gap
   in plain words, the crew member claims the slot, and the
   gap closes (B-107);
5. attendance is marked and reads back as a count (B-109);
6. the weekly digest goes out naming the supper, and lands
   in the crew member's mailbox (B-099 machinery).

No ZIP is cut at this gate (the phase folds into ZIP-RC at
B-138); the contract artefacts are regenerated instead.
"""

from datetime import datetime

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.vision import build_vision_prompt
from gpa.models.groups import Group
from gpa.services import digests as digest_service
from gpa.services import jobs as job_service
from gpa.services import mailer
from test_groups import invite_and_accept
from vision_world import (
    flyer_answer,
    poster_bytes,
    request_read,
    run_one_job,
    upload_image,
)

NY = "America/New_York"
RED = (208, 32, 32)
FIXTURE_ANSWER = flyer_answer(
    title="Harvest Supper", starts_phrase="Nov 6", starts_time="6.30pm",
    place="Village Hall", cost="£6", bring="a dish to share",
    transcript="HARVEST SUPPER — Nov 6, 6.30pm, Village Hall. £6.",
    alt_text="A red poster for a harvest supper at the Village Hall "
             "on Nov 6 at 6.30pm, £6, bring a dish to share.",
)
ROSTER = (
    "name,email,sub-group\n"
    "Greta Grill,greta@example.org,Kitchen Crew\n"
    "Hank Hob,hank@example.org,Kitchen Crew\n"
    "Ivan Ivy,ivan@example.org,\n"
)


@pytest.fixture(autouse=True)
def capture_transport(app):
    transport = mailer.CaptureTransport()
    mailer.set_transport(transport)
    yield transport
    mailer.set_transport(None)


def drain(app, db_session, limit=50):
    ran = 0
    while ran < limit and job_service.process_one(
        db_session, app.state.settings, "w-gate"
    ):
        ran += 1
    return ran


def test_the_nonprofit_story(client, app, db_session, capture_transport):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    gid = group["id"]
    headers = bearer(owner)
    ana = invite_and_accept(client, owner, gid, unique_email(), "member")
    ben = invite_and_accept(client, owner, gid, unique_email(), "member")

    # 1 — IMPORT: the paper roster lands; the Kitchen Crew is
    # born from the spreadsheet's own words.
    imported = client.post(
        f"/api/v1/groups/{gid}/members/import",
        content=ROSTER.encode(),
        headers={**headers, "Content-Type": "text/plain"},
    )
    assert imported.status_code == 200, imported.text
    report = imported.json()
    assert report["memberships_created"] == 3
    assert report["enrolments"] == 2
    assert report["subgroups_created"] == ["Kitchen Crew"]
    (crew,) = client.get(
        f"/api/v1/groups/{gid}/subgroups", headers=headers
    ).json()

    # Ana joins the crew; Ben does not.
    enrolled = client.post(
        f"/api/v1/groups/{gid}/subgroups/{crew['id']}/members",
        json={"user_id": ana["user_id"]}, headers=headers,
    )
    assert enrolled.status_code == 201, enrolled.text

    # An organiser announcement opens the board for the season.
    posted = client.post(
        f"/api/v1/groups/{gid}/announcements",
        json={"title": "Season opens", "body": "The hall is ours again."},
        headers=headers,
    )
    assert posted.status_code == 201, posted.text

    # 2 — FLYER IN → EVENT OUT: photographed, read, approved,
    # minted — the Phase 5 road, unchanged.
    detail = upload_image(client, headers, gid, poster_bytes(RED))
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
    request_read(client, headers, gid, detail["id"])
    assert run_one_job(app, db_session) is True
    (candidate,) = client.get(
        f"/api/v1/groups/{gid}/candidates", headers=headers
    ).json()
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    event_id = approved.json()["resulting_entity_id"]

    # 3 — SCOPE: the supper belongs to the Kitchen Crew. Ana
    # sees it; Ben's calendar has never heard of it.
    event = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=headers
    ).json()
    scoped = client.patch(
        f"/api/v1/groups/{gid}/events/{event_id}",
        json={"subgroup_id": crew["id"]},
        headers={**headers, "If-Match": f'"{event["etag"]}"'},
    )
    assert scoped.status_code == 200, scoped.text
    ana_events = client.get(
        f"/api/v1/groups/{gid}/events", headers=bearer(ana)
    ).json()
    assert any(row["id"] == event_id for row in ana_events)
    ben_events = client.get(
        f"/api/v1/groups/{gid}/events", headers=bearer(ben)
    ).json()
    assert all(row["id"] != event_id for row in ben_events)
    ben_direct = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=bearer(ben)
    )
    assert ben_direct.status_code == 404

    # 4 — SHIFTS: a driver is needed; the gap report says so in
    # plain words; Ana claims the slot and the gap closes.
    shift = client.post(
        f"/api/v1/groups/{gid}/events/{event_id}/shifts",
        json={
            "role_name": "driver", "headcount": 1,
            "starts_at": "2026-11-06T17:30:00-05:00",
            "ends_at": "2026-11-06T18:30:00-05:00",
        },
        headers=headers,
    )
    assert shift.status_code == 201, shift.text
    gaps = client.get(
        f"/api/v1/groups/{gid}/shift-gaps",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=headers,
    ).json()
    (line,) = [row for row in gaps["events"] if row["event_id"] == event_id]
    assert line["missing"] == [{"role_name": "driver", "count": 1}]
    assert line["summary"] == "Friday still needs a driver."
    slot_id = shift.json()["slots"][0]["id"]
    claimed = client.post(
        f"/api/v1/groups/{gid}/roles/{slot_id}/claim", headers=bearer(ana)
    )
    assert claimed.status_code == 200, claimed.text
    gaps = client.get(
        f"/api/v1/groups/{gid}/shift-gaps",
        params={"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z"},
        headers=headers,
    ).json()
    assert all(row["event_id"] != event_id for row in gaps["events"])

    # 5 — ATTENDANCE: marked by the organiser, read as a count.
    marked = client.post(
        f"/api/v1/groups/{gid}/events/{event_id}/attendance",
        json={"user_id": ana["user_id"]}, headers=headers,
    )
    assert marked.status_code == 201, marked.text
    count = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}/attendance",
        headers=bearer(ana),
    ).json()
    assert count == {"event_id": event_id, "attendee_count": 1}

    # 6 — DIGEST: the Monday of the supper's week, the digest
    # names it and lands in Ana's mailbox.
    group_row = db_session.get(Group, gid)
    digest = digest_service.run_digest(
        db_session, actor_label="user:test", group=group_row,
        now=datetime(2026, 11, 2, 12, 0),
    )
    assert "Harvest Supper" in digest.body_text
    drain(app, db_session)
    ana_mail = [m for m in capture_transport.sent if m["to"] == ana["email"]]
    assert any("your week ahead" in m["subject"] for m in ana_mail)
