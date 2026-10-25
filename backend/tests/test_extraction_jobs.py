"""Extraction jobs + budget tests (roadmap B-045, B-047).

Re-runs are additive history: new candidates land, undecided
old ones are superseded, decided ones are never touched, and the
audit shows both runs. A source still waiting on an earlier
stage is skipped, not failed. And the daily budget is a
soft-stop: at the cap the run defers to the next UTC day and the
queue's status says "paused — budget" in plain words.
"""

import pytest

from conftest import bearer
from extraction_world import build_world, candidates_by_kind, run_extraction_job
from gpa.models.core import AuditLog, Job
from gpa.models.groups import Candidate
from gpa.services import jobs as job_service


def _audit_actions(db_session, group_id):
    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.group_id == group_id)
        .order_by(AuditLog.created_at)
        .all()
    )
    return [row.action for row in rows]


def test_rerun_supersedes_undecided_and_keeps_decided(client, app, db_session):
    """B-045: approve the event, leave the task pending, re-run.
    The approved event is untouched history; the old task is
    superseded; fresh pending drafts of both kinds exist; the
    audit records both runs."""
    world = build_world(client, app, db_session)
    first_job = run_extraction_job(client, app, db_session, world)
    rows = candidates_by_kind(client, world)
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{rows['event']['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text

    run_extraction_job(client, app, db_session, world)
    db_session.expire_all()
    all_rows = (
        db_session.query(Candidate).filter(Candidate.group_id == gid).all()
    )
    by_id = {row.id: row for row in all_rows}
    assert by_id[rows["event"]["id"]].status == "approved"  # decided: untouched
    assert by_id[rows["task"]["id"]].status == "superseded"  # undecided: gives way
    fresh_events = [
        r for r in all_rows if r.kind == "event" and r.status == "pending"
    ]
    assert len(fresh_events) == 1
    assert fresh_events[0].job_id != first_job  # stamped with ITS run
    actions = _audit_actions(db_session, gid)
    assert actions.count("extraction.completed") == 2  # both runs on record
    assert "candidate.approved" in actions


def test_rerun_of_one_kind_leaves_other_kinds_alone(client, app, db_session):
    """A kinds-scoped re-run supersedes only its own kind."""
    world = build_world(client, app, db_session)
    run_extraction_job(client, app, db_session, world)
    rows = candidates_by_kind(client, world)
    run_extraction_job(client, app, db_session, world, kinds=["event"])
    db_session.expire_all()
    task = db_session.get(Candidate, rows["task"]["id"])
    assert task.status == "pending"  # not re-run, not superseded
    old_event = db_session.get(Candidate, rows["event"]["id"])
    assert old_event.status == "superseded"


def test_waiting_source_is_skipped_not_failed(client, app, db_session):
    """A source awaiting transcription has no text to draft from:
    the run records the skip and produces nothing — drafting from
    nothing would be inventing."""
    from gpa.services import extraction as extraction_service
    from gpa.services import sources as source_service

    world = build_world(client, app, db_session)
    source = source_service.create_source(
        db_session, actor_label="user:test", group=world["group_row"],
        kind="voice", title="A voice note", chunks=[{"text": "awaiting"}],
        status="awaiting_transcription", stage="awaiting_transcription",
    )
    db_session.commit()
    summary = extraction_service.run_extraction(
        db_session, group=world["group_row"], source=source,
        kinds=["event"], requested_by="user:test", job_id=None,
    )
    assert summary["candidates"] == 0
    assert summary["skipped"] == "awaiting_transcription"
    assert "extraction.skipped" in _audit_actions(db_session, world["group"]["id"])


def test_unknown_kind_is_refused_at_the_door(client, app, db_session):
    world = build_world(client, app, db_session)
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/sources/{world['source_id']}/extract",
        json={"kinds": ["poem"]}, headers=bearer(world["owner"]),
    )
    assert response.status_code in (400, 422)
    assert response.json()["code"] == "validation_error"


def test_budget_pause_defers_the_run_and_says_so(client, app, db_session):
    """B-047's acceptance: the cap is spent mid-run (the event
    step costs 500 of a 400 budget), the remaining kinds never
    start, a deferred job waits for the next UTC day, the audit
    names the deferral, and the queue status reads paused —
    budget, plainly."""
    world = build_world(client, app, db_session, usage={"cost_micros": 500})
    gid = world["group"]["id"]
    # Set the cap through the real settings route (admin+).
    updated = client.patch(
        f"/api/v1/groups/{gid}",
        json={"ai_daily_budget_micros": 400}, headers=bearer(world["owner"]),
    )
    assert updated.status_code == 200, updated.text

    run_extraction_job(client, app, db_session, world)
    db_session.expire_all()

    rows = candidates_by_kind(client, world)
    assert set(rows) == {"event"}  # only the step that ran
    jobs = db_session.query(Job).filter(Job.kind == "extract").all()
    deferred = [j for j in jobs if j.status == "queued"]
    assert len(deferred) == 1  # the run, postponed — not dropped
    assert "extraction.deferred_budget" in _audit_actions(db_session, gid)

    status = client.get(
        f"/api/v1/groups/{gid}/candidates/status", headers=bearer(world["owner"])
    )
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["paused"] is True
    assert body["pause_reason"] == "budget"
    assert body["spent_today_micros"] == 500
    assert body["budget_micros"] == 400
    assert body["pending_count"] == 1


def test_no_budget_means_never_paused(client, app, db_session):
    """No cap set: the status endpoint runs, unpaused, with the
    day's spend still honestly reported (here, zero-cost fixture
    calls)."""
    world = build_world(client, app, db_session)
    run_extraction_job(client, app, db_session, world)
    status = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates/status",
        headers=bearer(world["owner"]),
    ).json()
    assert status["paused"] is False
    assert status["pause_reason"] is None
    assert status["budget_micros"] is None
    assert status["pending_count"] == 4
