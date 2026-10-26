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
    integrations,
    invites,
    jobs,
    memory,
    meta,
    nonprofit,
    notifications,
    planning,
    polls,
    privacy,
    rsvps,
    sources,
    templates,
    truth,
    uploads,
    users,
)

# Path segments after which the NEXT segment is a credential
# (B-145): calendar feeds, guest invites, the email-in door,
# and inbound webhook sources all carry their token in the
# URL itself. The access log keeps the route's shape and
# drops the credential — a log file must never become a
# second copy of every live token.
_TOKEN_CARRYING_SEGMENTS = frozenset({"feed", "invites", "email-in", "source"})


def _redact_path(path: str) -> str:
    """The access log's view of a path: token-carrying
    segments replaced with REDACTED, everything else
    verbatim (routes must stay recognisable in the log)."""
    parts = path.split("/")
    for index, part in enumerate(parts[:-1]):
        if part in _TOKEN_CARRYING_SEGMENTS:
            parts[index + 1] = "REDACTED"
    return "/".join(parts)


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
    # The request-id filter rides the HANDLER, not only the
    # root logger: a logger's filters never run for records
    # propagated UP from child loggers (httpx, sqlalchemy, …),
    # but a handler's filters run for every record it emits —
    # the handler is where "the format never fails" is made
    # true (found at the Phase 9 gate, B-112: third-party
    # records reached this formatter without a request_id and
    # the formatter raised on them).
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # httpx/httpcore log every request's FULL URL at INFO
    # (B-145 find) — and in this system URLs carry credentials
    # (feeds, invites, member-pasted links). Their request
    # lines are quieted to WARNING: errors still surface, URLs
    # stop accumulating in log files.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


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
    # The internal API's audit hook (B-165): every internal
    # call this process dispatches writes its line (caller,
    # operation, scope, result — never payload) into the audit
    # stream.
    from gpa.internal import auditing

    auditing.install(settings=settings)
    # The job chassis' sealing settings (B-166): payloads at
    # rest are sealed under this process's one settings object.
    from gpa.services import jobs as _jobs

    _jobs.set_settings(settings)
    # The field-encryption service's settings (B-167): the
    # master key's one source in this process.
    from gpa.crypto import fields as _fields

    _fields.set_settings(settings)
    # The mail transport (Phase 8, B-095): the notification
    # outbox sends through this driver boundary — the file
    # driver in dev; the SMTP provider driver in production
    # (Phase 12, B-133).
    from gpa.services import mailer

    mailer.set_transport(mailer.build_transport(settings))
    # The Messenger notification transport (Phase 12, B-134):
    # the unavailable driver until access is granted (B-125).
    from gpa.services import messenger_transport

    messenger_transport.set_transport(messenger_transport.build_transport(settings))
    # The reconciliation job handler (Phase 12, B-137)
    # registers on import, like the other job services.
    from gpa.services import reconcile as _reconcile  # noqa: F401
    # Error-correction (B-173): a fresh composition starts
    # with closed circuit breakers and empty bulkheads — no
    # tenant of a shared process inherits another's outage —
    # and the data-repair job handler registers on import.
    from gpa.services import repair as _repair  # noqa: F401
    from gpa.services import resilience as _resilience

    _resilience.reset_all()
    # The outbound webhook deliverer (Phase 11, B-128): its
    # job handler receives only db + payload, so the process
    # settings it unseals with are wired in here.
    from gpa.services import outbound

    outbound.set_settings(settings)
    from gpa.services import audit as _audit

    _audit.set_chain_secret(settings.secret_key)
    app.state.db = Database(settings.database_url)
    # Storage: the local driver, sealed per-file when the
    # deployment asks (B-168, GPA_ENCRYPT_STORAGE).
    from gpa.storage.encrypted import build_storage

    app.state.storage = build_storage(settings)
    # The same backend, shared via the storage registry so job
    # handlers reach it too (Phase 5's vision/transcription).
    from gpa.storage import set_storage

    set_storage(app.state.storage)
    app.state.auth_limiter = TokenBucketLimiter(settings.auth_rate_per_minute)
    if settings.auto_create_tables:
        app.state.db.create_all()

    install_error_handlers(app)

    # B-171: the durability guard. A handler that answers
    # success while its tracked session still holds
    # uncommitted writes has answered a lie — roll the
    # writes back and answer the structural error instead.
    # Error responses pass untouched (their sessions close
    # and roll back by design).
    @app.middleware("http")
    async def _durability_guard(request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        from gpa import durability

        violation = durability.pending_violation(request)
        if violation is not None and response.status_code < 400:
            violation.session.rollback()
            from gpa.errors import AppError, problem_body
            from starlette.responses import JSONResponse

            err = AppError(
                "uncommitted_writes",
                detail=(
                    "The handler returned success with writes still "
                    "uncommitted; they were rolled back. Commit before "
                    "answering."
                ),
            )
            return JSONResponse(problem_body(request, err), status_code=err.status)
        return response

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
            "%s %s -> %s",
            request.method,
            _redact_path(request.url.path),
            response.status_code,
            extra={"request_id": request_id},
        )
        return response

    @app.middleware("http")
    async def embed_cors_middleware(request: Request, call_next):
        """Embed CORS (B-135): the public + embed paths answer a
        browser's Origin ONLY when some group registered it.
        An unregistered origin gets NOTHING — no Allow-Origin
        header, so the browser blocks the read — and a
        preflight from one is answered empty for the same
        reason. Every other path is untouched."""
        from fastapi.responses import Response as _Response

        path = request.url.path
        origin = request.headers.get("origin")
        on_embed_paths = path.startswith("/api/v1/public/") or path.startswith(
            "/api/v1/embed/"
        )
        if not (on_embed_paths and origin):
            return await call_next(request)
        from gpa.services import embed as embed_service

        session = app.state.db.session()
        try:
            registered = embed_service.origin_registered(session, origin)
        finally:
            session.close()
        if request.method == "OPTIONS":
            response = _Response(status_code=204)
            if registered:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = (
                    request.headers.get("access-control-request-headers", "*")
                )
                response.headers["Access-Control-Max-Age"] = "600"
                response.headers["Vary"] = "Origin"
            return response
        response = await call_next(request)
        if registered:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
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
    app.include_router(calendar.embed_router)
    app.include_router(discussion.router)
    app.include_router(rsvps.router)
    app.include_router(flyers.router)
    # Privacy BEFORE sources: its literal segments
    # (sources/permissions) must not be swallowed by the
    # sources router's {source_id} path (Phase 13).
    app.include_router(privacy.router)
    app.include_router(uploads.router)
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
    app.include_router(nonprofit.resources_router)
    app.include_router(nonprofit.attendance_router)
    app.include_router(nonprofit.members_io_router)
    app.include_router(nonprofit.settings_router)
    app.include_router(memory.router)
    app.include_router(integrations.router)
    app.include_router(integrations.webhook_router)

    # The agent pack (Phase 11, B-129): the seat's working
    # surface carries the "agent" tag in the contract, so a
    # frontend — or an agent framework — discovers it in the
    # schema itself. Done here, after every router is in, so
    # the exported OPENAPI.json and the live schema agree.
    from gpa.services import agent_pack

    agent_pack.tag_agent_operations(app)
    return app
