"""The weekly digest (roadmap B-099).

A golden week: one published event, one unclaimed task, one
open question, one open poll — the digest must carry all four
sections in BOTH renderings, store both on the Digest row, and
deliver one notification per member through the outbox. The
alt-text law check runs over the rendered HTML (the shipped
templates carry no images; the check is executable anyway).
Running a digest anchors the weekly chain; saving digest
settings re-anchors it.
"""

from datetime import datetime, timedelta, timezone

import pytest

import json

from conftest import bearer, make_group, register, unique_email
from gpa.errors import AppError
from gpa.models.core import Job
from gpa.models.notifications import Digest, Notification
from gpa.services import digests as digest_service
from test_groups import invite_and_accept

NY = "America/New_York"


def job_group(job):
    # Payloads are sealed at rest (B-166); payload_of is the
    # reading law the product itself uses.
    from gpa.services import jobs as job_service

    return job_service.payload_of(job).get("group_id")


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    ana = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    # An event two days out — inside this week's window and
    # the week ahead alike, whatever day the suite runs.
    start = datetime.now(timezone.utc) + timedelta(days=2)
    end = start + timedelta(hours=2)
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Golden Week Gathering",
            "starts_at": start.isoformat(), "ends_at": end.isoformat(),
            "timezone": NY, "place_name": "The Long Hall",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    task = client.post(
        f"/api/v1/groups/{group['id']}/tasks",
        json={"title": "Bring the mead"}, headers=bearer(owner),
    )
    assert task.status_code == 201, task.text
    question = client.post(
        f"/api/v1/groups/{group['id']}/questions",
        json={"text": "Who brings the cups?"}, headers=bearer(owner),
    )
    assert question.status_code == 201, question.text
    poll = client.post(
        f"/api/v1/groups/{group['id']}/polls",
        json={"question": "Friday or Saturday?", "kind": "choice",
              "options": [{"label": "Friday"}, {"label": "Saturday"}]},
        headers=bearer(owner),
    )
    assert poll.status_code == 201, poll.text
    return {"group": group, "owner": owner, "ana": ana, "event": created.json()}


def test_golden_week_renders_both_formats(client, world, db_session):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/digest/run",
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    digest = response.json()
    for body, marks in (
        (digest["body_text"], ["Coming up", "Tasks needing an owner",
                               "Open questions", "Polls awaiting your vote"]),
        (digest["body_html"], ["<h2>Coming up</h2>", "<h2>Tasks needing an owner</h2>",
                                "<h2>Open questions</h2>", "<h2>Polls awaiting your vote</h2>"]),
    ):
        for mark in marks:
            assert mark in body, mark
        assert "Golden Week Gathering" in body
        assert "Bring the mead" in body
        assert "Who brings the cups?" in body
        assert "Friday or Saturday?" in body
    assert digest["sent_at"] is not None

    # Stored, and listed back identically.
    rows = db_session.query(Digest).filter(Digest.group_id == world["group"]["id"]).all()
    assert len(rows) == 1 and rows[0].body_text == digest["body_text"]
    listed = client.get(
        f"/api/v1/groups/{world['group']['id']}/digests", headers=bearer(world["owner"])
    )
    assert listed.status_code == 200 and listed.json()[0]["id"] == digest["id"]

    # One digest notification per active member, through the outbox.
    notes = (
        db_session.query(Notification)
        .filter(Notification.group_id == world["group"]["id"], Notification.kind == "digest")
        .all()
    )
    assert {n.recipient_user_id for n in notes} == {
        world["owner"]["user_id"], world["ana"]["user_id"],
    }


def test_digest_run_anchors_the_weekly_chain(client, world, db_session):
    client.post(
        f"/api/v1/groups/{world['group']['id']}/digest/run",
        headers=bearer(world["owner"]),
    )
    chains = [
        job for job in db_session.query(Job).filter(Job.kind == "digest_run").all()
        if job_group(job) == world["group"]["id"] and job.status == "queued"
    ]
    assert len(chains) == 1
    # Next Monday 08:00 group-local, in UTC terms.
    assert chains[0].run_at > datetime.now(timezone.utc).replace(tzinfo=None)


def test_saving_digest_settings_reanchors(client, world, db_session):
    patched = client.patch(
        f"/api/v1/groups/{world['group']['id']}",
        json={"digest_weekday": 4, "digest_time": "17:30"},
        headers=bearer(world["owner"]),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["digest_weekday"] == 4
    assert patched.json()["digest_time"] == "17:30"
    chains = [
        job for job in db_session.query(Job).filter(Job.kind == "digest_run").all()
        if job_group(job) == world["group"]["id"] and job.status == "queued"
    ]
    assert len(chains) == 1


def test_alt_text_law_is_executable():
    digest_service.assert_alt_text('<p><img src="x.png" alt="The long hall"></p>')
    with pytest.raises(AppError):
        digest_service.assert_alt_text('<p><img src="x.png"></p>')
    with pytest.raises(AppError):
        digest_service.assert_alt_text('<p><img src="x.png" alt=""></p>')
