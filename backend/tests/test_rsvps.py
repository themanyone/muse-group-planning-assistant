"""RSVPs (roadmap B-061).

One answer per person; capacity seats parties in arrival
order; the overflow waits IN ORDER, as data; a freed seat
promotes the next in line and enqueues their notification job
— which the real job chassis can run to success.
"""

import json

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.core import Job
from gpa.services import jobs as job_service
from test_groups import invite_and_accept

NY = "America/New_York"


def promotion_jobs(db_session):
    """The outbox jobs carrying a waitlist promotion. (Phase 8:
    the ``notify`` queue carries every notification now —
    reminders included — so promotions are the jobs whose
    payload kind is ``rsvp.promoted``.)"""
    return [
        job
        for job in db_session.query(Job).filter(Job.kind == "notify").all()
        if json.loads(job.payload_json).get("kind") == "rsvp.promoted"
    ]


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    people = {"owner": owner}
    for name in ("ana", "ben", "cleo", "dan"):
        people[name] = invite_and_accept(
            client, owner, group["id"], unique_email(), "member"
        )
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Tiny Supper", "capacity": 3,
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return {"group": group, "people": people, "event": created.json()}


def answer(client, world, who, status="coming", guests=0):
    response = client.put(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/rsvp",
        json={"status": status, "guests": guests},
        headers=bearer(who),
    )
    assert response.status_code == 200, response.text
    return response.json()


def listing(client, world):
    response = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/rsvps",
        headers=bearer(world["people"]["owner"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_waitlist_order_and_promotion_with_notification(client, app, world, db_session):
    ppl = world["people"]
    first = answer(client, world, ppl["ana"])                    # seat 1
    assert first["waitlisted"] is False
    second = answer(client, world, ppl["ben"], guests=1)         # seats 2–3
    assert second["waitlisted"] is False
    third = answer(client, world, ppl["cleo"])                   # room full
    assert third["waitlisted"] is True and third["waitlist_position"] == 1
    fourth = answer(client, world, ppl["dan"])
    assert fourth["waitlisted"] is True and fourth["waitlist_position"] == 2

    summary = listing(client, world)["summary"]
    assert summary["confirmed_seats"] == 3
    assert summary["waitlisted_parties"] == 2

    # Ben can't come after all: two seats free, and the line
    # moves in order — Cleo takes one, Dan takes the other.
    answer(client, world, ppl["ben"], status="cant")
    rows = {row["principal_label"]: row for row in listing(client, world)["rsvps"]}
    assert rows[f"user:{ppl['cleo']['user_id']}"]["waitlisted"] is False
    assert rows[f"user:{ppl['dan']['user_id']}"]["waitlisted"] is False

    # The promotions produced notification jobs, addressed to
    # the promoted, and the chassis runs them to success.
    jobs = promotion_jobs(db_session)
    assert jobs, "promotion must enqueue a notification job"
    payloads = [json.loads(job.payload_json) for job in jobs]
    assert any(p["to"] == f"user:{ppl['cleo']['user_id']}" for p in payloads)
    assert all(p["kind"] == "rsvp.promoted" for p in payloads)
    while job_service.process_one(db_session, app.state.settings, "test-worker"):
        pass
    db_session.expire_all()
    for job in jobs:
        assert db_session.get(Job, job.id).status == "done"


def test_waitlist_is_strictly_next_in_line(client, world, db_session):
    """A party that does not fit blocks the line: nobody behind
    it jumps forward, even when their smaller party would fit —
    the freed seat is offered to the next in line, in order."""
    ppl = world["people"]
    answer(client, world, ppl["ana"], guests=1)   # 2 of 3 seats
    answer(client, world, ppl["ben"])             # last seat
    big = answer(client, world, ppl["cleo"], guests=1)   # party of 2: waits
    assert big["waitlisted"] is True and big["waitlist_position"] == 1
    small = answer(client, world, ppl["dan"])            # party of 1: waits
    assert small["waitlist_position"] == 2

    answer(client, world, ppl["ben"], status="cant")     # one seat frees
    rows = {row["principal_label"]: row for row in listing(client, world)["rsvps"]}
    # Cleo's party of 2 does not fit in one seat — and Dan does
    # NOT jump her, though his party of 1 would.
    assert rows[f"user:{ppl['cleo']['user_id']}"]["waitlisted"] is True
    assert rows[f"user:{ppl['cleo']['user_id']}"]["waitlist_position"] == 1
    assert rows[f"user:{ppl['dan']['user_id']}"]["waitlisted"] is True
    assert rows[f"user:{ppl['dan']['user_id']}"]["waitlist_position"] == 2
    assert promotion_jobs(db_session) == []  # nobody was promoted, nobody told

    # Ana drops her guest: a second seat frees, Cleo's party
    # fits, and she is promoted by SOMEONE ELSE's act — which is
    # exactly when the notification job must exist. (A promotion
    # caused by your own answer change needs no notification:
    # your response already told you.)
    answer(client, world, ppl["ana"], guests=0)
    rows = {row["principal_label"]: row for row in listing(client, world)["rsvps"]}
    assert rows[f"user:{ppl['cleo']['user_id']}"]["waitlisted"] is False
    assert rows[f"user:{ppl['dan']['user_id']}"]["waitlist_position"] == 1
    jobs = promotion_jobs(db_session)
    assert len(jobs) == 1
    payload = json.loads(jobs[0].payload_json)
    assert payload["to"] == f"user:{ppl['cleo']['user_id']}"


def test_raising_capacity_promotes_the_line(client, world, db_session):
    """An organiser's capacity PATCH re-runs the waitlist: the
    room got bigger, so the next in line is seated + notified."""
    ppl = world["people"]
    answer(client, world, ppl["ana"], guests=2)   # all 3 seats
    waiting = answer(client, world, ppl["ben"])
    assert waiting["waitlisted"] is True
    gid = world["group"]["id"]
    event = world["event"]
    patched = client.patch(
        f"/api/v1/groups/{gid}/events/{event['id']}",
        json={"capacity": 4},
        headers={**bearer(ppl["owner"]), "If-Match": '"v1"'},
    )
    assert patched.status_code == 200, patched.text
    rows = {row["principal_label"]: row for row in listing(client, world)["rsvps"]}
    assert rows[f"user:{ppl['ben']['user_id']}"]["waitlisted"] is False
    jobs = promotion_jobs(db_session)
    assert len(jobs) == 1


def test_upsert_and_withdrawal(client, world):
    ppl = world["people"]
    answer(client, world, ppl["ana"], status="maybe")
    answer(client, world, ppl["ana"], status="coming")
    rows = listing(client, world)["rsvps"]
    assert len(rows) == 1 and rows[0]["status"] == "coming"
    removed = client.delete(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/rsvp",
        headers=bearer(ppl["ana"]),
    )
    assert removed.status_code == 204
    assert listing(client, world)["rsvps"] == []


def test_rsvp_refusals(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["people"]["owner"])
    cancelled = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Doomed", "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=headers,
    ).json()
    client.post(f"/api/v1/groups/{gid}/events/{cancelled['id']}/cancel",
                json={"reason": "rain"}, headers=headers)
    response = client.put(
        f"/api/v1/groups/{gid}/events/{cancelled['id']}/rsvp",
        json={"status": "coming"}, headers=bearer(world["people"]["ana"]),
    )
    assert response.status_code == 409
    bad = client.put(
        f"/api/v1/groups/{gid}/events/{world['event']['id']}/rsvp",
        json={"status": "definitely"}, headers=bearer(world["people"]["ana"]),
    )
    assert bad.status_code == 422
