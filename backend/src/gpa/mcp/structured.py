"""Structured logging for the MCP surface (roadmap B-204).

The MCP server's log is an operator's record of a machine
surface: one JSON object per event on **stderr** (stdout is the
stdio transport's wire — a single stray byte there corrupts
the protocol), naming what happened, which tool or operation
was involved, which service identity spoke, how long it took,
and how it ended.

The rule that gives this module its shape: **payloads and
secrets are never logged** — not a tool's arguments, not a
result's content, not a credential. That is enforced here, not
by caller discipline: :func:`log_event` accepts only an
allow-list of flat, content-free fields and silently drops
everything else, including any mapping or sequence a caller
tries to pass. A field that could carry group content simply
has no road into the log.
"""

from __future__ import annotations

import json
import logging
import sys
import time

# The only fields an MCP log event may carry. Every one is a
# fact ABOUT a call (its name, its timing, its outcome), never
# a fact FROM a call (its arguments or its result).
ALLOWED_FIELDS = frozenset(
    {
        "tool",
        "operation",
        "caller",
        "key_id",
        "transport",
        "duration_ms",
        "result",
        "error_code",
        "protocol_version",
        "tools_count",
        "operations_count",
    }
)

_LOGGER_NAME = "gpa.mcp"


class JsonEventFormatter(logging.Formatter):
    """Render one log record as one JSON object per line.

    The record's ``msg`` is expected to already be the JSON
    event built by :func:`log_event`; the formatter adds only
    the timestamp and level, so the line stays a single flat
    object an operator (or a test) can parse whole.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format ``record`` as a flat JSON line."""
        try:
            event = json.loads(record.getMessage())
            if not isinstance(event, dict):
                event = {"event": str(event)}
        except (ValueError, TypeError):
            event = {"event": record.getMessage()}
        event.setdefault("level", record.levelname.lower())
        event.setdefault("ts", self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"))
        return json.dumps(event, sort_keys=True)


def get_logger() -> logging.Logger:
    """The MCP surface's logger, wired to stderr as JSON lines.

    Idempotent: composition roots and tests may ask repeatedly
    and always receive the one logger with at most one handler.
    Propagation is off so MCP events never leak into a host
    application's root formatting (or, worse, its stdout).
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if not any(getattr(handler, "_gpa_mcp_json", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonEventFormatter())
        handler._gpa_mcp_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    """Write one structured event, keeping only allowed fields.

    ``event`` is a short dotted name (``mcp.tool.completed``).
    Keyword fields outside :data:`ALLOWED_FIELDS` are dropped,
    and so is any value that is a mapping or a sequence — the
    allow-list is of flat facts, and a container value is a
    payload road by another name.
    """
    kept: dict[str, object] = {"event": event}
    for name, value in fields.items():
        if name not in ALLOWED_FIELDS:
            continue
        if isinstance(value, (dict, list, tuple, set, bytes)):
            continue
        kept[name] = value
    logger.info(json.dumps(kept, sort_keys=True, default=str))


def timed(logger: logging.Logger, event_prefix: str, **fields: object):
    """A tiny context manager timing one tool call for the log.

    Usage::

        with timed(logger, "mcp.tool", tool=name) as done:
            ...
            done(result="ok")

    Entering logs nothing; the callable the context yields
    records the outcome, and exiting without calling it records
    ``result="error"`` — a crashed call still leaves its line.
    """

    class _Timer:
        """The context object :func:`timed` yields and returns."""

        def __init__(self) -> None:
            """Start the clock with no outcome recorded yet."""
            self.started = time.monotonic()
            self.outcome: dict[str, object] = {}

        def __call__(self, **outcome: object) -> None:
            """Record how the timed call ended (ok / error_code)."""
            self.outcome.update(outcome)

        def __enter__(self) -> "_Timer":
            """Enter the timed span, yielding the outcome recorder."""
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            """Log the span's line, then let any exception travel."""
            duration_ms = round((time.monotonic() - self.started) * 1000, 3)
            outcome = dict(self.outcome)
            if exc_type is not None and "result" not in outcome:
                outcome["result"] = "error"
                outcome.setdefault("error_code", getattr(exc, "code", type(exc).__name__))
            log_event(
                logger,
                f"{event_prefix}.completed",
                duration_ms=duration_ms,
                **{**fields, **outcome},
            )
            return False

    return _Timer()
