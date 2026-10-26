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

Payloads at rest (B-166): a job's payload can name people,
sources, and group business, so the stored form is sealed —
``enc:v1:`` + AES-256-GCM under the internal comms key (the
§6A.4 boundary rule: data leaving a process — into the job
table, or across to a queue backend — is envelope-encrypted
at the serialisation boundary). Rows written before B-166
carry plaintext JSON and are still read (the prefix is the
version marker); every new write is sealed. `payload_of` is
the one reading law.
"""

import json
from datetime import timedelta
from typing import Callable

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from gpa.config import Settings, get_settings
from gpa.errors import AppError
from gpa.models.core import Job
from gpa.models.identity import now_utc

# kind -> handler. Registration is how a module offers background
# work without the worker knowing anything about the module.
HANDLERS: dict[str, Callable[[Session, dict], None]] = {}

# The stored payload's version prefix + the GCM additional
# data binding a seal to this exact use (a payload blob
# replayed into another sealed column does not open here).
PAYLOAD_PREFIX = "enc:v1:"
_PAYLOAD_AAD = b"gpa-job-payload-v1"

# The settings the sealing key derives from. One process has
# one settings object; composition roots inject it here (the
# mailer/outbound pattern), because queue() is called from
# deep inside other modules' transactions where no settings
# argument travels. Without injection, the environment's
# settings answer — the same object composition itself built
# in every real entry point. What must NEVER happen is two
# sources in one process: a payload sealed under one key and
# opened under another is indistinguishable from tampering.
_injected_settings: Settings | None = None


def set_settings(settings: Settings) -> None:
    """Composition's injection: this process's settings."""
    global _injected_settings
    _injected_settings = settings


def _resolve_settings(settings: Settings | None) -> Settings:
    return settings or _injected_settings or get_settings()


def seal_payload(payload: dict, settings: Settings | None = None) -> str:
    """A payload's stored form: sealed JSON, prefixed."""
    from gpa.internal import crypto

    key = crypto.load_internal_key(_resolve_settings(settings))
    sealed = crypto.seal(key, json.dumps(payload).encode(), aad=_PAYLOAD_AAD)
    return PAYLOAD_PREFIX + sealed


def open_payload(text: str, settings: Settings | None = None) -> dict:
    """Read a stored payload: sealed form opens; a legacy
    plaintext row (no prefix) parses as the JSON it is."""
    raw = text or "{}"
    if raw.startswith(PAYLOAD_PREFIX):
        from gpa.internal import crypto

        key = crypto.load_internal_key(_resolve_settings(settings))
        return json.loads(
            crypto.open_sealed(key, raw[len(PAYLOAD_PREFIX):], aad=_PAYLOAD_AAD)
        )
    return json.loads(raw)


def payload_of(job: Job, settings: Settings | None = None) -> dict:
    """The one reading law for a job's payload."""
    return open_payload(job.payload_json or "{}", settings)


def store_payload(job: Job, payload: dict, settings: Settings | None = None) -> None:
    """The one writing law after creation (payload stamps)."""
    job.payload_json = seal_payload(payload, settings)


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
        payload_json=seal_payload(payload or {}),
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


# How many due candidates one claim call will try before it
# reports "nothing claimable right now" (B-157). A multi-instance
# deployment's workers all read the same oldest rows first; a
# worker that only ever tried the single oldest would report an
# empty queue while rows behind the contended head sat claimable.
_CLAIM_SCAN_LIMIT = 16


def claim_next(db: Session, worker_id: str) -> Job | None:
    """Claim the oldest claimable due job for this worker, or None.

    The claim is one conditional UPDATE ... WHERE status='queued'
    per candidate: if another worker won that row's race, the
    update touches zero rows and we try the next candidate. That
    conditional is what makes double-running impossible — and
    since B-157 it is a LOOP, not a single shot, so a worker
    that loses the head of the queue still finds the work behind
    it in the same call.

    A claim can also collide at the database itself (on
    PostgreSQL a peer's transaction can make our UPDATE raise
    instead of returning zero rows). That collision is a LOST
    RACE, not an error: it is caught, the session rolls back,
    and the scan moves on — the constraint's verdict reaches the
    caller as a clean outcome, never as an exception.
    """
    candidates = (
        db.query(Job)
        .filter(Job.status == "queued", Job.run_at <= now_utc())
        .order_by(Job.run_at, Job.created_at)
        .limit(_CLAIM_SCAN_LIMIT)
        .all()
    )
    for candidate in candidates:
        try:
            result = db.execute(
                update(Job)
                .where(Job.id == candidate.id, Job.status == "queued")
                .values(status="running", locked_by=worker_id)
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            continue  # Lost that row's race at the database. Next.
        if result.rowcount == 1:
            return get_job(db, candidate.id)
        # Rowcount 0: a peer claimed it between our read and our
        # update. The next candidate may still be ours.
    return None


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


def _defer_for_dependency(
    db: Session, job: Job, dependency: str, *, retry_seconds: float
) -> None:
    """Put a claimed job back to sleep WITHOUT spending an attempt
    (B-173). Its dependency cannot be called right now — breaker
    open or bulkhead full — so running it would only burn an
    attempt against a wall. The job stays queued, due again after
    the breaker's remaining cooldown (or a short breath for a
    full bulkhead), and its error field says in plain words what
    is down and that the work is waiting, not lost."""
    from gpa.services import resilience

    job.status = "queued"
    job.locked_by = None
    job.run_at = now_utc() + timedelta(seconds=max(1.0, retry_seconds))
    job.last_error = resilience.refusal_reason(dependency)
    db.commit()


def process_one(db: Session, settings: Settings, worker_id: str) -> bool:
    """Claim and run one job. True when a job was processed.

    A handler that crashes never crashes the worker: the exception
    is the job's failure record, and the loop moves on (B-008
    acceptance).

    B-173: a job whose kind leans on an external dependency first
    takes that dependency's permit (bulkhead slot + breaker).
    No permit means DEFER, not fail — see
    :func:`_defer_for_dependency`. A run that does happen settles
    the breaker with its true outcome, so consecutive real
    failures are what open the breaker, and the first success
    after an outage closes it again.
    """
    from gpa.services import resilience

    job = claim_next(db, worker_id)
    if job is None:
        return False
    dependency = resilience.dependency_of_kind(job.kind)
    if dependency is not None and not resilience.try_begin(dependency):
        breaker = resilience.breaker_for(dependency)
        retry = (
            breaker.cooldown_remaining()
            if breaker.state in ("open", "half_open")
            else 5.0
        )
        _defer_for_dependency(db, job, dependency, retry_seconds=retry)
        return True
    handler = HANDLERS.get(job.kind)
    try:
        if handler is None:
            raise RuntimeError(f"No handler registered for job kind '{job.kind}'")
        handler(db, payload_of(job, settings))
        db.commit()
        _finish(db, job, settings, None)
        if dependency is not None:
            resilience.end(dependency, success=True)
    except Exception as exc:  # The job failed; the worker must not.
        db.rollback()
        if dependency is not None:
            resilience.end(dependency, success=False)
        _finish(db, get_job(db, job.id), settings, exc)
    return True
