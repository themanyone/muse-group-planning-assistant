"""Queue backends behind one interface (roadmap B-157, D-09).

D-09 chose the database-backed job table for the hackathon and
said, in so many words, that *an external queue can replace it
later behind the same interface*. This module is that interface,
and Phase 16 is the "later":

- :class:`JobQueue` is the whole contract: ``submit`` work by
  kind + payload, and the registered handler for the kind runs
  it. Nothing else in the codebase knows which backend carried
  a given job.
- :class:`DatabaseJobQueue` is the chassis itself
  (services/jobs.py): the job is a committed row, claimed by
  conditional update, safe across any number of worker
  processes — the multi-instance proof is the race suite in
  tests/test_job_race.py.
- :class:`ArqJobQueue` is the external option: submission goes
  to a Redis the deployment runs, through the `arq` library
  (an optional install extra — ``uv sync --extra queue``), and
  the arq worker runs :func:`run_gpa_job`, which dispatches to
  the SAME handler registry (``jobs.HANDLERS``) the database
  worker uses. A deployment that outgrows one box flips
  ``GPA_JOB_QUEUE=arq``; no handler changes, no producer
  changes at the call sites that submit through this module.

The honest edges, stated where they live:

- **Atomicity differs.** A database submission can join the
  caller's transaction story (services/jobs.py's ``queue()``
  is the outbox door). An arq submission cannot: the job is
  visible to workers the moment Redis accepts it. The app's
  own transactional producers therefore keep submitting
  through the chassis in this phase; the adapter is the
  deployment option D-09 promised, proven at its boundary,
  not a silent swap under the outbox.
- **The record differs.** ``GET /jobs/{id}`` reads the job
  TABLE — database submissions have rows; arq submissions
  return the arq job id from :meth:`submit` for the caller's
  own records and are inspected through arq/Redis tooling.
- **The live leg RAN** (2026-10-25, TEST_REPORT's Phase 16
  addendum): no Redis is packaged for this environment, so
  one was compiled from source in the verify sandbox; a
  throwaway copy of this tree (``uv sync --extra queue``)
  submitted through the real adapter to the real server and
  the real arq worker ran the job through this module's
  dispatch — the handler's effect landed exactly once. It
  caught two real defects on the way (create_worker's true
  home in arq.worker, and the function-name registration
  below), both fixed in this file's slice follow-up commit.
  The suite itself still proves the adapter at an injected
  pool double, so CI needs no Redis.
"""

import asyncio
import json
from datetime import timezone
from typing import Protocol

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.services import jobs as job_service

# The one function name every GPA job travels under on the arq
# backend: arq addresses work by function name, and ours is a
# dispatcher — the GPA kind rides inside the call, so the
# registry stays the single vocabulary of kinds on both
# backends.
ARQ_FUNCTION_NAME = "gpa_job"


class JobQueue(Protocol):
    """What submitting work means, on either backend."""

    name: str

    def submit(
        self,
        db: Session,
        settings: Settings,
        *,
        kind: str,
        payload: dict | None = None,
        run_at=None,
        max_attempts: int | None = None,
    ) -> str:
        """Submit one job; return the backend's id for it."""
        ...


class DatabaseJobQueue:
    """The D-09 chassis as a JobQueue: a committed job row."""

    name = "database"

    def submit(
        self,
        db: Session,
        settings: Settings,
        *,
        kind: str,
        payload: dict | None = None,
        run_at=None,
        max_attempts: int | None = None,
    ) -> str:
        job = job_service.enqueue(
            db, settings, kind=kind, payload=payload,
            run_at=run_at, max_attempts=max_attempts,
        )
        return job.id


class ArqJobQueue:
    """The external-queue adapter: arq over a deployment's Redis.

    The pool is created lazily from ``settings.redis_url``;
    tests inject ``pool_factory`` — a zero-arg callable
    returning the pool object (real or double) — which is the
    adapter's transport seam, the same injection shape as
    services/linkfetch.py's resolver/transport.
    """

    name = "arq"

    def __init__(self, settings: Settings, *, pool_factory=None) -> None:
        if not settings.redis_url and pool_factory is None:
            raise AppError(
                "validation_error",
                detail=(
                    "The arq job queue needs GPA_REDIS_URL to name "
                    "the deployment's Redis (for example "
                    "redis://127.0.0.1:6379/0)."
                ),
            )
        self._settings = settings
        self._pool_factory = pool_factory
        self._pool = None

    async def _get_pool(self):
        """The arq Redis pool, created once per adapter."""
        if self._pool is None:
            if self._pool_factory is not None:
                self._pool = self._pool_factory()
            else:
                try:
                    from arq import create_pool
                    from arq.connections import RedisSettings
                except ImportError as err:
                    raise AppError(
                        "validation_error",
                        detail=(
                            "The arq job queue is selected but the "
                            "arq library is not installed — install "
                            "the deployment's queue extra "
                            "(uv sync --extra queue) or set "
                            "GPA_JOB_QUEUE=database."
                        ),
                    ) from err
                self._pool = await create_pool(
                    RedisSettings.from_dsn(self._settings.redis_url)
                )
        return self._pool

    def submit(
        self,
        db: Session,
        settings: Settings,
        *,
        kind: str,
        payload: dict | None = None,
        run_at=None,
        max_attempts: int | None = None,
    ) -> str:
        """Enqueue one job on Redis through arq; return its id.

        ``db`` is accepted for interface parity and unused —
        that non-use IS the atomicity edge named in the module
        docstring. arq's client is async; this adapter is a
        sync door, so one submission runs the enqueue on a
        short-lived event loop (never called from inside a
        running loop — the API process does not submit through
        this backend in this phase).
        """
        options: dict = {}
        if run_at is not None:
            # arq defers by aware datetime; our convention is
            # naive UTC (D-21), so pin the zone for the call.
            options["_defer_until"] = run_at.replace(tzinfo=timezone.utc)
        if max_attempts is not None:
            options["_max_tries"] = max_attempts

        async def _enqueue() -> str:
            pool = await self._get_pool()
            job = await pool.enqueue_job(
                ARQ_FUNCTION_NAME, kind, json.dumps(payload or {}), **options
            )
            return job.job_id

        return asyncio.run(_enqueue())


def build_queue(settings: Settings, *, pool_factory=None) -> JobQueue:
    """The queue backend the settings name, or a plain refusal.

    A misspelled backend must fail at startup, in words — never
    fall back silently to the other queue and lose (or double)
    a deployment's work."""
    if settings.job_queue == "database":
        return DatabaseJobQueue()
    if settings.job_queue == "arq":
        return ArqJobQueue(settings, pool_factory=pool_factory)
    raise AppError(
        "validation_error",
        detail=(
            f"GPA_JOB_QUEUE names an unknown backend "
            f"'{settings.job_queue}' — the backends are "
            "'database' and 'arq'."
        ),
    )


# ---------------------------------------------------------------------
# The arq worker side: same handlers, other transport
# ---------------------------------------------------------------------


def execute_payload(db: Session, kind: str, payload: dict) -> None:
    """Run one submitted job's handler on a session.

    The dispatch half of the interface, shared by the arq
    worker below with services/jobs.py's process_one: the
    handler registry is the vocabulary on both backends.
    Success commits; failure rolls back and RAISES — retry
    policy belongs to the carrying backend (the chassis'
    backoff/dead-letter on this side, arq's own tries on its).
    """
    handler = job_service.HANDLERS.get(kind)
    if handler is None:
        raise RuntimeError(f"No handler registered for job kind '{kind}'")
    try:
        handler(db, payload)
        db.commit()
    except Exception:
        db.rollback()
        raise


async def run_gpa_job(ctx: dict, kind: str, payload_json: str) -> None:
    """The arq function every GPA job travels as.

    ``ctx`` is arq's job context; :func:`arq_startup` puts the
    process Database and Settings in it. The payload crosses
    as a JSON string so the arq job record stays plain data.
    """
    db = ctx["database"].session()
    try:
        execute_payload(db, kind, json.loads(payload_json or "{}"))
    finally:
        db.close()


async def arq_startup(ctx: dict) -> None:
    """Build the worker process's world (arq on_startup hook).

    Mirrors gpa.worker.main's wiring: the same registries —
    importing the handler modules is what registers their
    kinds — so a kind the database worker can run, this
    worker can run."""
    from gpa.ai import client as ai_client
    from gpa.db import Database
    from gpa.services import digests as _digests  # noqa: F401 — handler registration
    from gpa.services import extraction as _extraction  # noqa: F401
    from gpa.services import mailer
    from gpa.services import messenger_transport
    from gpa.services import notifications as _notifications  # noqa: F401
    from gpa.services import outbound as _outbound  # noqa: F401
    from gpa.services import reconcile as _reconcile  # noqa: F401
    from gpa.services import retention as _retention  # noqa: F401
    from gpa.services import transcribe as _transcribe  # noqa: F401
    from gpa.services import vision as _vision  # noqa: F401
    from gpa.config import get_settings
    from gpa.storage import LocalStorage, set_storage

    settings = ctx.get("settings") or get_settings()
    ctx["settings"] = settings
    ai_client.set_client(ai_client.build_client(settings))
    mailer.set_transport(mailer.build_transport(settings))
    messenger_transport.set_transport(messenger_transport.build_transport(settings))
    _outbound.set_settings(settings)
    set_storage(LocalStorage(settings.storage_dir))
    database = Database(settings.database_url)
    database.create_all()
    ctx["database"] = database


def worker_settings(settings: Settings) -> dict:
    """The arq WorkerSettings for this deployment, as a dict.

    Run it with ``python -m gpa.arq_worker`` (the console
    entry ``gpa-arq-worker``), which hands this dict to arq's
    worker — the arq twin of ``gpa-worker``."""
    from arq import func
    from arq.connections import RedisSettings

    return {
        # arq addresses functions by NAME: ours travels as
        # ARQ_FUNCTION_NAME, so the dispatcher is registered
        # under that name explicitly (its Python name is an
        # implementation detail, not wire format).
        "functions": [func(run_gpa_job, name=ARQ_FUNCTION_NAME)],
        "redis_settings": RedisSettings.from_dsn(settings.redis_url),
        "on_startup": arq_startup,
    }
