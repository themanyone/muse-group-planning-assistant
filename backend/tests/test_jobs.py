"""Job chassis acceptance (B-008): enqueue, claim-by-update with no
double-run, retry with backoff, dead-letter, worker survival, and
the status route."""

from conftest import bearer, register
from gpa.services import jobs as job_service


def test_job_runs_to_done(app, db_session):
    """A registered handler runs; the job ends done."""
    seen = []
    job_service.register_handler("test.echo", lambda db, payload: seen.append(payload))
    job = job_service.enqueue(db_session, app.state.settings, kind="test.echo", payload={"n": 1})
    assert job_service.process_one(db_session, app.state.settings, "w1") is True
    assert seen == [{"n": 1}]
    assert job_service.get_job(db_session, job.id).status == "done"


def test_failing_job_retries_then_dead_letters(app, db_session):
    """A handler that always crashes: retried, then dead — and the
    worker processes the NEXT job fine afterwards (B-008)."""

    def boom(db, payload):
        raise RuntimeError("handler exploded")

    job_service.register_handler("test.boom", boom)
    job_service.register_handler("test.ok", lambda db, payload: None)
    job = job_service.enqueue(
        db_session, app.state.settings, kind="test.boom", max_attempts=2
    )
    settings = app.state.settings
    assert job_service.process_one(db_session, settings, "w1") is True
    middle = job_service.get_job(db_session, job.id)
    assert middle.status == "queued" and middle.attempts == 1
    assert job_service.process_one(db_session, settings, "w1") is True
    final = job_service.get_job(db_session, job.id)
    assert final.status == "dead"
    assert "handler exploded" in final.last_error

    good = job_service.enqueue(db_session, settings, kind="test.ok")
    assert job_service.process_one(db_session, settings, "w1") is True
    assert job_service.get_job(db_session, good.id).status == "done"


def test_claim_by_update_no_double_run(app, db_session):
    """Two workers, one job: exactly one claim wins."""
    job_service.register_handler("test.once", lambda db, payload: None)
    job = job_service.enqueue(db_session, app.state.settings, kind="test.once")
    first = job_service.claim_next(db_session, "worker-a")
    second = job_service.claim_next(db_session, "worker-b")
    assert first is not None and first.id == job.id
    assert second is None


def test_job_status_route(client, app, db_session):
    """GET /api/v1/jobs/{id} answers for an authenticated caller and
    401s without credentials."""
    job_service.register_handler("test.route", lambda db, payload: None)
    job = job_service.enqueue(db_session, app.state.settings, kind="test.route")
    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 401
    session = register(client)
    response = client.get(f"/api/v1/jobs/{job.id}", headers=bearer(session))
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert client.get("/api/v1/jobs/nope", headers=bearer(session)).status_code == 404
