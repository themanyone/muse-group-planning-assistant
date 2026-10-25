"""Two-way Google, revisited (roadmap B-159).

B-132 shipped one-way out and said so; B-159 widens the
inbound half by exactly the B-124 notch: a Google-side edit
is PULLED as a change candidate and waits in the review
queue, visibly, until a person approves or rejects it. The
event never moves on a pull. The three-way comparison is
pinned too: our own not-yet-pushed edit drafts nothing (the
stale copy is the outbound sync's business, not a proposal
to revert ourselves).

Everything runs against the recorded-fixture double — no
Google account is connected in this environment.
"""

from datetime import datetime

import pytest

from conftest import bearer, make_group, register
from gpa.models.events import Event
from gpa.services import google_calendar as google_service

CALENDAR_ID = "village-calendar@example.org"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    google_service.set_client(None)


def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    double = google_service.FixtureGoogleClient()
    google_service.set_client(double)
    integration = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={
            "kind": "google_calendar",
            "name": "Village Google calendar",
            "config": {
                "calendar_id": CALENDAR_ID,
                "access_token": "fixture-token-not-real",
            },
        },
        headers=bearer(owner),
    )
    assert integration.status_code == 201, integration.text
    return owner, group, integration.json(), double


def make_event(client, owner, group, title="Autumn Social"):
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": title,
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "place_name": "Greenfield Farm",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return created.json()


def sync_out(client, owner, group, integration):
    response = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration['id']}/google-sync",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return response.json()


def pull(client, owner, group, integration):
    return client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration['id']}/google-pull",
        headers=bearer(owner),
    )


def pending_changes(client, owner, group):
    listing = client.get(
        f"/api/v1/groups/{group['id']}/candidates", headers=bearer(owner)
    )
    assert listing.status_code == 200, listing.text
    return [
        row for row in listing.json()
        if row["kind"] == "event_change" and row["status"] == "pending"
    ]


def test_google_edit_waits_for_a_human_visibly(client, db_session):
    """THE acceptance: an edit on Google drafts a candidate;
    the event does not move; a person's approval moves it."""
    owner, group, integration, double = world(client)
    event = make_event(client, owner, group)
    sync_out(client, owner, group, integration)
    external_id = double.list_events(CALENDAR_ID)[0]["id"]

    # A person edits the copy on Google: two hours later,
    # a different hall.
    double.edit_event(
        CALENDAR_ID, external_id,
        start="2026-11-07T01:30:00+00:00",
        end="2026-11-07T04:00:00+00:00",
        location="Riverside Hall",
    )
    pulled = pull(client, owner, group, integration)
    assert pulled.status_code == 200, pulled.text
    assert pulled.json()["candidates_created"] == 1
    assert pulled.json()["checked"] == 1

    # The event HERE has not moved — the queue holds the word.
    row = db_session.get(Event, event["id"])
    assert row.starts_at == datetime(2026, 11, 6, 23, 30)
    assert row.place_name == "Greenfield Farm"
    changes = pending_changes(client, owner, group)
    assert len(changes) == 1
    assert changes[0]["created_by"] == "system:google-calendar"
    assert changes[0]["draft"]["fields"]["starts"]["value"].startswith(
        "2026-11-06T20:30"
    )  # 01:30 UTC in the event's own zone (EST).
    assert changes[0]["draft"]["fields"]["place"]["value"] == "Riverside Hall"

    # A second pull drafts no duplicate while it waits.
    pulled = pull(client, owner, group, integration)
    assert pulled.json()["candidates_created"] == 0

    # A person approves → the change lands through B-077's door.
    approved = client.post(
        f"/api/v1/groups/{group['id']}/candidates/{changes[0]['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    db_session.expire_all()
    row = db_session.get(Event, event["id"])
    assert row.starts_at == datetime(2026, 11, 7, 1, 30)
    assert row.ends_at == datetime(2026, 11, 7, 4, 0)
    assert row.place_name == "Riverside Hall"


def test_our_own_unsynced_edit_drafts_nothing(client, db_session):
    """Three-way discipline: we edited HERE and have not
    synced out yet — the copy merely says what we last
    pushed. Pulling must not propose reverting our own edit."""
    owner, group, integration, double = world(client)
    event = make_event(client, owner, group)
    sync_out(client, owner, group, integration)

    detail = client.get(
        f"/api/v1/groups/{group['id']}/events/{event['id']}",
        headers=bearer(owner),
    )
    etag = detail.json()["etag"]
    moved = client.patch(
        f"/api/v1/groups/{group['id']}/events/{event['id']}",
        json={"place_name": "New Barn"},
        headers={**bearer(owner), "If-Match": f'"{etag}"'},
    )
    assert moved.status_code == 200, moved.text

    pulled = pull(client, owner, group, integration)
    assert pulled.json()["candidates_created"] == 0
    assert pending_changes(client, owner, group) == []


def test_google_cancellation_drafts_a_cancellation(client, db_session):
    owner, group, integration, double = world(client)
    event = make_event(client, owner, group)
    sync_out(client, owner, group, integration)
    external_id = double.list_events(CALENDAR_ID)[0]["id"]
    double.edit_event(CALENDAR_ID, external_id, status="cancelled")

    pulled = pull(client, owner, group, integration)
    assert pulled.json()["candidates_created"] == 1
    changes = pending_changes(client, owner, group)
    assert changes[0]["draft"]["fields"]["cancelled"]["value"] is True

    approved = client.post(
        f"/api/v1/groups/{group['id']}/candidates/{changes[0]['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    db_session.expire_all()
    assert db_session.get(Event, event["id"]).status == "cancelled"


def test_missing_copy_is_drift_not_an_edit(client):
    """A copy deleted on Google is reconciliation's story
    (B-137 reports it); the pull drafts nothing from an
    absence."""
    owner, group, integration, double = world(client)
    make_event(client, owner, group)
    sync_out(client, owner, group, integration)
    external_id = double.list_events(CALENDAR_ID)[0]["id"]
    double.remove_event(CALENDAR_ID, external_id)

    pulled = pull(client, owner, group, integration)
    assert pulled.json()["candidates_created"] == 0
    assert pending_changes(client, owner, group) == []


def test_pull_without_a_connection_refuses_plainly(client):
    """No fixture installed = the unavailable boundary: the
    pull refuses in the boundary's own plain words."""
    owner = register(client)
    group = make_group(client, owner)
    integration = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={
            "kind": "google_calendar",
            "name": "Village Google calendar",
            "config": {
                "calendar_id": CALENDAR_ID,
                "access_token": "fixture-token-not-real",
            },
        },
        headers=bearer(owner),
    )
    assert integration.status_code == 201, integration.text
    refused = pull(client, owner, group, integration.json())
    assert refused.status_code == 422, refused.text
    assert "No Google account is connected" in refused.json()["detail"]
