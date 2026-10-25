"""Deep health contracts (roadmap B-155).

/health is the operator's first look (RUNBOOK.md): these
tests pin what it must say — the queue's shape with stuck
and dead work visible, storage as a real probe, drivers as
configuration facts, and 503-with-the-same-body when the
database does not answer.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from conftest import bearer, make_settings, register
from gpa.main import create_app
from gpa.models.core import Job
from gpa.services import health as health_service


def _job(db_session, *, status, age_minutes=0, kind="notify"):
    job = Job(kind=kind, status=status, payload_json="{}")
    db_session.add(job)
    db_session.commit()
    if age_minutes:
        stamp = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        job.created_at = stamp
        job.updated_at = stamp
        db_session.commit()
    return job


def test_health_reports_the_deep_checks(client):
    # Anonymous callers get the Phase 13 minimal surface:
    # the deep checks are operational data, disclosed to
    # signed-in callers only (the threat model's test pins
    # the anonymous side of this bargain).
    anonymous = client.get("/health").json()
    assert anonymous["status"] == "ok"
    assert anonymous["checks"] == {}

    owner = register(client)
    body = client.get("/health", headers=bearer(owner)).json()
    assert body["status"] == "ok"
    checks = body["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["database"]["engine"] in ("sqlite", "postgresql")
    assert checks["jobs"] == {
        "queued": 0, "running": 0, "done": 0, "dead": 0,
        "oldest_queued_age_seconds": None, "stuck_running": 0,
    }
    assert checks["ai"]["driver"]  # a configuration fact
    assert checks["mail"]["transport"]
    assert checks["storage"]["status"] == "ok"
    # The probe cleans up after itself: nothing lingers.


def test_health_counts_jobs_and_flags_stuck_and_dead(client, db_session):
    _job(db_session, status="queued", age_minutes=130)
    _job(db_session, status="running", age_minutes=60)   # stuck
    _job(db_session, status="running", age_minutes=1)    # merely working
    _job(db_session, status="done")
    _job(db_session, status="dead")

    owner = register(client)
    response = client.get("/health", headers=bearer(owner))
    assert response.status_code == 200  # degraded still answers 200
    body = response.json()
    assert body["status"] == "degraded"  # dead + stuck demand eyes
    jobs = body["checks"]["jobs"]
    assert jobs["queued"] == 1 and jobs["running"] == 2
    assert jobs["done"] == 1 and jobs["dead"] == 1
    assert jobs["stuck_running"] == 1
    assert jobs["oldest_queued_age_seconds"] >= 130 * 60 - 30


def test_health_storage_failure_degrades(app):
    # The production shape: the app booted fine, THEN the
    # disk went away. Storage is read from app.state per
    # request, so swapping in a failing store is the honest
    # reproduction. (An unusable storage dir at BOOT refuses
    # the boot instead — a different, also-honest answer.)
    app.state.storage = _FailingStorage()
    client = TestClient(app)
    owner = register(client)
    body = client.get("/health", headers=bearer(owner)).json()
    assert body["checks"]["storage"]["status"] == "error"
    assert body["status"] == "degraded"


def test_health_database_down_is_503_with_the_health_body(tmp_path):
    # auto_create off so the app boots; the "database file"
    # is a directory, which SQLite can never open, so the
    # first real query is the one that finds out — exactly
    # the production shape of "process up, database dead".
    settings = make_settings(
        tmp_path,
        auto_create_tables=False,
        database_url=f"sqlite:///{tmp_path}",
    )
    response = TestClient(create_app(settings)).get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "down"
    assert body["database"] == "error"
    # Anonymous even here: the error's detail can carry a
    # filesystem path, so the deep checks stay empty.
    assert body["checks"] == {}


def test_deep_health_unit_reports_down_without_raising(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/gone/gpa.db")
    session = sessionmaker(bind=engine)()
    settings = make_settings(tmp_path)
    result = health_service.deep_health(
        session,
        database=type("DB", (), {"engine": engine})(),
        settings=settings,
        storage=_FailingStorage(),
    )
    assert result["status"] == "down"
    assert result["checks"]["storage"]["status"] == "error"


class _FailingStorage:
    def put_bytes(self, data, extension=""):
        raise OSError("disk full")

    def exists(self, key):  # pragma: no cover - put raises first
        return False

    def get_bytes(self, key):  # pragma: no cover
        return b""

    def delete(self, key):  # pragma: no cover
        return None
