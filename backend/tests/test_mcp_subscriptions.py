"""MCP subscription + notification tests (roadmap B-214).

The promises these guard:

* only the moving shelf is subscribable: a group's calendar
  (both forms) and its queue accept a subscription, while
  settled decisions, the memory-map, an event document, and
  names that are not group resources at all are refused —
  with the two refusals kept distinct (not_found vs
  validation_error), because an agent fixes them differently;
* the store is per-session and forgetful by design: a repeat
  subscribe is a no-op, unsubscribe and session-clearing
  drop exactly their own watches, and no session can see or
  drop another session's;
* the built server registers real protocol handlers for
  resources/subscribe and resources/unsubscribe, and its
  advertised capabilities tell the truth about this slice:
  resources subscribe=True and listChanged=True for tools,
  resources, and prompts (the SDK's defaults understate
  both, so the server wraps them — see subscriptions.py);
* subscribing is scope-checked like reading (B-213's guard
  restated): a draft-only principal is refused
  insufficient_scope at the subscribe handler;
* the translator is the SSE stream's own vocabulary: a
  job.progress frame refreshes calendar + queue, a
  notification.created frame refreshes the calendar only,
  and snapshot / comment.created frames refresh nothing —
  one event system, two surfaces, no second bus;
* the dispatcher sends notifications/resources/updated
  through the session's own SDK verb, only to sessions
  watching the changed document, exactly once per URI;
* list-changed notifications ride their own SDK verbs, per
  catalogue, and an invented catalogue is refused.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import make_group, register
from gpa.errors import AppError
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server
from gpa.mcp.subscriptions import (
    SubscriptionStore,
    dispatch_frame,
    send_list_changed,
    uris_for_frame,
)


class _FakeSession:
    """A recording stand-in for the SDK's ServerSession.

    The dispatcher and the list-changed sender speak to a
    session ONLY through its four send verbs, so a fake that
    records those four calls is a faithful wire for tests:
    what it records is exactly what a client would receive.
    """

    def __init__(self) -> None:
        """Start with no notifications received."""
        self.updated: list[str] = []
        self.list_changed: list[str] = []

    async def send_resource_updated(self, uri) -> None:
        """Record one notifications/resources/updated."""
        self.updated.append(str(uri))

    async def send_tool_list_changed(self) -> None:
        """Record one notifications/tools/list_changed."""
        self.list_changed.append("tools")

    async def send_resource_list_changed(self) -> None:
        """Record one notifications/resources/list_changed."""
        self.list_changed.append("resources")

    async def send_prompt_list_changed(self) -> None:
        """Record one notifications/prompts/list_changed."""
        self.list_changed.append("prompts")


def _uri(group_id: str, kind: str) -> str:
    """One concrete subscribable URI for a group."""
    return f"gpa://groups/{group_id}/{kind}"


# --- subscribability: the moving shelf only ---------------------------------


def test_only_calendar_and_queue_are_subscribable():
    """Calendar (both forms) + queue subscribe cleanly;
    every other shelf name is refused, in its own code."""
    store = SubscriptionStore()
    for kind in ("calendar.json", "calendar.ics", "queue.json"):
        assert store.subscribe("s1", _uri("g1", kind)) is True
    assert store.count() == 3
    # A group resource that exists but does not move with
    # the group's work: validation_error, naming the law.
    for kind in ("decisions.json", "memory-map.json"):
        with pytest.raises(AppError) as caught:
            store.subscribe("s1", _uri("g1", kind))
        assert caught.value.code == "validation_error", kind
    # Names that are not group resources at all: not_found.
    for uri in (
        "gpa://openapi.json",
        "gpa://docs/01-user-guide-start-and-groups.md",
        _uri("g1", "events/e1.json"),
        "https://groups/g1/calendar.json",
    ):
        with pytest.raises(AppError) as caught:
            store.subscribe("s1", uri)
        assert caught.value.code == "not_found", uri
    assert store.count() == 3


def test_store_is_per_session_and_forgetful_by_design():
    """Repeat subscribes no-op; unsubscribe and clear drop
    exactly their own session's watches, never a neighbour's."""
    store = SubscriptionStore()
    assert store.subscribe("s1", _uri("g1", "queue.json")) is True
    assert store.subscribe("s1", _uri("g1", "queue.json")) is False
    assert store.subscribe("s2", _uri("g1", "queue.json")) is True
    assert store.sessions_watching(_uri("g1", "queue.json")) == ["s1", "s2"]
    assert store.unsubscribe("s1", _uri("g1", "queue.json")) is True
    assert store.unsubscribe("s1", _uri("g1", "queue.json")) is False
    assert store.subscriptions_for("s2") == [_uri("g1", "queue.json")]
    assert store.clear_session("s2") == 1
    assert store.count() == 0
    assert store.clear_session("nobody") == 0


# --- the built server: handlers + truthful capabilities -----------------------


def test_server_registers_subscribe_handlers_and_advertises_them(
    client, app, settings
):
    """resources/subscribe + unsubscribe are real protocol
    handlers on the built server, and the initialization
    options advertise subscribe + all three listChanged
    flags — the truth about B-214's surface."""
    import mcp.types as types

    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    handlers = server._mcp_server.request_handlers
    assert types.SubscribeRequest in handlers
    assert types.UnsubscribeRequest in handlers
    capabilities = server._mcp_server.create_initialization_options().capabilities
    assert capabilities.resources.subscribe is True
    assert capabilities.resources.listChanged is True
    assert capabilities.tools.listChanged is True
    assert capabilities.prompts.listChanged is True


def test_subscribe_handler_records_the_watch(client, app, settings):
    """Driving the registered handler the way the wire does
    lands the subscription in the server's store (under the
    local key when no session context exists), and the
    unsubscribe handler removes it."""
    import mcp.types as types

    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    uri = _uri(group["id"], "calendar.json")

    async def drive(request_type, method, uri_text):
        """Invoke one registered handler with a built request."""
        handler = server._mcp_server.request_handlers[request_type]
        params_type = (
            types.SubscribeRequestParams
            if request_type is types.SubscribeRequest
            else types.UnsubscribeRequestParams
        )
        request = request_type(method=method, params=params_type(uri=uri_text))
        return await handler(request)

    asyncio.run(drive(types.SubscribeRequest, "resources/subscribe", uri))
    store = server._gpa_subscriptions
    assert store.subscriptions_for("local") == [uri]
    asyncio.run(
        drive(types.UnsubscribeRequest, "resources/unsubscribe", uri)
    )
    assert store.subscriptions_for("local") == []


def test_subscribe_handler_checks_read_scope(client, app, settings):
    """A draft-only principal meets insufficient_scope at
    the subscribe handler — subscribing is a reading act,
    guarded per call exactly as B-213's reads are."""
    import mcp.types as types

    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"draft"})
        ),
    )
    handler = server._mcp_server.request_handlers[types.SubscribeRequest]
    request = types.SubscribeRequest(
        method="resources/subscribe",
        params=types.SubscribeRequestParams(uri=_uri("g1", "queue.json")),
    )
    with pytest.raises(Exception) as caught:
        asyncio.run(handler(request))
    assert "insufficient_scope" in str(caught.value)
    assert server._gpa_subscriptions.count() == 0


# --- the translator: the stream's vocabulary, mapped --------------------------


def test_frame_translation_uses_the_stream_vocabulary():
    """job.progress → calendar + queue; notification.created
    → calendar only; snapshot and comment.created → nothing
    (a comment moves a discussion, which neither subscribable
    document carries). Unknown frames refresh nothing."""
    assert uris_for_frame("g1", "job.progress") == [
        _uri("g1", "calendar.ics"),
        _uri("g1", "calendar.json"),
        _uri("g1", "queue.json"),
    ]
    assert uris_for_frame("g1", "notification.created") == [
        _uri("g1", "calendar.ics"),
        _uri("g1", "calendar.json"),
    ]
    assert uris_for_frame("g1", "snapshot") == []
    assert uris_for_frame("g1", "comment.created") == []
    assert uris_for_frame("g1", "something.new") == []


# --- the dispatcher + list-changed senders -------------------------------------


def test_dispatch_notifies_only_watchers_exactly_once():
    """A job.progress frame for g1 reaches the session
    watching g1's queue and calendar — one updated notice
    per URI — and reaches neither the g2 watcher nor the
    session that subscribed and then unsubscribed."""
    store = SubscriptionStore()
    watcher, other, gone = _FakeSession(), _FakeSession(), _FakeSession()
    store.subscribe("watcher", _uri("g1", "queue.json"))
    store.subscribe("watcher", _uri("g1", "calendar.json"))
    store.subscribe("other", _uri("g2", "queue.json"))
    store.subscribe("gone", _uri("g1", "queue.json"))
    store.unsubscribe("gone", _uri("g1", "queue.json"))
    sessions = {"watcher": watcher, "other": other, "gone": gone}
    notified = asyncio.run(
        dispatch_frame(store, sessions, "g1", "job.progress", {"status": "done"})
    )
    assert watcher.updated == [_uri("g1", "calendar.json"), _uri("g1", "queue.json")]
    assert other.updated == [] and gone.updated == []
    assert notified == [
        ("watcher", _uri("g1", "calendar.json")),
        ("watcher", _uri("g1", "queue.json")),
    ]
    # A frame that moves nothing notifies nobody.
    notified = asyncio.run(
        dispatch_frame(store, sessions, "g1", "comment.created", {})
    )
    assert notified == [] and watcher.updated == [
        _uri("g1", "calendar.json"),
        _uri("g1", "queue.json"),
    ]


def test_list_changed_rides_its_own_verbs_and_refuses_inventions():
    """Each catalogue's change rides its own SDK verb, in
    the order named; a fourth, invented catalogue is a
    validation_error, sent to nobody."""
    session = _FakeSession()
    sent = asyncio.run(send_list_changed(session, ["tools", "prompts"]))
    assert sent == ["tools", "prompts"]
    assert session.list_changed == ["tools", "prompts"]
    with pytest.raises(AppError) as caught:
        asyncio.run(send_list_changed(session, ["widgets"]))
    assert caught.value.code == "validation_error"
