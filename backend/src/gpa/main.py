"""Application factory (roadmap B-002, B-010).

``create_app()`` assembles one whole backend: configuration,
structured logging, request ids, the database, storage, the auth
rate limiter, the error chassis, and every router. Tests build
isolated apps by passing their own ``Settings``; production uses
``uvicorn gpa.main:create_app --factory``.

Structured logging: JSON lines when GPA_ENV=prod, plain readable
lines in dev — same fields either way (time, level, logger, message,
request id when inside a request).
"""

import logging
import uuid

from fastapi import FastAPI, Request

from gpa import __version__
from gpa.ai import client as ai_client
from gpa.config import Settings, get_settings
from gpa.db import Database
from gpa.errors import install_error_handlers
from gpa.ratelimit import TokenBucketLimiter
from gpa.routers import (
    auth,
    calendar,
    candidates,
    checklists,
    discussion,
    events,
    flyers,
    groups,
    invites,
    jobs,
    meta,
    nonprofit,
    notifications,
    planning,
    polls,
    rsvps,
    sources,
    templates,
    truth,
    users,
)
from gpa.storage.local import LocalStorage


def _configure_logging(settings: Settings) -> None:
    """One logging setup for the whole process (idempotent)."""
    if settings.env == "prod":
        fmt = (
            '{"time": "%(asctime)s", "level": "%(levelname)s", '
            '"logger": "%(name)s", "request_id": "%(request_id)s", '
            '"message": "%(message)s"}'
        )
    else:
        fmt = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


class _RequestIdFilter(logging.Filter):
    """Give every log record a request_id attribute (the current
    request's, or "-" outside one) so the format never fails."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", "-")
        return True


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Fails fast on bad configuration —
    a missing secret in prod stops startup with plain words (B-002)."""
    settings = settings or get_settings()
    _configure_logging(settings)
    logging.getLogger().addFilter(_RequestIdFilter())

    app = FastAPI(
        title="Group Planning Assistant",
        version=__version__,
        description=(
            "Planning from conversation, flyers, and newsletters — "
            "always drafted, always reviewed by a person."
        ),
    )

    # Everything a handler might need, owned by this app instance.
    app.state.settings = settings
    # The AI driver this process speaks through (B-035): built from
    # settings here, shared via the ai.client registry so job
    # handlers (which receive only db + payload) reach the same one.
    ai_client.set_client(ai_client.build_client(settings))
    # The mail transport (Phase 8, B-095): the notification
    # outbox sends through this driver boundary — the file
    # driver in dev; the provider driver lands in Phase 12.
    from gpa.services import mailer

    mailer.set_transport(mailer.build_transport(settings))
    app.state.db = Database(settings.database_url)
    app.state.storage = LocalStorage(settings.storage_dir)
    # The same backend, shared via the storage registry so job
    # handlers reach it too (Phase 5's vision/transcription).
    from gpa.storage import set_storage

    set_storage(app.state.storage)
    app.state.auth_limiter = TokenBucketLimiter(settings.auth_rate_per_minute)
    if settings.auto_create_tables:
        app.state.db.create_all()

    install_error_handlers(app)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """One request id per request: header in, header out, and in
        the access log — the thread that ties a failure report to
        the exact request that produced it."""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        logging.getLogger("gpa.access").info(
            "%s %s -> %s", request.method, request.url.path, response.status_code,
            extra={"request_id": request_id},
        )
        return response

    # Routers: meta (health), then the /api/v1 domains.
    app.include_router(meta.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(groups.router)
    app.include_router(candidates.router)
    app.include_router(events.router)
    app.include_router(calendar.router)
    app.include_router(calendar.public_router)
    app.include_router(discussion.router)
    app.include_router(rsvps.router)
    app.include_router(flyers.router)
    app.include_router(sources.router)
    app.include_router(sources.email_router)
    app.include_router(truth.router)
    app.include_router(jobs.router)
    app.include_router(planning.router)
    app.include_router(polls.router)
    app.include_router(checklists.router)
    app.include_router(templates.router)
    app.include_router(notifications.me_router)
    app.include_router(notifications.group_router)
    app.include_router(notifications.webhook_router)
    app.include_router(invites.group_router)
    app.include_router(invites.public_router)
    app.include_router(nonprofit.subgroups_router)
    app.include_router(nonprofit.announcements_router)
    app.include_router(nonprofit.shifts_router)
    return app
