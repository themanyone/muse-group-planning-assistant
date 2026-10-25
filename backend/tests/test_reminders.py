"""The reminder engine (B-096), quiet hours (B-100), and AI
reminder wording (B-103).

- Publish materialises the default rules (7 days / 1 day /
  morning-of) per RSVP'd person, at civilised hours in the
  event's own timezone.
- Moving an event reschedules IN THE SAME TRANSACTION: old
  rows superseded, their jobs deleted — no orphan reminder
  for the old time.
- A reminder landing in a member's quiet hours is HELD to the
  quiet end, in their timezone (golden in two zones); an
  urgent organiser cancellation is never held.
- AI wording ships only when every fact survives verbatim;
  both versions are stored either way.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.ai import client as ai_client
from gpa.ai.client import ModelResult
from gpa.models.core import Job
from gpa.models.identity import now_utc
from gpa.models.notifications import Notification
from gpa.services import notifications as notify_service
from test_groups import invite_and_accept

NY = "America/New_York"
START = "2026-11-06T18:30:00-05:00"  # Friday 18:30 EST
END = "2026-11-06T21:00:00-05:00"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    ana = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Harvest Supper", "starts_at": START, "ends_at": END,
            "timezone": NY, "place_name": "Riverside Hall",
            "place_address": "12 River Road", "bring_list": "a dish to share",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return {"group": group, "owner": owner, "ana": ana, "event": created.json()}


def answer(client, world, who, status="coming"):
    response = client.put(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/rsvp",
        json={"status": status}, headers=bearer(who),
    )
    assert response.status_code == 200, response.text
    return response.json()


def reminders_for(db_session, event_id, user_id):
    return (
        db_session.query(Notification)
        .filter(
            Notification.event_id == event_id,
            Notification.kind == "reminder",
            Notification.recipient_user_id == user_id,
        )
        .order_by(Notification.send_after)
        .all()
    )


def test_publish_then_rsvp_materialises_default_rules(client, world, db_session):
    answer(client, world, world["ana"])
    rows = reminders_for(db_session, world["event"]["id"], world["ana"]["user_id"])
    live = [r for r in rows if r.status in ("queued", "held")]
    assert [r.reminder_rule for r in live] == [
        "days_before:7", "days_before:1", "morning_of",
    ]
    moments = {r.reminder_rule: r.send_after for r in live}
    # 09:00 event-local a week out (EDT, UTC-4) = 13:00 UTC.
    assert moments["days_before:7"] == datetime(2026, 10, 30, 13, 0)
    # 09:00 the day before (EST now, UTC-5) = 14:00 UTC.
    assert moments["days_before:1"] == datetime(2026, 11, 5, 14, 0)
    # 08:00 on the morning (EST) = 13:00 UTC.
    assert moments["morning_of"] == datetime(2026, 11, 6, 13, 0)
    # Every live row is backed by a live queued job.
    for row in live:
        job = db_session.get(Job, row.job_id)
        assert job is not None and job.status == "queued" and job.kind == "notify"
        assert job.run_at == row.send_after


def test_reminder_body_carries_facts(client, world, db_session):
    answer(client, world, world["ana"])
    row = reminders_for(db_session, world["event"]["id"], world["ana"]["user_id"])[0]
    assert "Riverside Hall" in row.body_text
    assert "a dish to share" in row.body_text
    assert "18:30" in row.body_text
    assert row.template_body == row.body_text  # AI off: template is the words
    assert row.wording_source == "template"


def test_move_event_reschedules_no_orphans(client, world, db_session):
    answer(client, world, world["ana"])
    before = reminders_for(db_session, world["event"]["id"], world["ana"]["user_id"])
    old_job_ids = [r.job_id for r in before if r.status in ("queued", "held")]
    event = world["event"]

    current = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        headers=bearer(world["owner"]),
    ).json()
    moved = client.patch(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        json={
            "starts_at": "2026-11-08T18:30:00-05:00",
            "ends_at": "2026-11-08T21:00:00-05:00",
        },
        headers={**bearer(world["owner"]), "If-Match": f'"{current["etag"]}"'},
    )
    assert moved.status_code == 200, moved.text

    db_session.expire_all()  # the app's PATCH rewrote these rows in its own session
    after = reminders_for(db_session, world["event"]["id"], world["ana"]["user_id"])
    superseded = [r for r in after if r.status == "superseded"]
    live = [r for r in after if r.status in ("queued", "held")]
    assert len(superseded) == 3 and len(live) == 3
    # The old jobs are GONE — deleted, not merely cancelled.
    for job_id in old_job_ids:
        assert db_session.get(Job, job_id) is None
    # New moments track the new date (Sunday 8 November).
    moments = {r.reminder_rule: r.send_after for r in live}
    assert moments["morning_of"] == datetime(2026, 11, 8, 13, 0)
    # And the words say what changed.
    assert "Start:" in live[0].body_text


def test_custom_group_rules(client, world, db_session):
    patched = client.patch(
        f"/api/v1/groups/{world['group']['id']}",
        json={"reminder_rules": [{"kind": "days_before", "days": 3}]},
        headers=bearer(world["owner"]),
    )
    assert patched.status_code == 200, patched.text
    answer(client, world, world["ana"])
    live = [
        r for r in reminders_for(db_session, world["event"]["id"], world["ana"]["user_id"])
        if r.status in ("queued", "held")
    ]
    assert [r.reminder_rule for r in live] == ["days_before:3"]


def test_withdrawn_answer_stops_reminders(client, world, db_session):
    answer(client, world, world["ana"])
    answer(client, world, world["ana"], status="cant")
    live = [
        r for r in reminders_for(db_session, world["event"]["id"], world["ana"]["user_id"])
        if r.status in ("queued", "held")
    ]
    assert live == []


# --- Quiet hours (B-100) -------------------------------------------


def _user(db_session, user_id):
    from gpa.models.identity import User

    return db_session.get(User, user_id)


def _group(db_session, group_id):
    from gpa.models.groups import Group

    return db_session.get(Group, group_id)


def _create_reminder_at(db_session, world, due_utc):
    """Write one reminder notification for Ana at an exact due
    moment, through the outbox — quiet-hours policy applies at
    creation."""
    return notify_service.create_notification(
        db_session,
        group=_group(db_session, world["group"]["id"]),
        recipient_user=_user(db_session, world["ana"]["user_id"]),
        recipient_label=f"user:{world['ana']['user_id']}",
        kind="reminder",
        subject="Reminder: Harvest Supper",
        body_text="A reminder.",
        event_id=world["event"]["id"],
        send_after=due_utc,
        now=now_utc(),
    )


def _set_quiet(client, world, start, end, tz):
    response = client.put(
        f"/api/v1/users/me/groups/{world['group']['id']}/notification-preferences",
        json={"quiet_hours": {"start": start, "end": end, "timezone": tz}},
        headers=bearer(world["ana"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_quiet_hours_hold_new_york(client, world, db_session):
    _set_quiet(client, world, "21:00", "09:00", NY)
    # 02:30 UTC = 22:30 EDT the evening before — inside quiet.
    due = datetime(2026, 10, 25, 2, 30)
    row = _create_reminder_at(db_session, world, due)
    db_session.commit()
    assert row.status == "held"
    # Held to the quiet end: 09:00 EDT = 13:00 UTC.
    assert row.send_after == datetime(2026, 10, 25, 13, 0)
    job = db_session.get(Job, row.job_id)
    assert job.run_at == row.send_after


def test_quiet_hours_hold_auckland(client, world, db_session):
    _set_quiet(client, world, "21:00", "09:00", "Pacific/Auckland")
    # 14:00 UTC = 03:00 NZDT the next day — inside quiet.
    due = datetime(2026, 10, 24, 14, 0)
    row = _create_reminder_at(db_session, world, due)
    db_session.commit()
    assert row.status == "held"
    local_end = row.send_after.replace(tzinfo=timezone.utc).astimezone(
        ZoneInfo("Pacific/Auckland")
    )
    assert (local_end.hour, local_end.minute) == (9, 0)
    assert row.send_after > due


def test_quiet_hours_do_not_hold_other_kinds(client, world, db_session):
    _set_quiet(client, world, "21:00", "09:00", NY)
    row = notify_service.create_notification(
        db_session,
        group=_group(db_session, world["group"]["id"]),
        recipient_user=_user(db_session, world["ana"]["user_id"]),
        recipient_label=f"user:{world['ana']['user_id']}",
        kind="change.announcement",
        subject="Update: Harvest Supper",
        body_text="The supper moved.",
        event_id=world["event"]["id"],
        send_after=datetime(2026, 10, 25, 2, 30),
        now=now_utc(),
    )
    db_session.commit()
    assert row.status == "queued"  # Only reminders are held.


def test_urgent_cancellation_never_held(client, world, db_session):
    _set_quiet(client, world, "00:00", "23:59", NY)  # quiet nearly all day
    answer(client, world, world["ana"])
    cancelled = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/cancel",
        json={}, headers=bearer(world["owner"]),
    )
    assert cancelled.status_code in (200, 201), cancelled.text
    rows = (
        db_session.query(Notification)
        .filter(
            Notification.event_id == world["event"]["id"],
            Notification.kind == "change.cancellation",
            Notification.recipient_user_id == world["ana"]["user_id"],
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].urgent is True and rows[0].status == "queued"


# --- AI wording (B-103) ---------------------------------------------


class StubClient:
    """A model that returns a fixed JSON body."""

    def __init__(self, body):
        self._body = body

    def complete_json(self, *, prompt, schema, tier="spark"):
        return ModelResult(
            text="{}", model_id="stub", tier=tier,
            data={"subject": "A friendly nudge", "body": self._body},
        )

    def complete_text(self, *, prompt, tier="spark"):
        return ModelResult(text="", model_id="stub", tier=tier)


@pytest.fixture()
def ai_world(client, world, db_session):
    patched = client.patch(
        f"/api/v1/groups/{world['group']['id']}",
        json={"ai_reminder_wording": True},
        headers=bearer(world["owner"]),
    )
    assert patched.status_code == 200, patched.text
    yield world
    ai_client.set_client(None)


def test_ai_wording_ships_when_facts_survive(client, ai_world, db_session):
    template_start = "Friday 06 November 2026, 18:30 (America/New_York)"
    friendly = (
        f"Just a friendly note: the Harvest Supper is coming up on "
        f"{template_start} at Riverside Hall, 12 River Road — "
        f"bring a dish to share, if you can."
    )
    ai_client.set_client(StubClient(friendly))
    answer(client, ai_world, ai_world["ana"])
    row = reminders_for(db_session, ai_world["event"]["id"], ai_world["ana"]["user_id"])[0]
    assert row.wording_source == "ai"
    assert row.body_text == friendly
    assert row.template_body != friendly and "18:30" in row.template_body


def test_ai_wording_falls_back_when_a_fact_is_lost(client, ai_world, db_session):
    lossy = "Friendly reminder: supper soon, bring something tasty!"  # no time, no place
    ai_client.set_client(StubClient(lossy))
    answer(client, ai_world, ai_world["ana"])
    row = reminders_for(db_session, ai_world["event"]["id"], ai_world["ana"]["user_id"])[0]
    assert row.wording_source == "template_fallback"
    assert row.body_text == row.template_body
    assert "Riverside Hall" in row.body_text
