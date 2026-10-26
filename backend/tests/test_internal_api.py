"""Internal API tests — the envelope, the registry, the door (B-162).

The promise these guard (§6A.4, B-162's acceptance): cross-
module operations travel in one typed envelope, are checked
against a registry of operation → owner → required scope, and
behave identically to the direct calls they replaced — while a
caller without the scope, or a name nobody registered, is
refused in the system's one error language.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.errors import AppError
from gpa.internal import registry
from gpa.internal.client import call_internal
from gpa.internal.envelope import InternalRequest, InternalResult
from gpa.models.core import Job
from gpa.models.groups import Candidate
from gpa.models.notifications import Notification

NY = "America/New_York"


def test_registry_names_the_roadmaps_five_families():
    """The registry maps operation → owning module → required
    scope for the five families the roadmap names (candidates,
    events, notify, AI jobs, integrations), plus the two
    supporting operations those families need."""
    from gpa.internal.handlers import register_all

    register_all()
    expected = {
        "candidates.create": ("extraction", "candidates:write"),
        "events.publish": ("events", "events:publish"),
        "notify.request": ("notifications", "notify:write"),
        "notify.rsvp_promotion": ("notifications", "notify:write"),
        "jobs.enqueue": ("jobs", "jobs:write"),
        "ai.submit_job": ("jobs", "ai:submit"),
        "integrations.emit": ("integrations", "integrations:write"),
    }
    for name, (owner, scope) in expected.items():
        operation = registry.get_operation(name)
        assert operation is not None, name
        assert operation.owner == owner
        assert operation.required_scope == scope
        assert name in registry.HANDLERS


def test_envelope_carries_the_roadmaps_fields():
    """The envelope is the §6A.4 shape — operation, caller,
    scope, payload, nonce, timestamp, signature — and it
    round-trips through JSON unchanged, because the wire form
    IS this form (B-163)."""
    envelope = InternalRequest(
        operation="notify.request",
        caller="events",
        scope="notify:write",
        payload={"kind": "change.cancellation"},
        nonce="fixed-nonce-for-the-test",
        timestamp="2026-10-25T12:00:00+00:00",
    )
    assert envelope.signature is None  # local profile; B-164 signs
    restored = InternalRequest.model_validate_json(envelope.model_dump_json())
    assert restored == envelope
    result = InternalResult(ok=True, operation="notify.request", data={"created_ids": ["x"]})
    assert result.error is None


def test_unknown_operation_is_refused(db_session):
    """An unregistered name is refused, in problem language."""
    with pytest.raises(AppError) as caught:
        call_internal(db_session, caller="events", operation="nope.nope")
    assert caught.value.code == "internal_unknown_operation"


def test_scope_denial_is_refused(db_session):
    """A caller without the operation's scope is refused —
    sources may submit AI jobs, but it may not write
    notifications; a caller nobody granted anything gets
    nothing."""
    with pytest.raises(AppError) as caught:
        call_internal(
            db_session,
            caller="sources",
            operation="notify.request",
            payload={
                "kind": "x",
                "subject": "x",
                "body_text": "x",
                "audience": {"label": "user:nobody"},
            },
        )
    assert caught.value.code == "internal_scope_denied"
    with pytest.raises(AppError) as caught:
        call_internal(
            db_session, caller="stranger", operation="jobs.enqueue",
            payload={"kind": "digest_run"},
        )
    assert caught.value.code == "internal_scope_denied"


def test_jobs_enqueue_and_ai_submit(db_session):
    """Job submission through the envelope commits a real job
    row; the AI door refuses non-AI kinds."""
    data = call_internal(
        db_session, caller="integrations", operation="jobs.enqueue",
        payload={"kind": "digest_run", "payload": {"group_id": "g1"}},
    )
    job = db_session.get(Job, data["job_id"])
    assert job is not None and job.kind == "digest_run"

    data = call_internal(
        db_session, caller="extraction", operation="ai.submit_job",
        payload={"kind": "extract", "payload": {"source_id": "s1"}},
    )
    assert db_session.get(Job, data["job_id"]).kind == "extract"

    with pytest.raises(AppError) as caught:
        call_internal(
            db_session, caller="extraction", operation="ai.submit_job",
            payload={"kind": "digest_run"},
        )
    assert caught.value.code == "internal_bad_payload"


def test_candidates_create_writes_the_row(client, db_session):
    """Candidate creation through the envelope writes the one
    shared row shape: draft and flags as JSON, id returned."""
    owner = register(client)
    group = make_group(client, owner)
    data = call_internal(
        db_session, caller="groups", operation="candidates.create",
        payload={
            "group_id": group["id"],
            "kind": "event",
            "title": "A drafted thing",
            "created_by": f"seat:{owner['user_id']}",
            "draft": {"title": "A drafted thing"},
            "flags": ["needs_review"],
        },
    )
    row = db_session.get(Candidate, data["candidate_id"])
    assert row is not None
    assert row.status == "pending"
    assert row.draft_json == '{"title": "A drafted thing"}'
    assert row.flags_json == '["needs_review"]'


def test_notify_request_writes_outbox_rows(client, db_session):
    """A notification request through the envelope writes the
    outbox row (and only the row — sending is the worker's)."""
    owner = register(client)
    group = make_group(client, owner)
    data = call_internal(
        db_session, caller="events", operation="notify.request",
        payload={
            "group_id": group["id"],
            "audience": {
                "user_id": owner["user_id"],
                "label": f"user:{owner['user_id']}",
                "email": owner["email"],
            },
            "kind": "change.cancellation",
            "subject": "Cancelled: Supper",
            "body_text": "It is off.",
        },
    )
    assert len(data["created_ids"]) == 1
    row = db_session.get(Notification, data["created_ids"][0])
    assert row is not None and row.kind == "change.cancellation"


def test_events_publish_through_the_envelope(client, db_session):
    """Event publication — the fifth family — works through
    the same door: a draft goes published, version stamped.
    (The HTTP door creates events already published, so the
    draft here is written as a row, the way an approved
    candidate's mint begins.)"""
    from datetime import datetime, timezone

    from gpa.models.events import Event

    owner = register(client)
    group = make_group(client, owner)
    draft = Event(
        group_id=group["id"],
        title="Harvest Supper",
        starts_at=datetime(2026, 11, 6, 23, 30, tzinfo=timezone.utc),
        ends_at=datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc),
        timezone=NY,
        place_name="Riverside Hall",
        status="draft",
        created_by=f"user:{owner['user_id']}",
    )
    db_session.add(draft)
    db_session.commit()
    data = call_internal(
        db_session, caller="core", operation="events.publish",
        payload={
            "group_id": group["id"],
            "event_id": draft.id,
            "actor": f"user:{owner['user_id']}",
        },
    )
    assert data["event_id"] == draft.id
    view = client.get(
        f"/api/v1/groups/{group['id']}/events/{draft.id}", headers=bearer(owner)
    )
    assert view.json()["status"] == "published"


def test_integrations_emit_counts_deliveries(client, db_session):
    """An integration call with no subscribed endpoints is a
    truthful zero, not an error."""
    owner = register(client)
    group = make_group(client, owner)
    data = call_internal(
        db_session, caller="events", operation="integrations.emit",
        payload={"group_id": group["id"], "kind": "event.published", "data": {"x": 1}},
    )
    assert data == {"deliveries": 0}
