"""Deep health (roadmap B-155).

``GET /health`` began (B-010) as "process up + database
answers". Operating the product needs the next layer down in
the same answer, because the failures this product actually
has (RUNBOOK.md) announce themselves here first:

* ``database`` — the engine answers, and WHICH engine it is
  (a deployment that promoted to PostgreSQL should say so).
* ``jobs`` — the queue's shape: how many are queued, running,
  done, and DEAD (dead-lettered: work that will never run
  without a person), the age of the oldest queued job, and
  how many running jobs look STUCK (claimed long ago by a
  worker that may no longer exist — the chassis only ever
  claims ``queued`` rows, so a dead worker's job waits
  forever until an operator requeues it; the fix is
  scripts/requeue_stuck_jobs.py, RUNBOOK.md entry 5).
* ``ai`` / ``mail`` — which drivers the process is configured
  with. Configuration facts, not probes: health never spends
  an AI call or an SMTP connection proving a name.
* ``storage`` — a real probe: one tiny object written, read
  back, and deleted. A full or vanished disk shows here as
  an error minutes before the first upload does.

Status words: ``ok`` (everything healthy), ``degraded``
(the app answers but something an operator must see is
wrong: dead or stuck jobs, storage failing), ``down``
(the database does not answer — the route answers 503).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.models.core import Job

# A running job untouched for this long is reported stuck.
# Vision work is the slowest honest job and finishes in
# minutes; fifteen is generous without being asleep.
STUCK_RUNNING_AFTER = timedelta(minutes=15)

_PROBE_BYTES = b"gpa-health-probe"


def _age_seconds(moment: datetime | None, now: datetime) -> int | None:
    """Whole seconds between a stored moment and now.

    Stored datetimes come back naive from SQLite and aware
    from PostgreSQL; a naive stamp in this system is a UTC
    stamp (every writer uses now_utc), so it is read as UTC.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, int((now - moment).total_seconds()))


def _jobs_check(db: Session, now: datetime) -> dict:
    """The queue's shape: counts by status, the oldest queued
    job's age, and how many running jobs look stuck (claimed
    longer ago than STUCK_RUNNING_AFTER and never touched
    since — the signature of a worker that died mid-job)."""
    counts = {
        row_status: count
        for row_status, count in db.execute(
            select(Job.status, func.count()).group_by(Job.status)
        ).all()
    }
    oldest_queued = db.execute(
        select(func.min(Job.created_at)).where(Job.status == "queued")
    ).scalar()
    stuck = db.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.status == "running", Job.updated_at < now - STUCK_RUNNING_AFTER)
    ).scalar_one()
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "dead": counts.get("dead", 0),
        "oldest_queued_age_seconds": _age_seconds(oldest_queued, now),
        "stuck_running": stuck,
    }


def deep_health(db: Session, *, database, settings: Settings, storage) -> dict:
    """The checks behind GET /health. Returns
    ``{"status": ..., "checks": {...}}``; never raises for a
    dependency's failure — a failure IS the answer."""
    checks: dict = {}
    status = "ok"
    now = datetime.now(timezone.utc)

    # --- the database -------------------------------------
    database_ok = False
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {
            "status": "ok",
            "engine": database.engine.dialect.name,
        }
        database_ok = True
    except Exception as error:  # noqa: BLE001 — reported, not raised
        checks["database"] = {"status": "error", "detail": str(error)[:200]}
        status = "down"

    # --- the job queue ------------------------------------
    # Guarded separately from the SELECT 1 above: a database
    # that answers a ping but has no schema (the moment
    # mid-migration, a wrong file) is DOWN for every useful
    # purpose, and /health must say so — never 500 while
    # reporting health. (The B-155 tests found this shape.)
    if database_ok:
        try:
            checks["jobs"] = _jobs_check(db, now)
        except Exception as error:  # noqa: BLE001 — reported, not raised
            checks["jobs"] = {"status": "error", "detail": str(error)[:200]}
            status = "down"
        else:
            if checks["jobs"]["dead"] or checks["jobs"]["stuck_running"]:
                status = "degraded"

    # --- configured drivers (facts, not probes) ------------
    checks["ai"] = {"driver": settings.ai_driver}
    checks["mail"] = {"transport": settings.mail_transport}

    # --- storage: a real write/read/delete probe -----------
    try:
        key = storage.put_bytes(_PROBE_BYTES, extension=".probe")
        try:
            intact = storage.exists(key) and storage.get_bytes(key) == _PROBE_BYTES
        finally:
            storage.delete(key)
        checks["storage"] = {"status": "ok" if intact else "error"}
        if not intact and status == "ok":
            status = "degraded"
    except Exception as error:  # noqa: BLE001 — reported, not raised
        checks["storage"] = {"status": "error", "detail": str(error)[:200]}
        if status == "ok":
            status = "degraded"

    return {"status": status, "checks": checks}
