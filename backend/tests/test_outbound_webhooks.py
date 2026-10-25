"""Outbound webhooks (roadmap B-128): delivery + retry golden.

A registered endpoint hears the group's acts — signed with
the secret shown once at registration, verifiable with the
inbound door's own scheme — and the record is inspectable:

- publish → delivered, signature checked, row says sent;
- a receiver answering 500 → the attempt is recorded, the
  job retries, and a later success lands (attempts = 2);
- a receiver that never answers well → the delivery dies
  WITH its job at the attempt cap, marked dead, in words;
- subscription is real: an unsubscribed kind makes no
  delivery at all.

No byte leaves the process: the transport is an httpx
MockTransport installed in the outbound registry.
"""

import json

import httpx
import pytest

from conftest import bearer, make_group, register
from gpa.models.core import Job
from gpa.services import jobs as job_service
from gpa.services import outbound as outbound_service
from gpa.services import webhooks_in as webhook_service


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    outbound_service.set_transport(None)


class Capture:
    """A receiver with a memory and a mood (status to answer)."""

    def __init__(self, status: int = 200):
        self.status = status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": self.status < 300})


def world(client, capture, *, events=("event.published", "candidate.pending")):
    outbound_service.set_transport(httpx.MockTransport(capture.handler))
    owner = register(client)
    group = make_group(client, owner)
    created = client.post(
        f"/api/v1/groups/{group['id']}/outbound-webhooks",
        json={"url": "https://tools.example.invalid/hook", "events": list(events)},
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return owner, group, created.json()


def publish_event(client, owner, group, title="Autumn Social"):
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": title,
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "place_name": "Greenfield Farm",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return created.json()


def deliveries(client, owner, group, webhook):
    response = client.get(
        f"/api/v1/groups/{group['id']}/outbound-webhooks/{webhook['id']}/deliveries",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_delivery_is_signed_and_inspectable(client, app, db_session):
    capture = Capture()
    owner, group, webhook = world(client, capture)
    assert webhook["signing_secret"]  # shown once, here only

    publish_event(client, owner, group)
    rows = deliveries(client, owner, group, webhook)
    assert len(rows) == 1
    assert rows[0]["event_kind"] == "event.published"
    assert rows[0]["status"] == "queued"

    assert job_service.process_one(db_session, app.state.settings, "test") is True
    assert len(capture.requests) == 1
    request = capture.requests[0]
    assert request.headers["x-gpa-event"] == "event.published"
    # The signature verifies with the shown-once secret, in
    # the inbound door's own scheme.
    expected = webhook_service.signature_for(
        webhook["signing_secret"],
        request.headers["x-gpa-timestamp"],
        request.content,
    )
    assert request.headers["x-gpa-signature"] == expected
    payload = json.loads(request.content)
    assert payload["event"] == "event.published"
    assert payload["data"]["title"] == "Autumn Social"

    rows = deliveries(client, owner, group, webhook)
    assert rows[0]["status"] == "sent"
    assert rows[0]["attempts"] == 1
    assert rows[0]["response_status"] == 200
    assert rows[0]["delivered_at"] is not None

    # The list view never shows the secret again.
    listed = client.get(
        f"/api/v1/groups/{group['id']}/outbound-webhooks", headers=bearer(owner)
    ).json()
    assert listed[0]["signing_secret"] is None


def test_retry_after_a_500_then_success(client, app, db_session):
    capture = Capture(status=500)
    owner, group, webhook = world(client, capture)
    publish_event(client, owner, group)

    assert job_service.process_one(db_session, app.state.settings, "test") is True
    rows = deliveries(client, owner, group, webhook)
    assert rows[0]["status"] == "failed"
    assert rows[0]["attempts"] == 1
    assert "HTTP 500" in rows[0]["last_error"]

    capture.status = 200  # the receiver recovers
    assert job_service.process_one(db_session, app.state.settings, "test") is True
    rows = deliveries(client, owner, group, webhook)
    assert rows[0]["status"] == "sent"
    assert rows[0]["attempts"] == 2
    assert len(capture.requests) == 2


def test_dead_at_the_attempt_cap(client, app, db_session):
    capture = Capture(status=500)
    owner, group, webhook = world(client, capture)
    publish_event(client, owner, group)
    # One attempt only, for this test: the job's cap is data.
    job = db_session.query(Job).filter(Job.kind == "webhook_deliver").one()
    job.max_attempts = 1
    db_session.commit()

    assert job_service.process_one(db_session, app.state.settings, "test") is True
    rows = deliveries(client, owner, group, webhook)
    assert rows[0]["status"] == "dead"
    assert "HTTP 500" in rows[0]["last_error"]


def test_subscription_and_candidate_pending(client, app, db_session):
    capture = Capture()
    owner, group, webhook = world(
        client, capture, events=("event.published",)
    )
    # candidate.pending is NOT subscribed: drafting one
    # makes no delivery for this endpoint.
    drafted = client.post(
        f"/api/v1/groups/{group['id']}/candidates",
        json={"kind": "event", "title": "A drafted idea"},
        headers=bearer(owner),
    )
    assert drafted.status_code == 201, drafted.text
    assert deliveries(client, owner, group, webhook) == []

    # A second endpoint subscribed to it hears it at once.
    other = client.post(
        f"/api/v1/groups/{group['id']}/outbound-webhooks",
        json={
            "url": "https://tools.example.invalid/queue-watch",
            "events": ["candidate.pending"],
        },
        headers=bearer(owner),
    ).json()
    drafted = client.post(
        f"/api/v1/groups/{group['id']}/candidates",
        json={"kind": "task", "title": "Bring the urn"},
        headers=bearer(owner),
    )
    assert drafted.status_code == 201, drafted.text
    rows = deliveries(client, owner, group, other)
    assert len(rows) == 1
    assert rows[0]["event_kind"] == "candidate.pending"
    assert job_service.process_one(db_session, app.state.settings, "test") is True
    payload = json.loads(capture.requests[-1].content)
    assert payload["data"]["title"] == "Bring the urn"
