"""Phase 11 gate (roadmap B-130): the bridge, walked end to end.

One scripted story, every span of Phase 11 in it:

  a Samtal hall (the recorded-fixture double — the real
  village is never touched by this suite) is synced into a
  live source → the fixture extraction engine drafts from
  the hall's own words → a person approves → the event is
  minted → the calendar return path carries it back onto
  the hall's calendar.

Around the story, the phase's other doors stand in their
proven states: the Messenger source refuses sync in plain
words (access never granted; the fallback is paste), the
webhook doors are signed both ways, and identity resolved
by link alone — the hall's Ana is a guest until a person
links her.
"""

import pytest

from conftest import bearer, make_group, register
from extraction_world import GROUP_TZ, record_answers
from gpa.ai import client as ai_client
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.services import jobs as job_service
from gpa.services import samtal as samtal_service
from gpa.services import sources as source_service

HALL_ID = "hall-longhouse"

# The Doc 07 golden chat, spoken in the hall this time —
# the same five lines extraction_world pastes, so the
# recorded fixture answers key identically.
HALL_MESSAGES = [
    {"id": "m1", "sender_id": "samtal-ana", "sender_name": "Ana",
     "text": "Club night is on! this Saturday at the hall, doors 7pm",
     "sent_at": "2026-10-21T23:02:00+00:00"},
    {"id": "m2", "sender_id": "samtal-ben", "sender_name": "Ben",
     "text": "I can bring the projector",
     "sent_at": "2026-10-21T23:03:00+00:00"},
    {"id": "m3", "sender_id": "samtal-cleo", "sender_name": "Cleo",
     "text": "I'll sort the playlist again",
     "sent_at": "2026-10-21T23:04:00+00:00"},
    {"id": "m4", "sender_id": "samtal-ana", "sender_name": "Ana",
     "text": "Agreed with the committee: the quiz is postponed to November",
     "sent_at": "2026-10-21T23:05:00+00:00"},
    {"id": "m5", "sender_id": "samtal-ben", "sender_name": "Ben",
     "text": "Quick question — who is doing the door rota this time?",
     "sent_at": "2026-10-21T23:06:00+00:00"},
]


@pytest.fixture(autouse=True)
def _reset_clients():
    yield
    samtal_service.set_client(None)
    ai_client.set_client(None)


def test_phase11_gate_hall_to_candidate_to_event_to_calendar(client, app, db_session):
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    double = samtal_service.FixtureSamtalClient(
        halls={HALL_ID: HALL_MESSAGES}, calendars={HALL_ID: []}
    )
    samtal_service.set_client(double)

    # 1. The hall is registered (B-032's permission record)
    #    and synced (B-123): five chunks, speakers as guests.
    live = client.post(
        f"/api/v1/groups/{group['id']}/sources/live",
        json={
            "connector_kind": "samtal",
            "external_id": HALL_ID,
            "granted_scope": "group-history",
            "title": "The longhouse hall",
        },
        headers=bearer(owner),
    )
    assert live.status_code == 201, live.text
    source_id = live.json()["id"]
    synced = client.post(
        f"/api/v1/groups/{group['id']}/sources/{source_id}/sync",
        headers=bearer(owner),
    )
    assert synced.json()["added"] == 5
    speakers = client.get(
        f"/api/v1/groups/{group['id']}/sources/{source_id}/speakers",
        headers=bearer(owner),
    ).json()
    assert speakers and all(row["guest"] for row in speakers)

    # 2. Extraction over the hall's words (fixture driver,
    #    the recorded answers) drafts the queue.
    from gpa.models.groups import Group
    from gpa.models.sources import Source

    group_row = db_session.get(Group, group["id"])
    source_row = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source_row)
    driver = FixtureDriver()
    record_answers(driver, chunks)
    ai_client.set_client(driver)

    requested = client.post(
        f"/api/v1/groups/{group['id']}/sources/{source_id}/extract",
        json={}, headers=bearer(owner),
    )
    assert requested.status_code == 202, requested.text
    assert job_service.process_one(db_session, app.state.settings, "gate") is True

    queue = client.get(
        f"/api/v1/groups/{group['id']}/candidates", headers=bearer(owner)
    ).json()
    event_drafts = [row for row in queue if row["kind"] == "event"]
    assert len(event_drafts) == 1
    assert event_drafts[0]["title"] == "Club night"
    assert event_drafts[0]["status"] == "pending"

    # 3. A person approves → the event is minted.
    approved = client.post(
        f"/api/v1/groups/{group['id']}/candidates/{event_drafts[0]['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    event_id = approved.json()["resulting_entity_id"]
    assert event_id
    events = client.get(
        f"/api/v1/groups/{group['id']}/events", headers=bearer(owner)
    ).json()
    minted = [row for row in events if row["id"] == event_id]
    assert len(minted) == 1
    assert minted[0]["title"] == "Club night"
    assert minted[0]["status"] == "published"

    # 4. The return path (B-124): the event now stands on
    #    the hall's own calendar — the story comes home.
    synced_calendar = client.post(
        f"/api/v1/groups/{group['id']}/sources/{source_id}/calendar-sync",
        headers=bearer(owner),
    )
    assert synced_calendar.status_code == 200, synced_calendar.text
    assert synced_calendar.json()["pushed"] == 1
    calendar = double.list_calendar_events(HALL_ID)
    assert len(calendar) == 1
    assert calendar[0]["ref"] == event_id
    assert calendar[0]["title"] == "Club night"
    # Saturday 24 October 2026, 19:00 in New York (EDT, -4).
    assert calendar[0]["starts_at"] == "2026-10-24T23:00:00+00:00"
