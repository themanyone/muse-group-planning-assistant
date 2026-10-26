"""Queue backends behind the interface (B-157).

The database backend is proven by delegation (a submission is
a job row the chassis runs). The arq adapter is proven at its
boundary with an injected pool double — this environment runs
no Redis server, and the adapter's contract is exactly what
the double records: the function name every GPA job travels
under, the kind, the payload as a JSON string, and arq's
defer/try options translated from ours. The dispatch half
(run_gpa_job → the shared handler registry) runs for real
against a real session. The end-to-end live leg (real Redis,
real arq worker) ran once at the slice's proof — TEST_REPORT's
Phase 16 addendum carries it — and stays out of the suite so
CI needs no Redis.
"""

import asyncio
import json
from datetime import datetime

import pytest

from gpa.config import Settings
from gpa.errors import AppError
from gpa.services import jobs as job_service
from gpa.services import jobqueue


def _settings(tmp_path, **overrides) -> Settings:
    values = dict(
        env="test",
        database_url=f"sqlite:///{tmp_path}/queue.db",
        secret_key="test-secret-for-tests-only",
        storage_dir=tmp_path / "storage",
        outbox_dir=tmp_path / "outbox",
    )
    values.update(overrides)
    return Settings(**values)


class _FakeJob:
    job_id = "arq-job-42"


class _FakePool:
    """The arq pool's shape, recorded: enqueue_job is the one
    call the adapter makes (arq's create_pool is the factory
    the live leg exercises)."""

    def __init__(self) -> None:
        self.calls: list = []

    async def enqueue_job(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))
        return _FakeJob()


def test_database_backend_is_default_and_delegates(tmp_path, db_session, app):
    settings = app.state.settings
    assert settings.job_queue == "database"
    queue = jobqueue.build_queue(settings)
    assert queue.name == "database"

    seen = []
    job_service.register_handler("test.queue_db", lambda db, p: seen.append(p))
    job_id = queue.submit(db_session, settings, kind="test.queue_db", payload={"n": 7})
    row = job_service.get_job(db_session, job_id)
    assert row.status == "queued"
    assert job_service.process_one(db_session, settings, "w1") is True
    assert seen == [{"n": 7}]


def test_unknown_backend_is_refused_in_plain_words(tmp_path):
    settings = _settings(tmp_path, job_queue="carrier-pigeon")
    with pytest.raises(AppError) as caught:
        jobqueue.build_queue(settings)
    assert "unknown backend" in caught.value.detail


def test_arq_backend_requires_a_redis_url(tmp_path):
    settings = _settings(tmp_path, job_queue="arq", redis_url="")
    with pytest.raises(AppError) as caught:
        jobqueue.build_queue(settings)
    assert "GPA_REDIS_URL" in caught.value.detail


def test_arq_submit_translates_the_submission(tmp_path, db_session):
    settings = _settings(tmp_path, job_queue="arq", redis_url="redis://127.0.0.1:6379/0")
    pool = _FakePool()
    queue = jobqueue.build_queue(settings, pool_factory=lambda: pool)

    run_at = datetime(2026, 11, 5, 9, 30, 0)
    job_id = queue.submit(
        db_session, settings, kind="notify",
        payload={"to": "mara@example.org"}, run_at=run_at, max_attempts=3,
    )
    assert job_id == "arq-job-42"
    assert len(pool.calls) == 1
    function, args, kwargs = pool.calls[0]
    assert function == jobqueue.ARQ_FUNCTION_NAME
    assert args[0] == "notify"
    # The payload crosses to Redis sealed (B-166: data leaving
    # the process is envelope-encrypted at the serialisation
    # boundary) — unreadable on the wire, whole on arrival.
    assert "mara@example.org" not in args[1]
    assert job_service.open_payload(args[1], settings) == {"to": "mara@example.org"}
    # Our naive-UTC run_at is pinned to the zone for arq, and
    # our attempt cap becomes arq's own.
    assert kwargs["_defer_until"].isoformat() == "2026-11-05T09:30:00+00:00"
    assert kwargs["_max_tries"] == 3


def test_arq_dispatch_runs_the_registered_handler(app, db_session):
    """run_gpa_job → execute_payload → jobs.HANDLERS: the same
    registry the database worker runs, proven with a real
    session and a committed effect."""
    seen = []
    job_service.register_handler("test.arq", lambda db, p: seen.append(p))
    asyncio.run(
        jobqueue.run_gpa_job(
            {"database": app.state.db}, "test.arq", json.dumps({"x": 2})
        )
    )
    assert seen == [{"x": 2}]


def test_arq_dispatch_failure_raises_for_the_backend_retry(app, db_session):
    """A crashing handler propagates (arq owns the retry on
    its backend, as the chassis owns it on the database one)
    and its transaction is rolled back, never half-committed."""

    def boom(db, payload):
        raise RuntimeError("arq handler exploded")

    job_service.register_handler("test.arq_boom", boom)
    with pytest.raises(RuntimeError):
        jobqueue.execute_payload(db_session, "test.arq_boom", {})


def test_arq_dispatch_unknown_kind_is_a_plain_refusal(app, db_session):
    with pytest.raises(RuntimeError) as caught:
        jobqueue.execute_payload(db_session, "test.no_such_kind", {})
    assert "No handler registered" in str(caught.value)
