"""Phase 12 gate (roadmap B-138).

One event's full outward life, end to end: drafted for
Facebook, pressed onto the Page, synced onto Google,
moved (and both copies brought back into agreement by
their own doors), the group told through the change-alert
machinery — with the Messenger leg honestly recording
``driver_unavailable`` — cancelled (and the cancellation
carried out by both doors), and the group's public face
served to a registered embed origin while a stranger's
origin gets nothing. Reconciliation, run mid-life, finds
no drift once the doors have done their work.

Everything runs against the recorded fixtures — no live
third-party action of any kind (the phase's standing
boundary; see TEST_REPORT's Phase 12 addendum).
"""

import pytest

from conftest import bearer, make_group, register
from gpa.models.notifications import Notification
from gpa.services import facebook_events, google_calendar, jobs as job_service
from gpa.services import mailer, messenger_transport
from test_groups import invite_and_accept

PAGE_ID = "page-42"
CALENDAR_ID = "cal-42"


@pytest.fixture()
def capture_transport(app):
    # Depends on ``app`` so create_app's wiring runs first
    # and this capture replaces it (the registry lesson).
    capture = mailer.CaptureTransport()
    mailer.set_transport(capture)
    messenger_transport.set_transport(
        messenger_transport.UnavailableMessengerTransport()
    )
    yield capture
    mailer.set_transport(None)
    messenger_transport.set_transport(None)


@pytest.fixture(autouse=True)
def _doubles():
    fb = facebook_events.FixtureFacebookClient()
    gg = google_calendar.FixtureGoogleClient()
    facebook_events.set_client(fb)
    google_calendar.set_client(gg)
    yield fb, gg
    facebook_events.set_client(None)
    google_calendar.set_client(None)


def drain(app, db_session, limit=60):
    ran = 0
    while ran < limit and job_service.process_one(
        db_session, app.state.settings, "w-gate12"
    ):
        ran += 1
    return ran


def make_integration(client, owner, group, kind, config):
    response = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": kind, "name": kind, "config": config},
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_one_events_full_outward_life(client, app, db_session, capture_transport, _doubles):
    fb, gg = _doubles
    owner = register(client)
    group = make_group(client, owner, "Village Circle",
                       visibility="public-listed", home_timezone="UTC")
    gid = group["id"]
    ana = invite_and_accept(client, owner, gid, "ana@example.org", "member")
    fb_row = make_integration(client, owner, group, "facebook_events",
                              {"page_id": PAGE_ID, "access_token": "tok"})
    gg_row = make_integration(client, owner, group, "google_calendar",
                              {"calendar_id": CALENDAR_ID, "access_token": "tok"})

    event = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Village Fete", "place_name": "The Green",
            "starts_at": "2026-11-06T18:30:00Z",
            "ends_at": "2026-11-06T21:00:00Z",
        },
        headers=bearer(owner),
    ).json()
    eid = event["id"]
    rsvp = client.put(
        f"/api/v1/groups/{gid}/events/{eid}/rsvp",
        json={"status": "coming"}, headers=bearer(ana),
    )
    assert rsvp.status_code == 200, rsvp.text
    link = client.post(
        f"/api/v1/groups/{gid}/account-links",
        json={"connector_kind": "messenger", "external_user_id": "msgr-ana",
              "external_name": "Ana", "user_id": ana["user_id"]},
        headers=bearer(owner),
    )
    assert link.status_code == 201, link.text

    # 1 — The draft exists before anything travels (B-058).
    draft = client.get(
        f"/api/v1/groups/{gid}/events/{eid}/facebook-draft", headers=bearer(owner)
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["payload"]["name"] == "Village Fete"

    # 2 — The press creates the Page copy; the sync pushes
    # the calendar copy. Both doors, deliberately used.
    pressed = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/facebook", headers=bearer(owner)
    ).json()
    assert pressed["action"] == "created" and pressed["differs"] is False
    connected = client.post(
        f"/api/v1/groups/{gid}/integrations/{gg_row['id']}/google-connect",
        headers=bearer(owner),
    )
    assert connected.json()["status"] == "ok"
    synced = client.post(
        f"/api/v1/groups/{gid}/integrations/{gg_row['id']}/google-sync",
        headers=bearer(owner),
    ).json()
    assert synced["pushed"] == 1

    # The health board agrees: both connections working.
    board = client.get(
        f"/api/v1/groups/{gid}/integrations/health-board", headers=bearer(owner)
    ).json()
    verdicts = {entry["kind"]: entry for entry in board}
    assert verdicts["google_calendar"]["advice"] == "Working — nothing to do."

    # 3 — The event moves. Neither copy follows on its own;
    # each door brings its own copy back into agreement.
    current = client.get(
        f"/api/v1/groups/{gid}/events/{eid}", headers=bearer(owner)
    ).json()
    moved = client.patch(
        f"/api/v1/groups/{gid}/events/{eid}",
        json={"starts_at": "2026-11-06T19:30:00Z",
              "ends_at": "2026-11-06T22:00:00Z"},
        headers={**bearer(owner), "If-Match": f'"{current["etag"]}"'},
    )
    assert moved.status_code == 200, moved.text
    state = client.get(
        f"/api/v1/groups/{gid}/events/{eid}/facebook", headers=bearer(owner)
    ).json()
    assert state["differs"] is True
    assert client.post(
        f"/api/v1/groups/{gid}/events/{eid}/facebook", headers=bearer(owner)
    ).json()["action"] == "updated"
    synced = client.post(
        f"/api/v1/groups/{gid}/integrations/{gg_row['id']}/google-sync",
        headers=bearer(owner),
    ).json()
    assert synced["updated"] == 1

    # Reconciliation, run now, finds the copies in agreement.
    drift = client.post(
        f"/api/v1/groups/{gid}/integrations/reconcile", headers=bearer(owner)
    ).json()
    assert drift["drift"] == []

    # 4 — The group is told: the re-announce draft is
    # approved, the alert reaches Ana's mailbox — and her
    # Messenger leg records this deployment's honest state.
    draft = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/reannounce-drafts",
        headers=bearer(owner),
    ).json()
    approved = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/reannounce-drafts/{draft['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    drain(app, db_session)
    ana_mail = [m for m in capture_transport.sent if m["to"] == "ana@example.org"]
    assert any(m["subject"] == "Update: Village Fete" for m in ana_mail)
    row = (
        db_session.query(Notification)
        .filter(
            Notification.kind == "change.announcement",
            Notification.recipient_user_id == ana["user_id"],
        )
        .one()
    )
    assert row.send_messenger is True
    assert row.messenger_status == "driver_unavailable"
    assert row.status == "sent"

    # 5 — Cancellation, carried out by both doors.
    cancelled = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/cancel",
        json={"reason": "the field is waterlogged"}, headers=bearer(owner),
    )
    assert cancelled.status_code == 200, cancelled.text
    carried = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/facebook", headers=bearer(owner)
    ).json()
    assert carried["action"] == "cancelled"
    assert fb.get_event(PAGE_ID, pressed["external_id"])["cancelled"] is True
    synced = client.post(
        f"/api/v1/groups/{gid}/integrations/{gg_row['id']}/google-sync",
        headers=bearer(owner),
    ).json()
    assert synced["cancelled"] == 1
    copies = gg.list_events(CALENDAR_ID)
    assert copies and copies[0]["status"] == "cancelled"

    # 6 — The public face: a registered origin embeds; a
    # stranger's gets nothing.
    client.put(
        f"/api/v1/groups/{gid}/embed-origins",
        json={"origins": ["https://village.example"]}, headers=bearer(owner),
    )
    page = client.get(
        f"/api/v1/embed/groups/{group['slug']}",
        headers={"Origin": "https://village.example"},
    )
    assert page.status_code == 200
    assert page.headers.get("access-control-allow-origin") == (
        "https://village.example"
    )
    assert page.json()["events"][0]["title"] == "Village Fete"
    stranger = client.get(
        f"/api/v1/embed/groups/{group['slug']}",
        headers={"Origin": "https://stranger.example"},
    )
    assert stranger.status_code == 403
