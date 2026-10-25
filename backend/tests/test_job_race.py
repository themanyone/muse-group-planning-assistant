"""Multi-instance job races (B-157): the D-09 claim protocol
under N competing workers.

The chassis' promise is exactly-once: however many worker
processes a deployment runs, each job's handler runs once.
These tests race REAL sessions against the REAL database (the
suite's SQLite file, or PostgreSQL in parity mode) — four
workers, one queue, every run recorded as an audit row in the
handler's own transaction, so a double-run would leave two
rows and fail the count.

The audit row is the probe because it is engine-agnostic and
transactional with the handler: memory lists would prove
nothing about commits, and a test-only table would need a
migration. ``action='race.ran'``, ``entity_id`` = the job's
unique token.
"""

import threading
import time

from gpa.models.core import AuditLog, Job
from gpa.services import audit, jobs as job_service

WORKERS = 4
JOB_COUNT = 24


def _race_handler(db, payload):
    """Record the run, in the handler's transaction.

    The small sleep widens the race window on purpose: more
    overlap between claim and finish means more chances for a
    broken claim to double-run — which is the point of racing.
    """
    time.sleep(0.005)
    audit.record(
        db, actor="test:race", action="race.ran",
        entity_type="job", entity_id=payload["token"],
    )


def _run_rows(db, token: str) -> int:
    return (
        db.query(AuditLog)
        .filter(AuditLog.action == "race.ran", AuditLog.entity_id == token)
        .count()
    )


def test_four_workers_no_double_runs(app, db_session):
    """4 workers drain 24 jobs; every handler ran exactly once."""
    job_service.register_handler("test.race", _race_handler)
    settings = app.state.settings
    tokens = [f"race-{index}" for index in range(JOB_COUNT)]
    for token in tokens:
        job_service.enqueue(
            db_session, settings, kind="test.race", payload={"token": token}
        )

    failures: list[BaseException] = []

    def drain(worker_id: str) -> None:
        """One worker process's loop, on its OWN session —
        the multi-instance shape: separate connections, one
        shared queue, claim-by-update as the only referee."""
        session = app.state.db.session()
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if job_service.process_one(session, settings, worker_id):
                    continue
                # Nothing claimable this instant: stop only
                # when nothing is left queued at all (a peer
                # may be mid-run on the rest).
                remaining = (
                    session.query(Job)
                    .filter(Job.status == "queued")
                    .count()
                )
                session.rollback()
                if remaining == 0:
                    return
                time.sleep(0.01)
            raise TimeoutError("race drain did not finish in time")
        except BaseException as err:  # surfaced by the assert below
            failures.append(err)
        finally:
            session.close()

    threads = [
        threading.Thread(target=drain, args=(f"racer-{index}",))
        for index in range(WORKERS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=150)

    assert not failures, failures
    for token in tokens:
        assert _run_rows(db_session, token) == 1, token
    total = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "race.ran")
        .count()
    )
    assert total == JOB_COUNT
    statuses = {
        job.status
        for job in db_session.query(Job).filter(Job.kind == "test.race").all()
    }
    assert statuses == {"done"}


def test_claim_storm_on_one_job_has_exactly_one_winner(app, db_session):
    """Four workers claim the same single job at the same
    instant (a barrier release): one wins, three get None —
    nobody errors, nobody double-claims."""
    job_service.register_handler("test.storm", lambda db, payload: None)
    job = job_service.enqueue(db_session, app.state.settings, kind="test.storm")

    barrier = threading.Barrier(WORKERS)
    results: list = []
    failures: list[BaseException] = []

    def claim(index: int) -> None:
        session = app.state.db.session()
        try:
            barrier.wait(timeout=30)
            results.append(job_service.claim_next(session, f"storm-{index}"))
        except BaseException as err:
            failures.append(err)
        finally:
            session.close()

    threads = [
        threading.Thread(target=claim, args=(index,)) for index in range(WORKERS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not failures, failures
    winners = [claimed for claimed in results if claimed is not None]
    assert len(winners) == 1
    assert winners[0].id == job.id


def test_claim_scans_past_a_contended_head(app, db_session):
    """The B-157 scan: worker A holds the oldest job running;
    worker B's claim must land the NEXT job, not report the
    queue empty while work waits behind the contended head."""
    job_service.register_handler("test.scan", lambda db, payload: None)
    settings = app.state.settings
    first = job_service.enqueue(db_session, settings, kind="test.scan")
    second = job_service.enqueue(db_session, settings, kind="test.scan")

    session_a = app.state.db.session()
    session_b = app.state.db.session()
    try:
        claimed_first = job_service.claim_next(session_a, "worker-a")
        assert claimed_first is not None and claimed_first.id == first.id
        claimed_second = job_service.claim_next(session_b, "worker-b")
        assert claimed_second is not None and claimed_second.id == second.id
    finally:
        session_a.close()
        session_b.close()
