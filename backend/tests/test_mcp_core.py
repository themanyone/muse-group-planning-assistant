"""MCP server core tests (roadmap B-204, Phase 19).

The promises these guard:

* the MCP package is its own module that reaches the backend
  ONLY through the internal encrypted API, speaking as its own
  ``mcp`` service identity (B-164) — proven by an import scan
  of the package and by the audit lines its calls leave;
* both transports the roadmap names — stdio and streamable
  HTTP — serve the SAME server: the official SDK client
  negotiates a protocol version, lists the core tools, and
  calls them, over each wire in turn;
* a tool call travels the whole road (tool → bridge →
  internal API → owning module) and commits: a candidate
  drafted over stdio is a real row afterwards, with a
  ``module:mcp`` audit line carrying the identity's key id
  and never the payload's content;
* the structured log never carries payloads or secrets —
  fields outside the allow-list have no road in.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from conftest import make_group, register
from gpa.internal import identity, registry
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server, supported_protocol_versions
from gpa.mcp.structured import JsonEventFormatter, log_event, timed
from gpa.models.core import AuditLog
from gpa.models.groups import Candidate

ROOT = Path(__file__).resolve().parent.parent
MCP_PACKAGE = ROOT / "src" / "gpa" / "mcp"

# Superseded by B-206: the catalogue is now the generated
# registry's full set — B-204's four core tools plus one
# generated tool per remaining internal operation. The name
# stays CORE_TOOLS in these B-204 tests because what they pin
# (both transports serve the SAME set) is unchanged; the set
# itself is the registry's, asserted exactly in
# tests/test_mcp_registry.py. Grown again by B-207: the
# groups/members/profile family's five tools join the set.
CORE_TOOLS = {
    "gpa_server_info",
    "gpa_list_internal_operations",
    "gpa_create_candidate_draft",
    "gpa_publish_event",
    "gpa_request_notification",
    "gpa_notify_rsvp_promotion",
    "gpa_enqueue_job",
    "gpa_submit_ai_job",
    "gpa_emit_integration_event",
    "gpa_get_group",
    "gpa_list_members",
    "gpa_update_group",
    "gpa_get_profile",
    "gpa_update_profile",
    # B-208: the candidates / review-queue family.
    "gpa_list_candidates",
    "gpa_get_candidate",
    "gpa_approve_candidate",
    "gpa_reject_candidate",
    "gpa_edit_candidate",
    # B-209: the events / RSVPs / calendar family.
    "gpa_list_events",
    "gpa_get_event",
    "gpa_cancel_event",
    "gpa_list_rsvps",
    "gpa_set_rsvp",
    "gpa_get_agenda",
    # B-210: the sources / extraction-request family.
    "gpa_list_sources",
    "gpa_get_source",
    "gpa_create_source",
    "gpa_append_source_chunks",
    "gpa_request_extraction",
    # B-211: the planning family.
    "gpa_list_tasks",
    "gpa_list_decisions",
    "gpa_get_decision_chain",
    "gpa_list_questions",
    "gpa_update_task",
    "gpa_resolve_question",
    # B-212: the notifications / digests / announcements family.
    "gpa_list_announcements",
    "gpa_create_announcement",
    "gpa_get_inbox",
    "gpa_get_notification_preferences",
    "gpa_list_digests",
    "gpa_preview_digest",
    # B-216: the elicitation bridge (one tool, async — it can
    # ask the client back mid-call).
    "gpa_ask_for_missing_fact",
    # B-217: the sampling policy's one tool.
    "gpa_request_sampling",
}


# --- the boundary: internal API only, own identity -------------------------


def test_mcp_package_talks_only_through_the_internal_api():
    """No file in gpa/mcp imports a service, router, or model.

    The bridge's law (B-204) made mechanical: the package may
    import the internal API, the foundation files (config, db,
    errors), the SDK, and itself — a domain capability arrives
    as a registered internal operation or not at all.
    """
    forbidden = ("gpa.services", "gpa.routers", "gpa.models", "gpa.schemas")
    offenders: list[str] = []
    saw_internal = False
    for path in sorted(MCP_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.startswith(forbidden):
                    offenders.append(f"{path.name}: {module}")
                if module.startswith("gpa.internal"):
                    saw_internal = True
    assert offenders == [], f"MCP package reached around the internal API: {offenders}"
    assert saw_internal, "the bridge must actually use the internal API"


def test_bridge_identity_is_the_mcp_service_identity(app, settings):
    """The bridge speaks as ``mcp``, with B-164's own key pair.

    Its key id is the derived identity's key id for the name
    ``mcp`` — a distinct key, never a human's credential — and
    the registry grants this caller every registered
    operation's scope (the gateway posture B-205 layers
    per-principal authorisation over).
    """
    bridge = InternalBridge(settings, database=app.state.db)
    assert bridge.caller == "mcp"
    store = identity.load_store(settings)
    assert bridge.key_id == store.own["mcp"].key_id
    assert bridge.key_id != store.own["core"].key_id
    for scope in (
        "candidates:write",
        "events:publish",
        "notify:write",
        "jobs:write",
        "ai:submit",
        "integrations:write",
        # B-207: the groups/members/profile family's scopes.
        "groups:read",
        "groups:write",
        "profile:read",
        "profile:write",
        # B-208: the review queue's read + decision scopes.
        "candidates:read",
        "candidates:decide",
        # B-209: events/calendar reads, cancel write, RSVP write.
        "events:read",
        "events:write",
        "rsvps:write",
        # B-210: source reads/writes + the extraction request.
        "sources:read",
        "sources:write",
        "extraction:request",
        # B-211: planning reads + upkeep write.
        "planning:read",
        "planning:write",
        # B-212: board + notification reads, announce/compose.
        "announcements:read",
        "announcements:write",
        "notifications:read",
        "notifications:write",
    ):
        assert registry.caller_may("mcp", scope), scope
    operations = {op["name"] for op in bridge.operations()}
    assert operations == {
        "candidates.create",
        "events.publish",
        "notify.request",
        "notify.rsvp_promotion",
        "jobs.enqueue",
        "ai.submit_job",
        "integrations.emit",
        # B-207: the first domain family's operations.
        "groups.get",
        "groups.list_members",
        "groups.update",
        "profile.get",
        "profile.update",
        # B-208: the review-queue family's operations.
        "candidates.list",
        "candidates.get",
        "candidates.approve",
        "candidates.reject",
        "candidates.edit",
        # B-209: the events family's operations.
        "events.list",
        "events.get",
        "events.cancel",
        "rsvps.list",
        "rsvps.set",
        "calendar.agenda",
        # B-210: the sources family's operations.
        "sources.list",
        "sources.get",
        "sources.create",
        "sources.append",
        "extraction.request",
        # B-211: the planning family's operations.
        "planning.list_tasks",
        "planning.list_decisions",
        "planning.decision_chain",
        "planning.list_questions",
        "planning.update_task",
        "planning.resolve_question",
        # B-212: the hear-from-itself family's operations.
        "announcements.list",
        "announcements.create",
        "notifications.inbox",
        "notifications.preferences",
        "digests.list",
        "digests.preview",
        # B-216: the elicitation bridge's degradation operation
        # (rides planning:write, already granted above).
        "elicitation.ask",
        # B-217: the sampling authorisation + record operation
        # (its sampling:request scope is granted in the
        # registry's mcp caller table, beside the families').
        "sampling.sample",
    }


def test_bridge_call_commits_and_audits_without_payload(client, app, settings):
    """A bridge call is one committed, audited transaction.

    The candidate exists in a FRESH session afterwards (the
    commit is real, not session-local), and its audit line —
    actor ``module:mcp``, the B-165 detail shape — carries the
    bridge's key id while the draft's title appears nowhere in
    the line: the story is reconstructable, the content is not
    in it.
    """
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    secret_title = "MCP drafted club night — payload marker"
    result = bridge.call(
        "candidates.create",
        {
            "group_id": group["id"],
            "kind": "event",
            "title": secret_title,
            "created_by": "seat:mcp-test",
            "draft": {},
        },
    )
    candidate_id = result["candidate_id"]
    session = app.state.db.session()
    try:
        row = session.get(Candidate, candidate_id)
        assert row is not None and row.title == secret_title
        lines = (
            session.query(AuditLog)
            .filter_by(actor="module:mcp", action="internal.candidates.create")
            .all()
        )
        assert len(lines) == 1
        detail = json.loads(lines[0].detail_json)
        assert detail["caller"] == "mcp"
        assert detail["key_id"] == bridge.key_id
        assert detail["result"] == "ok"
        assert secret_title not in lines[0].detail_json
    finally:
        session.close()


def test_server_builds_with_the_core_tools(app, settings):
    """The built server carries exactly the registered tool set
    (B-204's core, grown to the full B-206 generated catalogue)."""
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == CORE_TOOLS
    info = bridge.info()
    # B-207: 12; B-208: 17; B-209: 23; B-210: 28; B-211: 34;
    # B-212: 40.
    assert info["caller"] == "mcp" and info["operations_count"] == 42


# --- structured logging: no payloads, ever ---------------------------------


def _capture_logger() -> tuple[logging.Logger, io.StringIO]:
    """A logger writing formatted JSON events into a string."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonEventFormatter())
    logger = logging.getLogger("gpa.mcp.test-capture")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream


def test_structured_log_drops_payloads_and_secrets():
    """Fields outside the allow-list have no road into the log.

    A caller that (wrongly) passes a payload mapping, a secret
    string under a plausible name, and a whole title produces a
    line carrying only the allowed flat facts about the call.
    """
    logger, stream = _capture_logger()
    log_event(
        logger,
        "mcp.tool.completed",
        tool="gpa_create_candidate_draft",
        result="ok",
        duration_ms=3.5,
        payload={"title": "SECRET-TITLE-MARKER"},
        api_key="SECRET-KEY-MARKER",
        title="SECRET-TITLE-MARKER",
    )
    out = stream.getvalue()
    assert "SECRET-TITLE-MARKER" not in out
    assert "SECRET-KEY-MARKER" not in out
    event = json.loads(out.strip().splitlines()[-1])
    assert event["tool"] == "gpa_create_candidate_draft"
    assert event["result"] == "ok"
    assert event["event"] == "mcp.tool.completed"


def test_timed_span_logs_outcome_and_error_code():
    """A span that raises still leaves its line, with the code."""
    logger, stream = _capture_logger()

    class _Boom(Exception):
        code = "internal_scope_denied"

    try:
        with timed(logger, "mcp.tool", tool="gpa_publish_event") as done:
            raise _Boom("refused")
    except _Boom:
        pass
    else:  # pragma: no cover — the span must not swallow
        raise AssertionError("timed swallowed the exception")
    event = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert event["result"] == "error"
    assert event["error_code"] == "internal_scope_denied"
    assert "duration_ms" in event


# --- transports: the same server over stdio and streamable HTTP ------------


def _server_env(settings) -> dict[str, str]:
    """The environment a spawned MCP server process inherits.

    Proxy variables are stripped: the server is a loopback
    process, and this sandbox's egress-proxy variables (with
    their bracketed no-proxy entries) break httpx's proxy-map
    parsing when the SDK client reads them — a loopback wire
    must never travel a proxy in the first place.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.lower().endswith("_proxy") and key.lower() != "no_proxy"
    }
    env.update(
        GPA_ENV="test",
        GPA_DATABASE_URL=settings.database_url,
        GPA_SECRET_KEY=settings.secret_key,
        GPA_AUTO_CREATE_TABLES="true",
        GPA_STORAGE_DIR=str(settings.storage_dir),
        GPA_OUTBOX_DIR=str(settings.outbox_dir),
    )
    return env


def _result_data(result) -> dict:
    """A tool call's data, from structured content or JSON text."""
    if getattr(result, "structuredContent", None):
        return dict(result.structuredContent)
    text = result.content[0].text
    return json.loads(text)


def test_stdio_transport_negotiates_and_drafts_a_candidate(client, settings):
    """stdio: SDK client handshake, tool list, real draft.

    The negotiate step asserts the protocol version the client
    and server settled on is one the server claims to support;
    the draft step proves the whole road commits — the row is
    read back through the API's own database afterwards.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    owner = register(client)
    group = make_group(client, owner)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gpa.mcp.server", "--transport", "stdio"],
        env=_server_env(settings),
    )

    async def run() -> tuple[str, set[str], dict, dict]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                info = _result_data(await session.call_tool("gpa_server_info", {}))
                draft = await session.call_tool(
                    "gpa_create_candidate_draft",
                    {
                        "group_id": group["id"],
                        "kind": "event",
                        "title": "Drafted over stdio",
                        "created_by": "seat:mcp-test",
                    },
                )
                return (
                    init.protocolVersion,
                    {tool.name for tool in tools.tools},
                    info,
                    _result_data(draft),
                )

    protocol_version, tool_names, info, draft = asyncio.run(run())
    assert protocol_version in supported_protocol_versions()
    assert tool_names == CORE_TOOLS
    assert info["name"] == "group-planning-assistant"
    assert protocol_version in info["protocol_versions"]
    assert draft["candidate_id"]
    # The draft committed: read the row through the app database.
    from gpa.db import Database

    session = Database(settings.database_url).session()
    try:
        row = session.get(Candidate, draft["candidate_id"])
        assert row is not None and row.title == "Drafted over stdio"
    finally:
        session.close()


def _free_port() -> int:
    """A currently-free loopback port for the HTTP server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_streamable_http_transport_serves_the_same_server(settings, monkeypatch):
    """streamable HTTP: the remote wire, the same tools.

    A real server subprocess on a free loopback port; the SDK
    client negotiates, lists, and calls over HTTP — and the
    server info it reads names the same server the stdio test
    met, because there is only one server to meet.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    # The SDK client builds its httpx client from this process's
    # environment; strip the proxy variables for the loopback
    # wire (see _server_env's note — same sandbox fact).
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)

    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gpa.mcp.server",
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ],
        env=_server_env(settings),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise AssertionError("MCP HTTP server did not start listening")
                time.sleep(0.2)

        async def run() -> tuple[str, set[str], dict]:
            url = f"http://127.0.0.1:{port}/mcp"
            # Superseded by B-205: the remote road is now
            # authenticated — the client presents an OAuth
            # bearer token minted for this server's audience
            # (the full B-205 error cases live in
            # tests/test_mcp_auth.py).
            from gpa.mcp.auth import issue_oauth_token

            token = issue_oauth_token(
                settings,
                subject="user:mcp-http-test",
                scopes=["read", "draft", "publish", "admin"],
                audience=url,
            )
            headers = {"Authorization": f"Bearer {token}"}
            async with streamablehttp_client(url, headers=headers) as (read, write, _session_id):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    info = _result_data(await session.call_tool("gpa_server_info", {}))
                    return init.protocolVersion, {tool.name for tool in tools.tools}, info

        protocol_version, tool_names, info = asyncio.run(run())
        assert protocol_version in supported_protocol_versions()
        assert tool_names == CORE_TOOLS
        assert info["name"] == "group-planning-assistant"
        assert info["transports"] == ["stdio", "streamable-http"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
