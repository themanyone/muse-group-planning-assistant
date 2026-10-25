"""Background job chassis (roadmap B-008).

A job is a row before it is work. ``enqueue`` commits the row;
a worker claims due jobs with a conditional UPDATE (claim-by-update:
two workers racing get exactly one winner); handlers run outside
any long transaction; failure retries with exponential backoff and,
after the job's max attempts, the job is dead-lettered — visible
forever, never silently dropped, and the worker lives on.

Handlers are plain functions ``handler(db, payload) -> None``,
registered by kind. Later phases register ingest, reminder, digest,
and sync handlers here.
"""

import json
from datetime import timedelta
from typing import Callable

from sqlalchemy import update
from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.core import Job
from gpa.models.identity import now_utc

# kind -> handler. Registration is how a module offers background
# work without the worker knowing anything about the module.
HANDLERS: dict[str, Callable[[Session, dict], None]] = {}


def register_handler(kind: str, handler: Callable[[Session, dict], None]) -> None:
    """Register (or replace) the handler for one job kind."""
    HANDLERS[kind] = handler


def queue(
    db: Session,
    *,
    kind: str,
    payload: dict | None = None,
    run_at=None,
    max_attempts: int = 5,
) -> Job:
    """Add a queued job WITHOUT committing (Phase 8).

    The outbox pattern (B-095) writes a notification row and its
    send job in the caller's transaction, so both exist or
    neither does. ``enqueue`` below is this plus a commit, for
    callers who own no larger transaction.
    """
    job = Job(
        kind=kind,
        payload_json=json.dumps(payload or {}),
        run_at=run_at or now_utc(),
        max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


def enqueue(
    db: Session,
    settings: Settings,
    *,
    kind: str,
    payload: dict | None = None,
    run_at=None,
    max_attempts: int | None = None,
) -> Job:
    """Commit a new queued job and return it."""
    job = queue(
        db,
        kind=kind,
        payload=payload,
        run_at=run_at,
        max_attempts=max_attempts or settings.job_max_attempts,
    )
    db.commit()
    return job


def get_job(db: Session, job_id: str) -> Job:
    """Fetch a job by id or raise not_found."""
    job = db.get(Job, job_id)
    if job is None:
        raise AppError("not_found", detail="That job could not be found.")
    return job


def claim_next(db: Session, worker_id: str) -> Job | None:
    """Claim the oldest due job for this worker, or None.

    The claim is one conditional UPDATE ... WHERE status='queued':
    if another worker won the race, the update touches zero rows
    and we move on. That is what makes double-running impossible.
    """
    candidate = (
        db.query(Job)
        .filter(Job.status == "queued", Job.run_at <= now_utc())
        .order_by(Job.run_at, Job.created_at)
        .first()
    )
    if candidate is None:
        return None
    result = db.execute(
        update(Job)
        .where(Job.id == candidate.id, Job.status == "queued")
        .values(status="running", locked_by=worker_id)
    )
    db.commit()
    if result.rowcount != 1:
        return None  # Lost the race — the other worker has it.
    return get_job(db, candidate.id)


def _finish(db: Session, job: Job, settings: Settings, error: Exception | None) -> None:
    """Record a job's outcome: done, retry-with-backoff, or dead."""
    if error is None:
        job.status = "done"
        job.progress = 100
        job.last_error = ""
    else:
        job.attempts += 1
        job.last_error = f"{type(error).__name__}: {error}"[:2000]
        if job.attempts >= job.max_attempts:
            # Dead-letter: out of the queue, but the row — and its
            # error — stay visible for a person to inspect.
            job.status = "dead"
        else:
            delay = settings.job_backoff_base_seconds * (2 ** job.attempts)
            job.status = "queued"
            job.run_at = now_utc() + timedelta(seconds=delay)
    job.locked_by = None
    db.commit()


def process_one(db: Session, settings: Settings, worker_id: str) -> bool:
    """Claim and run one job. True when a job was processed.

    A handler that crashes never crashes the worker: the exception
    is the job's failure record, and the loop moves on (B-008
    acceptance).
    """
    job = claim_next(db, worker_id)
    if job is None:
        return False
    handler = HANDLERS.get(job.kind)
    try:
        if handler is None:
            raise RuntimeError(f"No handler registered for job kind '{job.kind}'")
        handler(db, json.loads(job.payload_json or "{}"))
        db.commit()
        _finish(db, job, settings, None)
    except Exception as exc:  # The job failed; the worker must not.
        db.rollback()
        _finish(db, get_job(db, job.id), settings, exc)
    return True
