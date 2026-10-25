"""Notification core: the outbox (roadmap B-095) + deliverability
records (B-102).

The promises under test:

- creating a notification NEVER sends inline — the row and its
  send job are written, the transport is untouched until a
  worker runs the job;
- a dead mail server can never fail a user flow: the invite
  still returns 201, the notification retries, and when the
  server comes back an admin resend delivers it;
- retries end: at the attempt ceiling the row is dead, and the
  admin failure list shows it — never silently retried forever;
- a provider bounce lands on the failure list with its mail
  event attached.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.core import Job
from gpa.models.notifications import Notification
from gpa.services import jobs as job_service
from gpa.services import mailer
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture(autouse=True)
def capture_transport(app):
    """Every test here sends through the capture driver; the
    registry is restored afterwards so no test leaks a driver
    into another. Depends on ``app`` so the swap lands AFTER
    create_app installs the configured (file) transport."""
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
            "title": "Harvest Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "timezone": NY, "place_name": "Riverside Hall",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return {"group": group, "owner": owner, "ana": ana, "event": created.json()}


def invite(client, world, email):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/invites",
        json={"email": email}, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def notifications(db_session, **filters):
    query = db_session.query(Notification)
    for key, value in filters.items():
        query = query.filter(getattr(Notification, key) == value)
    return query.order_by(Notification.created_at, Notification.id).all()


def run_due_jobs(app, db_session, limit=20):
    """Run due jobs until none are left (bounded)."""
    ran = 0
    while ran < limit and job_service.process_one(db_session, app.state.settings, "w-test"):
        ran += 1
    return ran


def test_invite_writes_outbox_row_and_job_but_sends_nothing_inline(
    client, app, world, db_session, capture_transport
):
    made = invite(client, world, "outside@example.org")
    rows = notifications(db_session, kind="invite")
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "queued"
    assert row.recipient_email == "outside@example.org"
    assert row.send_email is True and row.in_app is False  # a guest: email only
    assert made["link"] and made["link"].endswith(made["id"]) is False
    # The token rides the body — and the transport has NOT been
    # touched: nothing was sent inside the request.
    assert "/invite/" in row.body_text
    assert capture_transport.sent == []
    job = db_session.get(Job, row.job_id)
    assert job is not None and job.kind == "notify" and job.status == "queued"

    # The worker sends it; the row and the capture agree.
    assert run_due_jobs(app, db_session) >= 1
    db_session.refresh(row)
    assert row.status == "sent" and row.sent_at is not None
    assert len(capture_transport.sent) == 1
    sent = capture_transport.sent[0]
    assert sent["to"] == "outside@example.org"
    assert sent["subject"] == row.subject and sent["body_text"] == row.body_text


def test_kill_the_mail_server_flows_survive_and_retry(
    client, app, world, db_session, capture_transport
):
    failing = mailer.FailingTransport()
    mailer.set_transport(failing)
    # The user flow succeeds with the mail server dead.
    made = invite(client, world, "patient@example.org")
    assert made["id"]
    row = notifications(db_session, kind="invite")[0]

    # The send job fails; the row records the failure honestly.
    # (Exactly one run: the test chassis' backoff is 0s, so a
    # drain loop would retry to death inside a single call —
    # that road is the dead-letter test's, below.)
    assert run_due_jobs(app, db_session, limit=1) == 1
    db_session.refresh(row)
    assert row.status == "failed" and row.attempts == 1
    assert "unreachable" in row.last_error
    assert failing.attempts == 1

    # The server comes back; an organiser resends; delivery lands.
    mailer.set_transport(capture_transport)
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/notifications/{row.id}/resend",
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert run_due_jobs(app, db_session) >= 1
    db_session.refresh(row)
    assert row.status == "sent"
    assert capture_transport.sent[-1]["to"] == "patient@example.org"


def test_dead_after_max_attempts_and_admin_failure_list(
    client, app, world, db_session
):
    mailer.set_transport(mailer.FailingTransport())
    invite(client, world, "doomed@example.org")
    row = notifications(db_session, kind="invite")[0]
    job = db_session.get(Job, row.job_id)
    # Drive the retries: the chassis backs off, so each round
    # the job is made due again by hand — the fake clock.
    for _ in range(6):
        job = db_session.get(Job, row.job_id)
        from gpa.models.identity import now_utc

        job.run_at = now_utc()
        db_session.commit()
        job_service.process_one(db_session, app.state.settings, "w-test")
        db_session.refresh(row)
        if row.status == "dead":
            break
    assert row.status == "dead" and row.attempts >= 5
    job = db_session.get(Job, row.job_id)
    assert job.status == "dead"  # the chassis agrees: no more retries

    # Visible to an organiser, forbidden to a plain member.
    as_owner = client.get(
        f"/api/v1/groups/{world['group']['id']}/notification-failures",
        headers=bearer(world["owner"]),
    )
    assert as_owner.status_code == 200, as_owner.text
    listed = as_owner.json()["notifications"]
    assert [n["id"] for n in listed] == [row.id]
    assert listed[0]["status"] == "dead"
    as_member = client.get(
        f"/api/v1/groups/{world['group']['id']}/notification-failures",
        headers=bearer(world["ana"]),
    )
    assert as_member.status_code == 403


def test_bounce_webhook_marks_failed_and_lists(
    client, app, world, db_session, capture_transport
):
    invite(client, world, "bouncy@example.org")
    row = notifications(db_session, kind="invite")[0]
    assert run_due_jobs(app, db_session) >= 1
    db_session.refresh(row)
    assert row.status == "sent"

    response = client.post(
        "/api/v1/webhooks/mail/testmark",
        json={
            "email": "bouncy@example.org", "event_type": "bounce",
            "notification_id": row.id, "detail": "mailbox full",
        },
    )
    assert response.status_code == 201, response.text
    db_session.refresh(row)
    assert row.status == "failed" and "bounced" in row.last_error

    failures = client.get(
        f"/api/v1/groups/{world['group']['id']}/notification-failures",
        headers=bearer(world["owner"]),
    ).json()
    assert [n["id"] for n in failures["notifications"]] == [row.id]
    assert len(failures["mail_events"]) == 1
    event = failures["mail_events"][0]
    assert event["event_type"] == "bounce" and event["provider"] == "testmark"
