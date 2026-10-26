"""The durability manager (roadmap B-171).

Commit-before-answer, enforced structurally. The Samtal
lesson is law here: a handler that returns success while
its writes sit uncommitted has answered a lie — the client
acts on state a crash would erase. Until B-171 such a
handler failed SILENTLY (the request session closed and
rolled back, and nobody knew). Now it cannot answer at all:

* :func:`track` wraps every request session (installed by
  ``get_db``). Flush listeners mark the tracker when real
  writes are flushed; commit and rollback clear the mark.
* The durability middleware (installed by ``create_app``)
  asks :func:`pending_violation` after the handler answers:
  a marked tracker under a success response means the
  handler forgot — the session is rolled back and the
  client gets a 500 ``uncommitted_writes`` instead of the
  lie. Errors pass untouched (a refused request may leave
  its writes uncommitted on purpose; the close rolls back).

The tracker rides a contextvar, so concurrent requests each
carry their own.
"""

from __future__ import annotations

import contextvars

from sqlalchemy import event
from sqlalchemy.orm import Session


class WriteTracker:
    """One request session's write state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.wrote_uncommitted = False

    def attach(self) -> None:
        tracker = self

        @event.listens_for(self.session, "after_flush")
        def _flushed(session, flush_context):  # noqa: ANN001, ANN202
            # A flush carrying real row changes (not a query's
            # empty autoflush) is a write the world has not
            # been promised yet.
            if flush_context is not None:
                tracker.wrote_uncommitted = True

        @event.listens_for(self.session, "after_commit")
        def _committed(session):  # noqa: ANN001, ANN202
            tracker.wrote_uncommitted = False

        @event.listens_for(self.session, "after_rollback")
        def _rolled_back(session):  # noqa: ANN001, ANN202
            tracker.wrote_uncommitted = False

        @event.listens_for(self.session, "after_soft_rollback")
        def _soft_rolled_back(session, previous_transaction):  # noqa: ANN001, ANN202
            # A savepoint rollback discards the inner writes;
            # work flushed BEFORE the savepoint still stands
            # uncommitted if the outer transaction has not
            # committed — the flush listeners will have their
            # say again on any later flush, and the outer
            # commit clears the mark. Conservatively: keep
            # the mark as the flush history left it.
            pass


_current: contextvars.ContextVar[WriteTracker | None] = contextvars.ContextVar(
    "gpa_write_tracker", default=None
)


def track(session: Session) -> WriteTracker:
    """Begin tracking a request session (get_db calls this)."""
    tracker = WriteTracker(session)
    tracker.attach()
    _current.set(tracker)
    return tracker


def pending_violation(request=None) -> WriteTracker | None:
    """The request's tracker, if it holds uncommitted writes.

    The middleware passes its request; the tracker rides
    ``request.state`` (get_db put it there) because a
    contextvar set downstream does not propagate back up
    through the middleware stack."""
    tracker = None
    if request is not None:
        tracker = getattr(request.state, "durability_tracker", None)
    if tracker is None:
        tracker = _current.get()
    if tracker is not None and tracker.wrote_uncommitted:
        return tracker
    return None


__all__ = ["WriteTracker", "pending_violation", "track"]
