"""The local client SDK — how a module asks another (B-162).

One call shape for every cross-module operation:

    data = call_internal(
        db, caller="events", operation="notify.request",
        payload={...},
    )

The client builds the B-162 envelope, checks the caller holds
the operation's required scope (the registry's law), dispatches
to the owning module's handler, and returns plain result data.
A denial or an unknown operation raises the same AppError the
external API would surface as problem+json — one error language
for the whole system (§6A.4). Across a transport (B-163) the
identical call runs over the wire and raises the identical
errors; the modules cannot tell the difference, which is the
point of the exercise (B-166 proves it with one suite, two
profiles).

Registration is lazy on purpose: handlers live in
``gpa.internal.handlers``, which imports the owning services.
Services import THIS module at their top level, so importing
handlers eagerly here would close an import circle. The first
call in a process registers everything, once; composition
roots may also call ``gpa.internal.handlers.register_all()``
at startup to pay the cost up front.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy.orm import Session

from gpa.config import Settings, get_settings
from gpa.errors import AppError
from gpa.internal import registry
from gpa.internal.envelope import InternalRequest, InternalResult, utc_now_iso


@dataclass
class CallContext:
    """What a handler may know about its call: who is asking,
    under which operation, with which settings. The envelope
    rides along for handlers that key work on its nonce or
    idempotency key."""

    caller: str
    operation: str
    settings: Settings
    envelope: InternalRequest


_registered = False


def _ensure_registered() -> None:
    """Register all operation handlers, once per process."""
    global _registered
    if _registered:
        return
    from gpa.internal import handlers

    handlers.register_all()
    _registered = True


class InternalClient:
    """The SDK object. One per process is plenty; call sites
    usually want the module-level :func:`call_internal`, which
    keeps a shared default client.

    ``wire`` is the B-166 split profile: when a WireClient is
    attached, `call` seals the envelope and sends it through
    the wire instead of dispatching locally — the modules'
    call sites do not change, the profile is a composition
    choice, and the server door enforces the same law."""

    def __init__(self, settings: Settings | None = None, *, wire=None) -> None:
        self._settings = settings
        self.wire = wire
        # B-165 installs the audit hook here (every call logged
        # by caller, operation, scope, result — never payload).
        self.audit_hook = None

    @property
    def settings(self) -> Settings:
        """The client's settings: the ones it was built with,
        or the application's (lazily, so tests can point the
        environment at a fixture world first)."""
        return self._settings or get_settings()

    def call(
        self,
        db: Session,
        *,
        caller: str,
        operation: str,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Make one internal call and return its result data.

        Raises AppError: ``internal_unknown_operation`` for an
        unregistered name, ``internal_scope_denied`` when the
        caller does not hold the operation's scope, and whatever
        AppError the handler itself raises — unchanged, so a
        caller's error handling reads exactly as it did when the
        call was direct.
        """
        _ensure_registered()
        op = registry.get_operation(operation)
        envelope = InternalRequest(
            operation=operation,
            caller=caller,
            scope=op.required_scope if op is not None else "",
            payload=payload or {},
            nonce=secrets.token_urlsafe(16),
            timestamp=utc_now_iso(),
            idempotency_key=idempotency_key,
        )
        if self.wire is not None:
            # Split profile: the server door dispatches, and
            # its audit hook writes the line — this side's
            # audit hook stays silent, because the act lives
            # where it was dispatched, exactly once.
            result = self.wire.call(envelope)
            return result.data
        return self._dispatch(db, envelope)

    def execute_envelope(self, db: Session, envelope: InternalRequest) -> InternalResult:
        """Dispatch a PREBUILT envelope and answer with a
        result object instead of raising (B-163).

        This is the server door's entry point: a remote caller
        arrives as an envelope (already signature-verified by
        the transport), and the answer must travel back as
        data — success or problem — because the wire cannot
        carry an exception. Scope and registration law are the
        same `_dispatch` the in-process door enforces; nothing
        about crossing a process weakens them.

        Transaction law differs by door, honestly: in-process,
        the caller's session is the caller's business (handlers
        ride its transaction); at this door the session is
        per-call and owned by the server, so one call is one
        transaction — commit on success, roll back on refusal
        or error."""

        try:
            data = self._dispatch(db, envelope)
        except AppError as exc:
            db.rollback()
            return InternalResult(
                ok=False,
                operation=envelope.operation,
                error={
                    "code": exc.code,
                    "title": exc.title,
                    "status": exc.status,
                    "detail": exc.detail,
                },
            )
        db.commit()
        return InternalResult(ok=True, operation=envelope.operation, data=data)

    def _dispatch(self, db: Session, envelope: InternalRequest) -> dict:
        """The one dispatch path: registration, scope, handler.

        Both doors (in-process `call`, wire `execute_envelope`)
        come through here, so the law cannot differ by door."""
        _ensure_registered()
        operation = envelope.operation
        caller = envelope.caller
        op = registry.get_operation(operation)
        handler = registry.HANDLERS.get(operation)
        if op is None or handler is None:
            raise AppError(
                "internal_unknown_operation",
                detail=f"No internal operation is registered as '{operation}'.",
            )
        if not registry.caller_may(caller, op.required_scope):
            self._audit(db, envelope, ok=False, error_code="internal_scope_denied")
            raise AppError(
                "internal_scope_denied",
                detail=(
                    f"Caller '{caller}' does not hold the internal scope "
                    f"'{op.required_scope}' that {operation} requires."
                ),
            )
        ctx = CallContext(
            caller=caller,
            operation=operation,
            settings=self.settings,
            envelope=envelope,
        )
        try:
            data = handler(db, envelope.payload, ctx)
        except AppError as exc:
            self._audit(db, envelope, ok=False, error_code=exc.code)
            raise
        self._audit(db, envelope, ok=True, error_code=None)
        return data

    def _audit(self, db, envelope, *, ok: bool, error_code: str | None) -> None:
        """Run the installed audit hook, if any (B-165).

        Auditing must never break the call it describes: a hook
        failure is swallowed here — the audit module's own tests
        prove the hook works, and a logging fault turning into
        a product fault would invert the design.
        """
        if self.audit_hook is None:
            return
        try:
            self.audit_hook(db, envelope, ok=ok, error_code=error_code)
        except Exception:  # noqa: BLE001 — see docstring
            pass


_default_client: InternalClient | None = None


def default_client() -> InternalClient:
    """The shared client call sites use."""
    global _default_client
    if _default_client is None:
        _default_client = InternalClient()
    return _default_client


def call_internal(
    db: Session,
    *,
    caller: str,
    operation: str,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Make one internal call through the shared default client.

    This is the form rewired call sites use: it reads as the
    direct call it replaced, with the boundary made explicit —
    who is asking (``caller`` = the calling module's name), what
    they are asking for, and nothing but ids and values in the
    payload.
    """
    return default_client().call(
        db,
        caller=caller,
        operation=operation,
        payload=payload,
        idempotency_key=idempotency_key,
    )
