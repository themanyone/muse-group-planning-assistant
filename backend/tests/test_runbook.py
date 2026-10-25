"""RUNBOOK.md, reproduced (roadmap B-155).

Each test here is one runbook entry, broken on purpose and
then fixed by the runbook's own prescription:

1. mail down   — failing transport, failure recorded,
                 fix the transport, resend delivers;
2. AI quota    — budget spent, the queue says paused —
                 budget; raise the cap, extraction proceeds;
3. disk full   — a 1 MB filesystem under storage: writes
                 fail, /health says so, free space, healed;
4. token expired — a zero-minute access token is refused;
                 a normal login works;
5. job stuck   — a running row no worker will ever claim:
                 /health flags it, the requeue script returns
                 it, the worker drains it.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import bearer, make_group, make_settings, register, unique_email
from extraction_world import build_world, candidates_by_kind, run_extraction_job
from gpa.main import create_app
from gpa.models.core import Job
from gpa.services import jobs as job_service
from gpa.services import mailer
from test_groups import invite_and_accept
from test_notifications_core import invite, notifications, run_due_jobs

ROOT = Path(__file__).resolve().parent.parent
NY = "America/New_York"


@pytest.fixture(autouse=True)
def capture_transport(app):
    transport = mailer.CaptureTransport()
    mailer.set_transport(transport)
    yield transport
    mailer.set_transport(None)


# --- 1. mail down -------------------------------------------------


def test_runbook_mail_down_fix_and_resend(client, app, db_session, capture_transport):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    invite_and_accept(client, owner, group["id"], unique_email(), "member")
    event = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Harvest Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "timezone": NY, "place_name": "Riverside Hall",
        },
        headers=bearer(owner),
    )
    assert event.status_code == 201, event.text
    world = {"group": group, "owner": owner, "event": event.json()}

    mailer.set_transport(mailer.FailingTransport())
    made = invite(client, world, "guest@example.org")
    assert made["id"]  # the user flow survives a dead mail server
    row = notifications(db_session, kind="invite")[0]
    assert run_due_jobs(app, db_session, limit=1) == 1
    db_session.refresh(row)
    assert row.status == "failed" and "unreachable" in row.last_error

    # The runbook fix: correct the transport, resend from the
    # failures list. Delivery lands.
    mailer.set_transport(capture_transport)
    response = client.post(
        f"/api/v1/groups/{group['id']}/notifications/{row.id}/resend",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    assert run_due_jobs(app, db_session) >= 1
    db_session.refresh(row)
    assert row.status == "sent"


# --- 2. AI quota ---------------------------------------------------


def test_runbook_ai_quota_raise_the_cap_and_proceed(client, app, db_session):
    world = build_world(client, app, db_session, usage={"cost_micros": 500})
    gid = world["group"]["id"]
    capped = client.patch(
        f"/api/v1/groups/{gid}",
        json={"ai_daily_budget_micros": 400}, headers=bearer(world["owner"]),
    )
    assert capped.status_code == 200, capped.text

    run_extraction_job(client, app, db_session, world)
    status = client.get(
        f"/api/v1/groups/{gid}/candidates/status", headers=bearer(world["owner"])
    ).json()
    assert status["paused"] is True and status["pause_reason"] == "budget"
    assert set(candidates_by_kind(client, world)) == {"event"}

    # The runbook fix: raise the cap (or wait for the UTC
    # reset). A fresh run now proceeds past the gate.
    raised = client.patch(
        f"/api/v1/groups/{gid}",
        json={"ai_daily_budget_micros": 100000}, headers=bearer(world["owner"]),
    )
    assert raised.status_code == 200, raised.text
    run_extraction_job(client, app, db_session, world)
    status = client.get(
        f"/api/v1/groups/{gid}/candidates/status", headers=bearer(world["owner"])
    ).json()
    assert status["paused"] is False
    assert len(candidates_by_kind(client, world)) > 1


# --- 3. disk full ----------------------------------------------------


def test_runbook_disk_full_and_recovery(tmp_path):
    mount = tmp_path / "tiny"
    mount.mkdir()
    mounted = subprocess.run(
        ["mount", "-t", "tmpfs", "-o", "size=1M", "tmpfs", str(mount)],
        capture_output=True,
    )
    if mounted.returncode != 0:
        pytest.skip("cannot mount a scratch tmpfs in this environment")
    try:
        settings = make_settings(tmp_path, storage_dir=mount / "storage")
        app = create_app(settings)
        client = TestClient(app)
        owner = register(client)  # deep checks need a signed-in caller
        assert client.get(
            "/health", headers=bearer(owner)
        ).json()["checks"]["storage"]["status"] == "ok"

        storage = app.state.storage
        keys = []
        try:
            # Storage is content-addressed: the filler must
            # differ per put, or forty puts store one file.
            for index in range(40):  # 40 x 64 KiB > 1 MiB of filesystem
                keys.append(
                    storage.put_bytes(bytes([index]) * 65536, extension=".fill")
                )
        except OSError:
            pass
        health = client.get("/health", headers=bearer(owner)).json()
        assert health["checks"]["storage"]["status"] == "error"
        assert health["status"] == "degraded"

        for key in keys:  # the runbook fix: free the space
            storage.delete(key)
        assert client.get(
            "/health", headers=bearer(owner)
        ).json()["checks"]["storage"]["status"] == "ok"
    finally:
        subprocess.run(["umount", str(mount)], capture_output=True)


# --- 4. token expired -------------------------------------------------


def test_runbook_token_expired_and_fresh_login(tmp_path):
    # Negative, not zero: a zero-minute token minted and used
    # inside the same second can still validate (exp == now).
    # Born five minutes dead is expired in every clock's eyes.
    settings = make_settings(tmp_path, access_token_minutes=-5)
    client = TestClient(create_app(settings))
    session = register(client)
    refused = client.get("/api/v1/users/me", headers=bearer(session))
    assert refused.status_code == 401  # born expired: re-login is the fix

    healthy = TestClient(create_app(make_settings(tmp_path / "second")))
    fresh = register(healthy)
    ok = healthy.get("/api/v1/users/me", headers=bearer(fresh))
    assert ok.status_code == 200, ok.text


# --- 5. job stuck -----------------------------------------------------


def test_runbook_job_stuck_requeue_and_drain(client, app, db_session, settings):
    owner = register(client)  # deep /health checks need a signed-in caller
    job_service.register_handler("runbook_probe", lambda db, payload: None)
    job = Job(kind="runbook_probe", status="running", payload_json="{}",
              locked_by="worker-that-died")
    db_session.add(job)
    db_session.commit()
    stale = datetime.now(timezone.utc) - timedelta(minutes=90)
    job.updated_at = stale
    job.created_at = stale
    db_session.commit()

    health = client.get("/health", headers=bearer(owner)).json()
    assert health["checks"]["jobs"]["stuck_running"] >= 1
    assert health["status"] == "degraded"

    # The runbook fix is the operator's script, run as the
    # operator runs it: list first (changes nothing), then
    # --apply.
    env = dict(os.environ)
    env["GPA_DATABASE_URL"] = settings.database_url
    env["GPA_SECRET_KEY"] = "test-secret-for-tests-only"
    listed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "requeue_stuck_jobs.py")],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert listed.returncode == 0, listed.stderr
    assert job.id in listed.stdout
    db_session.refresh(job)
    assert job.status == "running"  # listing changed nothing

    applied = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "requeue_stuck_jobs.py"), "--apply"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert applied.returncode == 0, applied.stderr
    db_session.refresh(job)
    assert job.status == "queued" and job.locked_by is None

    assert job_service.process_one(db_session, settings, "test-worker") is True
    db_session.refresh(job)
    assert job.status == "done"
