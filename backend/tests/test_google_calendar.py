"""Google Calendar sync, one-way out (roadmap B-132).

The acceptance is the fixture integration test: connect
records its verdict, sync upserts the group's published
events into the chosen calendar, edits re-sync, and a
cancellation propagates AS a cancellation. The decided scope
is proven, not just stated: an edit made on the Google side
is never read back — the event here does not move, and the
tampered copy is left standing for the reconciliation job
(B-137) to notice. No Google account is connected in this
environment; the double is the deliverable, as the handoff
says.
"""

import pytest

from conftest import bearer, make_group, register
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


def make_event(client, owner, group, title, **extra):
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": title,
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "place_name": "Greenfield Farm",
            **extra,
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return created.json()


def sync(client, owner, group, integration):
    response = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration['id']}/google-sync",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_connect_records_its_verdict(client):
    owner, group, integration, double = world(client)
    connected = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration['id']}/google-connect",
        headers=bearer(owner),
    )
    assert connected.status_code == 200, connected.text
    body = connected.json()
    assert body["status"] == "ok"
    assert body["last_error"] == ""
    assert body["last_success_at"] is not None
    # The wire still names only the config KEYS held.
    assert body["config_keys"] == ["access_token", "calendar_id"]


def test_sync_upserts_edits_and_propagates_cancellation(client):
    owner, group, integration, double = world(client)
    first = make_event(client, owner, group, "Autumn Social")
    make_event(client, owner, group, "Bonfire Night")

    run = sync(client, owner, group, integration)
    assert run == {
        "integration_id": integration["id"],
        "pushed": 2, "updated": 0, "cancelled": 0, "unchanged": 0,
    }
    copies = double.list_events(CALENDAR_ID)
    assert {c["summary"] for c in copies} == {"Autumn Social", "Bonfire Night"}
    assert all(c["status"] == "confirmed" for c in copies)

    # A quiet re-run: every copy already agrees.
    run = sync(client, owner, group, integration)
    assert run["unchanged"] == 2 and run["pushed"] == 0

    # An edit here travels on the next sync — same copy, not
    # a second event on the calendar.
    gid = group["id"]
    moved = client.patch(
        f"/api/v1/groups/{gid}/events/{first['id']}",
        json={"starts_at": "2026-11-06T19:30:00-05:00"},
        headers={**bearer(owner), "If-Match": '"v1"'},
    )
    assert moved.status_code == 200, moved.text
    run = sync(client, owner, group, integration)
    assert run["updated"] == 1 and run["unchanged"] == 1
    copies = double.list_events(CALENDAR_ID)
    assert len(copies) == 2
    social = next(c for c in copies if c["summary"] == "Autumn Social")
    assert social["start"] == "2026-11-07T00:30:00+00:00"

    # Cancellation propagates AS a cancellation: the copy
    # stays on the calendar, marked — it does not vanish.
    client.post(
        f"/api/v1/groups/{gid}/events/{first['id']}/cancel",
        json={"reason": "the barn flooded"}, headers=bearer(owner),
    )
    run = sync(client, owner, group, integration)
    assert run["cancelled"] == 1
    social = double.get_event(CALENDAR_ID, social["id"])
    assert social["status"] == "cancelled"


def test_one_way_scope_google_edits_are_never_read_back(client, db_session):
    owner, group, integration, double = world(client)
    event = make_event(client, owner, group, "Autumn Social")
    sync(client, owner, group, integration)
    copy = double.list_events(CALENDAR_ID)[0]

    # Someone edits the copy over on Google: new time, new
    # place. One-way scope says: not our truth, not read.
    double.edit_event(
        CALENDAR_ID, copy["id"],
        start="2026-12-01T00:00:00+00:00", location="Somewhere Else",
    )
    run = sync(client, owner, group, integration)
    # Our link hash still matches OUR event, so the sync
    # touches nothing — and the event here never moved.
    assert run["unchanged"] == 1
    from gpa.models.events import Event

    row = db_session.get(Event, event["id"])
    assert row.place_name == "Greenfield Farm"
    assert row.starts_at.hour == 23 and row.starts_at.minute == 30  # 18:30 EST
    # The tampered copy stands, for B-137 to notice — not
    # silently overwritten by us either.
    assert double.get_event(CALENDAR_ID, copy["id"])["location"] == (
        "Somewhere Else"
    )


def test_sync_refusals_are_plain_words(client):
    owner = register(client)
    group = make_group(client, owner)
    integration = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "google_calendar", "name": "G", "config": {}},
        headers=bearer(owner),
    ).json()
    refused = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration['id']}/google-sync",
        headers=bearer(owner),
    )
    assert refused.status_code == 422, refused.text
    assert "missing: calendar_id, access_token" in refused.json()["detail"]

    # Config complete but the boundary is this environment's
    # unavailable default — and it says so, in words.
    client.patch(
        f"/api/v1/groups/{group['id']}/integrations/{integration['id']}",
        json={"config": {"calendar_id": CALENDAR_ID, "access_token": "x"}},
        headers=bearer(owner),
    )
    refused = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration['id']}/google-sync",
        headers=bearer(owner),
    )
    assert refused.status_code == 422
    assert "No Google account is connected" in refused.json()["detail"]
