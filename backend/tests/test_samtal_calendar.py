"""Samtal calendar return path (roadmap B-124).

The round-trip, run against the recorded-fixture double —
the only Samtal this suite ever speaks to:

  published event → sync → it stands on the hall's calendar
  → a Samtal-side edit → sync → the event HERE has not moved;
  a change candidate waits in the queue → a person approves
  → the event moves, and the next sync carries the approved
  words back out.

The approval law runs in both directions. Rejection leaves
the event standing; a cancellation on the Samtal side is a
candidate too, never a silent write; drafts never travel.
"""

from datetime import datetime

import pytest

from conftest import bearer, make_group, register
from gpa.models.events import Event
from gpa.services import samtal as samtal_service

HALL_ID = "hall-longhouse"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    samtal_service.set_client(None)


def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    double = samtal_service.FixtureSamtalClient(
        halls={HALL_ID: []}, calendars={HALL_ID: []}
    )
    samtal_service.set_client(double)
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
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Autumn Social",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "place_name": "Greenfield Farm",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return owner, group, live.json(), created.json(), double


def calendar_sync(client, owner, group, source):
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/{source['id']}/calendar-sync",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return response.json()


def pending_changes(client, owner, group):
    response = client.get(
        f"/api/v1/groups/{group['id']}/candidates", headers=bearer(owner)
    )
    assert response.status_code == 200, response.text
    return [
        row for row in response.json()
        if row["kind"] == "event_change" and row["status"] == "pending"
    ]


def test_round_trip_event_out_edit_back_in(client, db_session):
    owner, group, source, event, double = world(client)

    # OUT: the published event appears on the hall's calendar.
    first = calendar_sync(client, owner, group, source)
    assert first == {"source_id": source["id"], "pushed": 1,
                     "candidates_created": 0}
    calendar = double.list_calendar_events(HALL_ID)
    assert len(calendar) == 1
    assert calendar[0]["title"] == "Autumn Social"
    assert calendar[0]["starts_at"] == "2026-11-06T23:30:00+00:00"
    external_id = calendar[0]["id"]

    # A second sync pushes nothing — the copy is current.
    assert calendar_sync(client, owner, group, source)["pushed"] == 0

    # IN: a Samtal-side edit (the whole event moved an hour
    # later — both instants — and the place changed).
    double.edit_calendar_event(
        HALL_ID, external_id,
        starts_at="2026-11-07T00:30:00+00:00",
        ends_at="2026-11-07T03:00:00+00:00",
        place="Village Hall",
    )
    arrived = calendar_sync(client, owner, group, source)
    assert arrived["candidates_created"] == 1
    assert arrived["pushed"] == 0

    # The event HERE has not moved. The queue holds the word.
    row = db_session.get(Event, event["id"])
    assert row.starts_at == datetime(2026, 11, 6, 23, 30)
    assert row.place_name == "Greenfield Farm"
    changes = pending_changes(client, owner, group)
    assert len(changes) == 1
    assert changes[0]["created_by"] == "system:samtal-calendar"

    # Re-running the sync does not draft the same proposal twice.
    assert calendar_sync(client, owner, group, source)["candidates_created"] == 0

    # A person approves → the change lands through B-077's door.
    approved = client.post(
        f"/api/v1/groups/{group['id']}/candidates/{changes[0]['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    db_session.expire_all()
    row = db_session.get(Event, event["id"])
    assert row.starts_at == datetime(2026, 11, 7, 0, 30)
    assert row.ends_at == datetime(2026, 11, 7, 3, 0)
    assert row.place_name == "Village Hall"

    # And the approved words travel back out on the next sync.
    back = calendar_sync(client, owner, group, source)
    assert back["pushed"] == 1
    assert back["candidates_created"] == 0
    calendar = double.list_calendar_events(HALL_ID)
    assert calendar[0]["place"] == "Village Hall"


def test_rejected_edit_leaves_the_event_standing(client, db_session):
    owner, group, source, event, double = world(client)
    calendar_sync(client, owner, group, source)
    external_id = double.list_calendar_events(HALL_ID)[0]["id"]
    double.edit_calendar_event(
        HALL_ID, external_id, place="Somewhere Else"
    )
    calendar_sync(client, owner, group, source)
    changes = pending_changes(client, owner, group)
    assert len(changes) == 1
    rejected = client.post(
        f"/api/v1/groups/{group['id']}/candidates/{changes[0]['id']}/reject",
        headers=bearer(owner),
    )
    assert rejected.status_code == 200, rejected.text
    row = db_session.get(Event, event["id"])
    assert row.place_name == "Greenfield Farm"


def test_samtal_side_cancellation_is_a_candidate_not_a_write(client, db_session):
    owner, group, source, event, double = world(client)
    calendar_sync(client, owner, group, source)
    external_id = double.list_calendar_events(HALL_ID)[0]["id"]
    double.edit_calendar_event(HALL_ID, external_id, cancelled=True)
    arrived = calendar_sync(client, owner, group, source)
    assert arrived["candidates_created"] == 1
    row = db_session.get(Event, event["id"])
    assert row.status == "published"  # not silently cancelled

    changes = pending_changes(client, owner, group)
    approved = client.post(
        f"/api/v1/groups/{group['id']}/candidates/{changes[0]['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    db_session.expire_all()
    assert db_session.get(Event, event["id"]).status == "cancelled"


def test_drafts_never_travel_and_foreign_events_are_not_imported(client):
    owner, group, source, event, double = world(client)
    draft = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Unannounced plan",
            "starts_at": "2026-12-01T19:00:00-05:00",
            "ends_at": "2026-12-01T21:00:00-05:00",
            "status": "draft",
        },
        headers=bearer(owner),
    )
    assert draft.status_code == 201, draft.text
    # A Samtal-born event with no ref to any of ours stands
    # in the hall; the sync must not import it (importing
    # would mint an event no person here approved).
    double.upsert_calendar_event(
        HALL_ID,
        {
            "title": "Hall-only social",
            "starts_at": "2026-11-20T19:00:00+00:00",
            "ends_at": "2026-11-20T21:00:00+00:00",
            "place": "The hall",
        },
    )
    result = calendar_sync(client, owner, group, source)
    assert result["pushed"] == 1  # only the published event
    assert result["candidates_created"] == 0
    titles = [
        e["title"] for e in double.list_calendar_events(HALL_ID)
    ]
    assert "Unannounced plan" not in titles
