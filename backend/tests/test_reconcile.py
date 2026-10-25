"""Reconciliation job (roadmap B-137).

The nightly compare: outside copies (Facebook, Google) are
read, compared against ours, and drift is REPORTED to the
organisers — and never, ever repaired by the machine.
Acceptance, planted by hand: a Facebook-side edit and a
Google-side deletion both surface in one organiser
notification; the tampered copies are exactly as tampered
afterwards (the job wrote nothing outward). Settling is a
person's deliberate act — the missing copy returns at the
sync press; the edited copy is removed and re-pressed,
never silently overwritten — and then the next run is
clean. The nightly chain anchors itself when an outside
calendar is connected and perpetuates itself link by link.
"""

from datetime import timedelta

import pytest

from conftest import bearer, make_group, register
from gpa.models.identity import now_utc
from gpa.models.integrations import ExternalEventLink
from gpa.models.core import Job
from gpa.models.notifications import Notification
from gpa.services import facebook_events, google_calendar
from gpa.services import jobs as job_service
from test_groups import invite_and_accept


@pytest.fixture(autouse=True)
def _fb_client():
    client = facebook_events.FixtureFacebookClient()
    facebook_events.set_client(client)
    yield client
    facebook_events.set_client(None)


@pytest.fixture(autouse=True)
def _gg_client():
    client = google_calendar.FixtureGoogleClient()
    google_calendar.set_client(client)
    yield client
    google_calendar.set_client(None)


def make_integration(client, owner, group, kind, config):
    response = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": kind, "name": kind, "config": config},
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    return response.json()


def world(client, _fb_client, _gg_client):
    owner = register(client)
    group = make_group(client, owner, home_timezone="UTC")
    bo = invite_and_accept(client, owner, group["id"], "bo@example.org", "organiser")
    ana = invite_and_accept(client, owner, group["id"], "ana@example.org", "member")
    fb = make_integration(
        client, owner, group, "facebook_events",
        {"page_id": "page-1", "access_token": "tok"},
    )
    gg = make_integration(
        client, owner, group, "google_calendar",
        {"calendar_id": "cal-1", "access_token": "tok"},
    )
    event = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Harvest Supper", "place_name": "The Hall",
            "starts_at": "2026-11-14T19:00:00Z",
            "ends_at": "2026-11-14T22:00:00Z",
        },
        headers=bearer(owner),
    ).json()
    press = client.post(
        f"/api/v1/groups/{group['id']}/events/{event['id']}/facebook",
        headers=bearer(owner),
    )
    assert press.status_code == 200, press.text
    client.post(
        f"/api/v1/groups/{group['id']}/integrations/{gg['id']}/google-connect",
        headers=bearer(owner),
    )
    sync = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{gg['id']}/google-sync",
        headers=bearer(owner),
    )
    assert sync.json()["pushed"] == 1, sync.text
    return owner, group, bo, ana, event, gg


def drift_notes(db_session, group):
    return (
        db_session.query(Notification)
        .filter(
            Notification.group_id == group["id"],
            Notification.kind == "change.drift",
        )
        .all()
    )


def test_drift_is_reported_and_never_repaired(
    client, db_session, _fb_client, _gg_client
):
    owner, group, bo, ana, event, gg = world(client, _fb_client, _gg_client)
    links = {
        link.connector_kind: link
        for link in db_session.query(ExternalEventLink)
        .filter(ExternalEventLink.group_id == group["id"])
        .all()
    }
    # Plant the drift the way the outside world would: an
    # edit on Facebook, a deletion on Google.
    _fb_client.edit_event(
        links["facebook_events"].external_id, name="Harvest Supper — MOVED"
    )
    _gg_client.remove_event("cal-1", links["google_calendar"].external_id)

    response = client.post(
        f"/api/v1/groups/{group['id']}/integrations/reconcile",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    drift = {(d["connector"], d["kind"]) for d in response.json()["drift"]}
    assert drift == {
        ("facebook_events", "differs"),
        ("google_calendar", "missing"),
    }

    # The organisers — owner and Bo — are told; a plain
    # member is not (drift is an organiser's business).
    notes = drift_notes(db_session, group)
    recipients = {n.recipient_user_id for n in notes}
    assert recipients == {owner["user_id"], bo["user_id"]}
    assert "Harvest Supper" in notes[0].body_text
    assert "differs on Facebook" in notes[0].body_text
    assert "missing from Google Calendar" in notes[0].body_text

    # THE LAW: nothing was written outward. The tampered
    # copy is exactly as tampered; the deleted copy is still
    # deleted. Reconciliation reports; people reconcile.
    assert _fb_client.get_event(
        "page-1", links["facebook_events"].external_id
    )["name"] == "Harvest Supper — MOVED"
    assert _gg_client.get_event("cal-1", links["google_calendar"].external_id) is None

    # A person settles it, by the deliberate roads: the
    # edited Facebook copy is removed and re-pressed (an
    # outside edit is never silently overwritten — B-132's
    # law), and the missing Google copy is restored by the
    # sync press itself.
    removed = client.delete(
        f"/api/v1/groups/{group['id']}/events/{event['id']}/facebook",
        headers=bearer(owner),
    )
    assert removed.status_code == 200, removed.text
    client.post(
        f"/api/v1/groups/{group['id']}/events/{event['id']}/facebook",
        headers=bearer(owner),
    )
    client.post(
        f"/api/v1/groups/{group['id']}/integrations/{gg['id']}/google-sync",
        headers=bearer(owner),
    )
    # …and the next run is clean, with no new alarm.
    again = client.post(
        f"/api/v1/groups/{group['id']}/integrations/reconcile",
        headers=bearer(owner),
    )
    assert again.json()["drift"] == []
    assert len(drift_notes(db_session, group)) == 2


def test_the_nightly_chain_anchors_and_perpetuates(
    client, db_session, app, _fb_client, _gg_client
):
    owner = register(client)
    group = make_group(client, owner, home_timezone="UTC")

    def outstanding():
        return [
            job
            for job in db_session.query(Job)
            .filter(Job.kind == "reconcile", Job.status == "queued")
            .all()
            if '"group_id": "' + group["id"] + '"' in (job.payload_json or "")
        ]

    assert outstanding() == []  # nothing connected yet
    make_integration(
        client, owner, group, "facebook_events",
        {"page_id": "page-1", "access_token": "tok"},
    )
    db_session.expire_all()
    anchored = outstanding()
    assert len(anchored) == 1  # connecting anchored the chain

    # The night arrives: the run reconciles (nothing outside
    # yet, so no drift, no alarm) and queues tomorrow's link.
    job = anchored[0]
    job.run_at = now_utc() - timedelta(minutes=1)
    db_session.commit()
    assert job_service.process_one(db_session, app.state.settings, "w-night")
    db_session.expire_all()
    following = outstanding()
    assert len(following) == 1
    assert following[0].id != job.id
    assert following[0].run_at > now_utc()
    assert drift_notes(db_session, group) == []
