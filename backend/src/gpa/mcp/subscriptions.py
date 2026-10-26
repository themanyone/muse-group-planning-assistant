"""MCP subscriptions + change notifications (roadmap B-214).

What this module is, and the law it works under:

* **Subscriptions are the shelf's watch, not a second
  stream.** B-213 gave agents read-only ``gpa://`` resources.
  This module lets a client SUBSCRIBE to the two resource
  families that actually move while a group works — its
  calendar (``calendar.json`` / ``calendar.ics``) and its
  review queue (``queue.json``) — and be told, server →
  client, when a subscribed document has changed, so it
  re-reads instead of polling. Nothing else on the shelf is
  subscribable: the settled decisions, the memory-map, and
  the product's own paper change only when a person acts in
  ways the calendar and queue already reflect, or when a
  release replaces the tree — and a release is announced by
  the list-changed notifications below, not by pretending a
  static document is a live one.
* **One event system, two surfaces (the roadmap's own
  words).** The backend already has exactly one change
  stream: ``gpa.services.stream``'s SSE model (B-033, grown
  by B-060 and B-101), which speaks ``snapshot``,
  ``job.progress``, ``comment.created``, and
  ``notification.created`` frames polled from the durable
  tables. This module does NOT invent a second event bus.
  Its translator (:func:`resource_kinds_for_frame`) maps
  those very frames onto the resource kinds they can move,
  and its dispatcher turns a frame for one group into
  ``notifications/resources/updated`` messages for the
  sessions subscribed to that group's documents. The MCP
  package still imports no service: the translator works on
  the frames' plain shape (event name + data dict), which
  is the stream's published contract, not its internals.
* **Over-notify, never under-notify.** A frame rarely says
  WHICH document it moved — a ``job.progress`` frame may be
  an extraction filling the queue or a publish filling the
  calendar — so the mapping refreshes both rather than
  guess. A spurious "updated" costs a client one re-read of
  a document it cares about; a missed one leaves it citing
  a stale calendar. The one frame mapped to nothing is
  ``comment.created``: a comment changes a discussion, and
  neither the calendar nor the queue document carries
  discussion, so announcing either would be a lie about
  what changed. ``snapshot`` is likewise not a change — it
  is the connect-time picture itself.
* **The store is per-session, in this process, and honest
  about it.** A subscription is a fact about a live MCP
  session, so :class:`SubscriptionStore` keys subscriptions
  by session and forgets a session whole when it ends
  (:meth:`SubscriptionStore.clear_session`). It is guarded
  by a lock because the HTTP transport serves sessions from
  several threads. It is deliberately NOT durable: a server
  restart ends every session anyway, and a subscription
  that outlived its session would notify nobody, wrongly.
* **Sending rides the SDK session's own verbs.**
  :func:`dispatch_frame` and :func:`send_list_changed` call
  the session object's ``send_resource_updated`` /
  ``send_tool_list_changed`` / ``send_resource_list_changed``
  / ``send_prompt_list_changed`` — the official SDK's
  server-notification surface — and nothing else. Sessions
  are duck-typed here (any object with those coroutines),
  which is what lets the tests drive the dispatcher with a
  recording fake instead of a live wire.
* **The SDK capability gap, stated plainly.** The installed
  SDK (1.30.0) builds its resource capability with
  ``subscribe=False`` hardcoded and defaults every
  ``listChanged`` flag to False, even though it routes
  ``resources/subscribe`` requests and offers the send
  verbs above. ``gpa.mcp.server`` therefore wraps the
  built server's initialization options to advertise what
  this server TRULY does — subscribe handlers registered,
  list-changed notifications sendable — rather than let
  the SDK's defaults understate the surface. The wrap is
  on our instance only, is covered by tests, and goes away
  the day the SDK grows the knobs itself.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable
from urllib.parse import urlparse

from gpa.errors import AppError
from gpa.mcp.structured import get_logger, log_event

# The resource kinds a client may subscribe to, named by the
# final path segment of their ``gpa://`` templates (B-213):
# the group's calendar in both its forms, and its queue.
# Membership in this set is the whole subscribability law —
# the store refuses anything else, by name.
SUBSCRIBABLE_KINDS = frozenset({"calendar.json", "calendar.ics", "queue.json"})

# The list surfaces a list-changed notification can name
# (the MCP protocol's three): when the server's catalogue
# itself changes — a tool family landing (B-207 → B-212), a
# prompt set landing (B-215), the shelf growing — the
# matching notification tells every connected client to
# re-list, rather than work from a stale catalogue.
LIST_KINDS = frozenset({"tools", "resources", "prompts"})

# Which resource kinds each stream frame can move (see the
# module docstring's over-notify paragraph). Keys are the
# SSE event names ``gpa.services.stream`` publishes; values
# are the subscribable kinds those frames refresh. A frame
# name absent here — ``snapshot``, ``comment.created``, or
# anything a future stream grows without updating this map
# — refreshes nothing, conservatively.
FRAME_RESOURCE_KINDS: dict[str, frozenset[str]] = {
    # Jobs ARE the queue filling (extraction, vision) and
    # the calendar filling (publishing runs through jobs):
    # a progress frame can mean either, so it refreshes both.
    "job.progress": frozenset({"calendar.json", "calendar.ics", "queue.json"}),
    # A notification landing means the group's world moved
    # in a way the group chose to be told about — an event
    # published, moved, or cancelled, an RSVP promoted — and
    # those are calendar facts first; reminders and alerts
    # do not change the queue document, so it is not named.
    "notification.created": frozenset({"calendar.json", "calendar.ics"}),
}


def parse_group_resource(uri: str) -> tuple[str, str] | None:
    """Split one ``gpa://`` group-resource URI, or return None.

    The subscribable shape is exactly B-213's calendar and
    queue templates instantiated:
    ``gpa://groups/{group_id}/calendar.json`` (or ``.ics``,
    or ``queue.json``). Anything else — another scheme,
    another shelf branch, an event URI, extra path parts —
    returns None, and the caller refuses it as
    not-subscribable rather than guess at its meaning.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "gpa" or parsed.netloc != "groups":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    group_id, kind = parts
    return group_id, kind


def require_subscribable(uri: str) -> tuple[str, str]:
    """Validate one URI for subscription, or refuse it plainly.

    Two refusals, kept distinct because an agent fixes them
    differently: a URI that is not a group resource at all
    is ``not_found`` (no such document on the shelf), and a
    group resource outside the subscribable set is a
    ``validation_error`` naming the set (the document
    exists; watching it is what is not offered).
    """
    parsed = parse_group_resource(uri)
    if parsed is None:
        raise AppError(
            "not_found",
            detail=f"No subscribable resource is named by '{uri}'.",
        )
    group_id, kind = parsed
    if kind not in SUBSCRIBABLE_KINDS:
        raise AppError(
            "validation_error",
            detail=(
                f"'{kind}' resources are not subscribable; subscriptions "
                "cover calendar.json, calendar.ics, and queue.json."
            ),
        )
    return group_id, kind


def resource_kinds_for_frame(event: str) -> frozenset[str]:
    """The subscribable kinds one stream frame can move.

    A pure lookup in :data:`FRAME_RESOURCE_KINDS`: unknown
    and deliberately-unmapped frames return the empty set,
    so a dispatcher fed a frame it does not understand
    notifies nobody rather than everybody.
    """
    return FRAME_RESOURCE_KINDS.get(event, frozenset())


def uris_for_frame(group_id: str, event: str) -> list[str]:
    """The concrete URIs one frame refreshes for one group.

    Built from the frame's kinds in a stable (sorted) order,
    so a dispatcher's output — and a test's expectation — is
    deterministic for a given frame.
    """
    return [
        f"gpa://groups/{group_id}/{kind}"
        for kind in sorted(resource_kinds_for_frame(event))
    ]


class SubscriptionStore:
    """Every live session's subscriptions, in one locked map.

    Keys are opaque session keys chosen by the server layer
    (the SDK session's identity where one exists, a stable
    local key where it does not); values are the set of
    concrete ``gpa://`` URIs that session watches. The store
    validates on the way in (:func:`require_subscribable`),
    so a URI inside it is always a watchable document.
    """

    def __init__(self) -> None:
        """Start empty: no sessions, no subscriptions."""
        self._lock = threading.Lock()
        self._by_session: dict[str, set[str]] = {}

    def subscribe(self, session_key: str, uri: str) -> bool:
        """Add one subscription; True when it is newly added.

        Re-subscribing to a document already watched is a
        no-op that answers False — the protocol treats a
        repeat subscribe as success, and the caller needs
        the distinction only for its logging.
        """
        require_subscribable(uri)
        with self._lock:
            watched = self._by_session.setdefault(session_key, set())
            if uri in watched:
                return False
            watched.add(uri)
            return True

    def unsubscribe(self, session_key: str, uri: str) -> bool:
        """Drop one subscription; True when one was removed.

        Unsubscribing a document never watched answers False
        rather than raising — a client tidying up after a
        half-remembered subscription has done nothing wrong.
        The URI is NOT validated here: a client must be able
        to unsubscribe even a name the shelf has since
        stopped offering.
        """
        with self._lock:
            watched = self._by_session.get(session_key)
            if not watched or uri not in watched:
                return False
            watched.discard(uri)
            if not watched:
                del self._by_session[session_key]
            return True

    def subscriptions_for(self, session_key: str) -> list[str]:
        """One session's watched URIs, sorted for stability."""
        with self._lock:
            return sorted(self._by_session.get(session_key, ()))

    def sessions_watching(self, uri: str) -> list[str]:
        """The session keys watching one URI, sorted."""
        with self._lock:
            return sorted(
                key for key, watched in self._by_session.items() if uri in watched
            )

    def clear_session(self, session_key: str) -> int:
        """Forget one session whole; returns how many it held.

        The disconnect act: when a session ends, its watches
        end with it (see the module docstring — a subscription
        is a fact about a live session, never a durable row).
        """
        with self._lock:
            return len(self._by_session.pop(session_key, ()))

    def count(self) -> int:
        """The total live subscriptions across all sessions."""
        with self._lock:
            return sum(len(watched) for watched in self._by_session.values())


async def dispatch_frame(
    store: SubscriptionStore,
    sessions: dict[str, Any],
    group_id: str,
    event: str,
    data: dict | None = None,
) -> list[tuple[str, str]]:
    """Send resource-updated notifications for one stream frame.

    ``sessions`` maps session keys to live session objects
    (the SDK's ``ServerSession``, or a test's recording
    fake); ``event``/``data`` are one frame in the stream's
    published shape — only the event NAME steers the
    translation, because the kinds a frame can move are a
    property of the frame kind, not of its payload. Each
    session watching a refreshed URI receives exactly one
    ``send_resource_updated`` for it, however many frames
    kinds it matched. Sessions in the store but missing
    from ``sessions`` are skipped silently: they ended
    between the watch and the change, and notifying a dead
    session is nobody's error. Returns the (session, URI)
    pairs actually notified, in deterministic order, and
    logs the dispatch (frame, group, counts — never data).
    """
    uris = uris_for_frame(group_id, event)
    notified: list[tuple[str, str]] = []
    for uri in uris:
        for session_key in store.sessions_watching(uri):
            session = sessions.get(session_key)
            if session is None:
                continue
            await session.send_resource_updated(uri)
            notified.append((session_key, uri))
    log_event(
        get_logger(),
        "mcp.subscription.dispatched",
        operation=event,
        result="ok",
        error_code=None,
    )
    return notified


async def send_list_changed(session: Any, kinds: Iterable[str]) -> list[str]:
    """Send the list-changed notifications for changed catalogues.

    ``kinds`` names the surfaces that changed — any of
    ``tools``, ``resources``, ``prompts`` (an unknown kind
    is a ``validation_error``: a caller inventing a fourth
    catalogue has a bug, and a loud refusal is kinder than a
    silent half-send). Each named surface gets its own SDK
    verb, in the order given. Returns the kinds sent.
    """
    senders = {
        "tools": session.send_tool_list_changed,
        "resources": session.send_resource_list_changed,
        "prompts": session.send_prompt_list_changed,
    }
    sent: list[str] = []
    for kind in kinds:
        sender = senders.get(kind)
        if sender is None:
            raise AppError(
                "validation_error",
                detail=(
                    f"No list-changed notification exists for '{kind}'; "
                    "the catalogues are tools, resources, and prompts."
                ),
            )
        await sender()
        sent.append(kind)
    return sent
