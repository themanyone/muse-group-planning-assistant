"""Phase 8 gate (roadmap B-104): a published event's full
comms life, end to end, with a fake clock.

invite → answer → reminder → move → change alert → digest
mention — one event, one guest, one member, one organiser,
and the capture transport as the mailbox. "Fake clock" here
is the house pattern: a job is made due by moving its
``run_at``, and time in the digest is injected at the service
seam — the machinery under test is the real machinery.
"""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.core import Job
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.notifications import Notification
from gpa.services import digests as digest_service
from gpa.services import jobs as job_service
from gpa.services import mailer
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture(autouse=True)
def capture_transport(app):
    transport = mailer.CaptureTransport()
    mailer.set_transport(transport)
    yield transport
    mailer.set_transport(None)


def drain(app, db_session, limit=50):
    ran = 0
    while ran < limit and job_service.process_one(db_session, app.state.settings, "w-gate"):
        ran += 1
    return ran


def make_due(db_session, job_id):
    job = db_session.get(Job, job_id)
    job.run_at = now_utc() - timedelta(seconds=1)
    db_session.commit()


def mail_to(transport, address):
    return [m for m in transport.sent if m["to"] == address]


def test_full_comms_life(client, app, db_session, capture_transport):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    ana = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    gid = group["id"]

    created = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Winter Feast", "starts_at": "2026-12-18T18:00:00-05:00",
            "ends_at": "2026-12-18T22:00:00-05:00", "timezone": NY,
            "place_name": "The Long Hall", "bring_list": "a bottle of mead",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    event = created.json()
    eid = event["id"]

    # 1 — INVITE: the guest is invited; the outbox carries it.
    invite = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/invites",
        json={"email": "guest@example.org"}, headers=bearer(owner),
    ).json()
    token = invite["link"].rstrip("/").split("/")[-1]
    drain(app, db_session)
    guest_mail = mail_to(capture_transport, "guest@example.org")
    assert len(guest_mail) == 1 and "Winter Feast" in guest_mail[0]["subject"]

    # 2 — ANSWER: the guest answers without an account; Ana too.
    answered = client.post(
        f"/api/v1/invites/{token}/rsvp", json={"status": "coming"}
    )
    assert answered.status_code == 201, answered.text
    rsvp = client.put(
        f"/api/v1/groups/{gid}/events/{eid}/rsvp",
        json={"status": "coming"}, headers=bearer(ana),
    )
    assert rsvp.status_code == 200, rsvp.text

    # 3 — REMINDER: Ana's day-before reminder exists with the
    # facts; run it due and it reaches her mailbox.
    reminder = (
        db_session.query(Notification)
        .filter(
            Notification.event_id == eid, Notification.kind == "reminder",
            Notification.recipient_user_id == ana["user_id"],
            Notification.reminder_rule == "days_before:1",
            Notification.status.in_(("queued", "held")),
        )
        .one()
    )
    assert "The Long Hall" in reminder.body_text
    assert "a bottle of mead" in reminder.body_text
    # The fake clock moves the moment itself — the row's
    # send_after AND its job's run_at, two faces of one time.
    reminder.send_after = now_utc() - timedelta(seconds=1)
    db_session.commit()
    make_due(db_session, reminder.job_id)
    drain(app, db_session)
    ana_mail = mail_to(capture_transport, ana["email"])
    assert any("Reminder: Winter Feast" == m["subject"] for m in ana_mail)

    # The guest holds reminders too (their seat at the table).
    guest_reminders = (
        db_session.query(Notification)
        .filter(
            Notification.event_id == eid, Notification.kind == "reminder",
            Notification.recipient_label == "guest:guest@example.org",
            Notification.status.in_(("queued", "held")),
        )
        .count()
    )
    assert guest_reminders == 3

    # 4 — MOVE + CHANGE ALERT: the feast moves a day; the draft
    # is approved; both the member and the guest are told.
    current = client.get(
        f"/api/v1/groups/{gid}/events/{eid}", headers=bearer(owner)
    ).json()
    moved = client.patch(
        f"/api/v1/groups/{gid}/events/{eid}",
        json={
            "starts_at": "2026-12-19T18:00:00-05:00",
            "ends_at": "2026-12-19T22:00:00-05:00",
        },
        headers={**bearer(owner), "If-Match": f'"{current["etag"]}"'},
    )
    assert moved.status_code == 200, moved.text
    draft = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/reannounce-drafts", headers=bearer(owner)
    ).json()
    approved = client.post(
        f"/api/v1/groups/{gid}/events/{eid}/reannounce-drafts/{draft['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    drain(app, db_session)
    ana_mail = mail_to(capture_transport, ana["email"])
    guest_mail = mail_to(capture_transport, "guest@example.org")
    assert any(m["subject"] == "Update: Winter Feast" for m in ana_mail)
    assert any(m["subject"] == "Update: Winter Feast" for m in guest_mail)

    # The move rescheduled the reminders: nothing live still
    # points at the old date's day-before moment (17 Dec 09:00
    # EST = 14:00 UTC).
    stale = (
        db_session.query(Notification)
        .filter(
            Notification.event_id == eid, Notification.kind == "reminder",
            Notification.status.in_(("queued", "held")),
            Notification.send_after == datetime(2026, 12, 17, 14, 0),
        )
        .count()
    )
    assert stale == 0

    # 5 — DIGEST MENTION: in the feast's own week, the digest
    # names it — composed at the service seam with its moment
    # injected (the endpoint's clock is the real one).
    group_row = db_session.get(Group, gid)
    digest = digest_service.run_digest(
        db_session, actor_label="user:test", group=group_row,
        now=datetime(2026, 12, 14, 12, 0),  # the Monday of the feast's week
    )
    assert "Winter Feast" in digest.body_text
    assert "Winter Feast" in digest.body_html
    drain(app, db_session)
    ana_mail = mail_to(capture_transport, ana["email"])
    assert any("your week ahead" in m["subject"] for m in ana_mail)

    # The whole trail, in kinds, for this one event's people.
    kinds = {
        row.kind
        for row in db_session.query(Notification)
        .filter(Notification.group_id == gid)
        .all()
    }
    assert {"invite", "reminder", "change.announcement", "digest"} <= kinds
