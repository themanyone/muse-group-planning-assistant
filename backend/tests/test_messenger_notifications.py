"""Messenger notifications (roadmap B-134).

B-125's access does not exist, so this slice ships the
clean ``driver_unavailable`` — and its acceptance is that
the RULES are shared: the same preference switches and the
same quiet-hours hold govern Messenger exactly as they
govern email and in-app, proven here by running the same
scenarios across channels.

Opt-in, stated precisely: a row rides Messenger only when
the member's messenger switch is on for the kind's category
AND the member linked a Messenger identity for the group
(B-123's account link — identity by link, never by name).
Delivery records the outcome on the row: in this
environment, ``driver_unavailable``, while email carries
the words and the row still completes as sent.
"""

from datetime import datetime, time, timedelta, timezone

import pytest

from conftest import bearer, make_group, register
from gpa.models.groups import Group
from gpa.models.identity import User
from gpa.services import jobs as job_service
from gpa.services import mailer
from gpa.services import messenger_transport
from gpa.services import notifications as notify_service
from test_groups import invite_and_accept

MSGR_REF = "msgr-ana-123"


@pytest.fixture()
def _drivers(app):
    # Depends on ``app`` so create_app's own transport wiring
    # runs FIRST and this capture replaces it — the registry
    # is process-wide and last write wins.
    capture = mailer.CaptureTransport()
    mailer.set_transport(capture)
    yield capture
    mailer.set_transport(None)
    messenger_transport.set_transport(None)


def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone="UTC")
    ana = invite_and_accept(client, owner, group["id"], "ana@example.org", "member")
    return owner, group, ana


def link_messenger(client, owner, group, ana):
    response = client.post(
        f"/api/v1/groups/{group['id']}/account-links",
        json={
            "connector_kind": "messenger",
            "external_user_id": MSGR_REF,
            "external_name": "Ana on Messenger",
            "user_id": ana["user_id"],
        },
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text


def remind(db_session, group, ana, **kwargs):
    row = notify_service.create_notification(
        db_session,
        group=db_session.get(Group, group["id"]),
        recipient_user=db_session.get(User, ana["user_id"]),
        recipient_label=f"user:{ana['user_id']}",
        kind="reminder",
        subject="Reminder: Autumn Social",
        body_text="Autumn Social starts soon.",
        **kwargs,
    )
    db_session.commit()
    return row


def test_messenger_opt_in_needs_link_and_switch(client, db_session, _drivers):
    owner, group, ana = world(client)

    # No link: the channel is not even attempted, switch or no.
    row = remind(db_session, group, ana)
    assert row.send_messenger is False

    link_messenger(client, owner, group, ana)
    row = remind(db_session, group, ana)
    assert row.send_messenger is True

    # The member turns Messenger off for reminders — the
    # SAME preference door as every channel — and the row
    # stops riding it, while email still does.
    response = client.put(
        f"/api/v1/users/me/groups/{group['id']}/notification-preferences",
        json={"switches": [
            {"channel": "messenger", "kind": "reminder", "enabled": False}
        ]},
        headers=bearer(ana),
    )
    assert response.status_code == 200, response.text
    view = response.json()
    msgr = [s for s in view["switches"] if s["channel"] == "messenger"]
    assert len(msgr) == 5  # every category, one switchboard
    assert all(s["enabled"] for s in msgr if s["kind"] != "reminder")
    row = remind(db_session, group, ana)
    assert row.send_messenger is False
    assert row.send_email is True


def test_driver_unavailable_recorded_while_email_carries(
    client, db_session, app, _drivers
):
    owner, group, ana = world(client)
    link_messenger(client, owner, group, ana)
    # The process driver is this environment's truth:
    # unavailable (access never granted).
    messenger_transport.set_transport(
        messenger_transport.UnavailableMessengerTransport()
    )
    row = remind(db_session, group, ana)
    assert job_service.process_one(db_session, app.state.settings, "w-msg")
    delivered = db_session.get(type(row), row.id)
    # The notification COMPLETED — Messenger's state is a
    # fact on its record, not a failure of the message.
    assert delivered.status == "sent"
    assert delivered.messenger_status == "driver_unavailable"
    # And the words really did travel — by email.
    assert [m["to"] for m in _drivers.sent] == ["ana@example.org"]


def test_capture_driver_marks_the_messenger_leg_sent(
    client, db_session, app, _drivers
):
    owner, group, ana = world(client)
    link_messenger(client, owner, group, ana)
    capture = messenger_transport.CaptureMessengerTransport()
    messenger_transport.set_transport(capture)
    row = remind(db_session, group, ana)
    assert job_service.process_one(db_session, app.state.settings, "w-msg")
    delivered = db_session.get(type(row), row.id)
    assert delivered.messenger_status == "sent"
    assert capture.sent[0]["messenger_ref"] == MSGR_REF
    assert capture.sent[0]["subject"] == "Reminder: Autumn Social"


def test_quiet_hours_hold_is_shared_by_every_channel(
    client, db_session, app, _drivers
):
    owner, group, ana = world(client)
    link_messenger(client, owner, group, ana)
    capture = messenger_transport.CaptureMessengerTransport()
    messenger_transport.set_transport(capture)

    # Ana's quiet hours: 22:00 → 07:00 UTC. The reminder's
    # moment is placed at 23:30 UTC inside them.
    response = client.put(
        f"/api/v1/users/me/groups/{group['id']}/notification-preferences",
        json={
            "switches": [],
            "quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "UTC"},
        },
        headers=bearer(ana),
    )
    assert response.status_code == 200, response.text
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    moment = now.replace(hour=23, minute=30, second=0, microsecond=0)
    if moment <= now:
        moment += timedelta(days=1)
    row = remind(db_session, group, ana, send_after=moment)

    # Held — once, for the whole row: there is one hold, and
    # every channel waits behind it together. The send job is
    # not even due yet, so a worker pass moves nothing.
    assert row.status == "held"
    assert row.send_messenger is True and row.send_email is True
    assert job_service.process_one(db_session, app.state.settings, "w-msg") is False
    held = db_session.get(type(row), row.id)
    assert held.status == "held"  # nothing sent early, on any channel
    assert _drivers.sent == []  # no email…
    assert capture.sent == []  # …and no Messenger message either
    assert held.messenger_status == ""
