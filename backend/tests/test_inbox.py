"""The in-app inbox (roadmap B-101) + preferences plumbing.

The inbox and the email are two readings of ONE row: deliver
a notification and the inbox item's subject/body are the very
words the transport carried. Mark-read is the owner's alone —
another person's item is a 404, its existence included. The
SSE stream nudges the recipient (and only the recipient) when
a notification lands in their inbox.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.notifications import Notification
from gpa.services import jobs as job_service
from gpa.services import mailer
from gpa.services import stream as stream_service
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture(autouse=True)
def capture_transport(app):
    transport = mailer.CaptureTransport()
    mailer.set_transport(transport)
    yield transport
    mailer.set_transport(None)


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    ana = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Inbox Social", "starts_at": "2026-11-12T19:00:00-05:00",
            "ends_at": "2026-11-12T21:00:00-05:00", "timezone": NY,
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    invite = client.post(
        f"/api/v1/groups/{group['id']}/events/{created.json()['id']}/invites",
        json={"email": ana["email"]}, headers=bearer(owner),
    )
    assert invite.status_code == 201, invite.text
    return {"group": group, "owner": owner, "ana": ana, "event": created.json()}


def inbox(client, who, **params):
    response = client.get("/api/v1/users/me/inbox", headers=bearer(who), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_inbox_lists_and_mark_read(client, world, db_session):
    box = inbox(client, world["ana"])
    assert box["unread"] == 1 and len(box["items"]) == 1
    item = box["items"][0]
    assert item["kind"] == "invite" and item["read_at"] is None

    marked = client.post(
        f"/api/v1/users/me/inbox/{item['id']}/read", headers=bearer(world["ana"])
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["read_at"] is not None
    box = inbox(client, world["ana"])
    assert box["unread"] == 0
    box = inbox(client, world["ana"], unread_only=True)
    assert box["items"] == []


def test_inbox_is_private(client, world):
    item = inbox(client, world["ana"])["items"][0]
    # The owner cannot read or mark Ana's item — 404, not 403:
    # the inbox does not confirm its contents to anyone else.
    assert client.post(
        f"/api/v1/users/me/inbox/{item['id']}/read", headers=bearer(world["owner"])
    ).status_code == 404
    assert inbox(client, world["owner"])["items"] == [
        i for i in inbox(client, world["owner"])["items"] if i["id"] != item["id"]
    ]


def test_inbox_and_email_never_disagree(client, app, world, db_session, capture_transport):
    item = inbox(client, world["ana"])["items"][0]
    while job_service.process_one(db_session, app.state.settings, "w-inbox"):
        pass
    sent = [m for m in capture_transport.sent if m["to"] == world["ana"]["email"]]
    assert len(sent) == 1
    assert sent[0]["subject"] == item["subject"]
    assert sent[0]["body_text"] == item["body_text"]
    row = db_session.get(Notification, item["id"])
    assert row.status == "sent"


def test_stream_nudges_the_recipient(client, app, world, db_session):
    factory = app.state.db.session
    events = stream_service.group_events(
        factory, world["group"]["id"], user_id=world["ana"]["user_id"],
        poll_seconds=0.01, max_polls=2,
    )
    assert next(events).startswith("event: snapshot")
    # A fresh notification lands for Ana (a second invite).
    created = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/invites",
        json={"email": world["ana"]["email"]}, headers=bearer(world["owner"]),
    )
    assert created.status_code == 201, created.text
    frames = list(events)
    nudges = [f for f in frames if f.startswith("event: notification.created")]
    assert nudges, frames
    assert '"kind": "invite"' in nudges[-1]


def test_preferences_switch_stops_a_channel(client, world, db_session):
    # Ana turns email off for invites in this group.
    response = client.put(
        f"/api/v1/users/me/groups/{world['group']['id']}/notification-preferences",
        json={"switches": [{"channel": "email", "kind": "invite", "enabled": False}]},
        headers=bearer(world["ana"]),
    )
    assert response.status_code == 200, response.text
    view = response.json()
    off = [s for s in view["switches"] if s["channel"] == "email" and s["kind"] == "invite"]
    assert off and off[0]["enabled"] is False
    on = [s for s in view["switches"] if s["channel"] == "in_app" and s["kind"] == "invite"]
    assert on and on[0]["enabled"] is True  # the rest default on

    second = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/invites",
        json={"email": world["ana"]["email"]}, headers=bearer(world["owner"]),
    )
    assert second.status_code == 201, second.text
    rows = (
        db_session.query(Notification)
        .filter(Notification.kind == "invite", Notification.recipient_user_id == world["ana"]["user_id"])
        .order_by(Notification.created_at)
        .all()
    )
    assert rows[-1].send_email is False and rows[-1].in_app is True
