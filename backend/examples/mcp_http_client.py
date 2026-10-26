"""Worked MCP client example — hand-rolled JSON-RPC over HTTP (B-219).

    GPA_MCP_URL=http://127.0.0.1:8765/mcp \
    GPA_MCP_TOKEN=<an OAuth bearer token for that server> \
        python examples/mcp_http_client.py

Run it against a streamable-HTTP MCP server you started
yourself, for example from the repo root:

    python -m gpa.mcp.server --transport streamable-http

The defaults fit that server as shipped: ``GPA_MCP_URL``
defaults to ``http://127.0.0.1:8765/mcp`` (the settings'
``GPA_MCP_HTTP_HOST`` / ``GPA_MCP_HTTP_PORT`` defaults, path
``/mcp``), and ``GPA_MCP_TOKEN`` carries the bearer token
the remote road demands — the OAuth 2.1 resource-server
door of B-205, so a token minted for the server's audience
(``gpa.mcp.auth.issue_oauth_token`` is how this backend's
own tests mint one) is the caller's key.

What it does, in order — every step a plain HTTP POST of a
JSON-RPC 2.0 message, no MCP SDK anywhere in the file:

1. ``initialize`` — offers one protocol version this server
   supports (2025-03-26; the server's own list is in
   ``gpa.mcp.server.supported_protocol_versions``) and a
   clientInfo naming this example. The response may carry
   an ``mcp-session-id`` header: the session's name on this
   wire, captured here and sent on every later request.
2. ``notifications/initialized`` — the notification (no id,
   no answer expected) that completes the handshake.
3. ``tools/list`` — the served catalogue's names.
4. ``tools/call`` for ``gpa_server_info`` — the identity
   tool, called exactly as the catalogue's example_call
   for it is shaped.
5. ``resources/read`` for ``gpa://tool-catalogue.json`` —
   the B-219 catalogue as a document, so the summary below
   can state the catalogue's count and digest beside the
   wire's tool count.

Responses on this transport arrive in one of two bodies,
and this client reads BOTH: a plain JSON document, or a
``text/event-stream`` body whose ``data:`` lines carry the
JSON-RPC messages (the answer is the data line whose id
matches the request's). Teaching that duality is half this
example's point: the streamable-HTTP transport is allowed
either, and a client that only reads one breaks on the
other. The summary it prints matches the stdio example's
style — server name, protocol version, tool count,
catalogue count + digest prefix — because the two clients
are B-219's independent pair: the SDK's reading of the
wire, and this file's bare-protocol reading of the same
server, agreeing in public.
"""

from __future__ import annotations

import http.client
import json
import os
from urllib.parse import urlsplit

# The protocol version this client offers. It is one of the
# versions the server's SDK negotiates (2024-11-05,
# 2025-03-26, 2025-06-18, and later lines); a client offers
# the newest it can speak, and the server answers with the
# version the session will actually run.
PROTOCOL_VERSION = "2025-03-26"

# The catalogue resource this example reads for its summary:
# B-219's generated documentation, served as a resource.
CATALOGUE_URI = "gpa://tool-catalogue.json"


def _parse_body(raw: str, content_type: str) -> dict | None:
    """One response body as a JSON-RPC message, either shape.

    Plain JSON parses directly. An event-stream body is
    scanned line by line: each ``data:`` line is one JSON
    message, and the LAST one carrying an ``id`` is the
    answer (a stream may also carry notifications, which
    have no id and are not the answer to anything). An
    empty body (a notification's 202) reads as None.
    """
    text = raw.strip()
    if not text:
        return None
    if "text/event-stream" in content_type or text.startswith("data:"):
        # The event-stream road: collect the data lines, in
        # order, and keep the id-carrying message.
        answer: dict | None = None
        for line in text.splitlines():
            if line.startswith("data:"):
                message = json.loads(line[len("data:"):].strip())
                if isinstance(message, dict) and "id" in message:
                    answer = message
        return answer
    return json.loads(text)


class McpHttpClient:
    """A minimal streamable-HTTP MCP client, stdlib only.

    State is the wire's own: the parsed server URL, the
    bearer token (or None), the session id the initialize
    response assigned (or None), and the next request id.
    Every method is one POST; nothing is cached across
    calls, so the file reads top to bottom as the protocol
    conversation it performs.
    """

    def __init__(self, url: str, token: str = "") -> None:
        """Fix the wire's address and the caller's token.

        The URL is split once, here: host, port, and path
        are what http.client needs per request, and an
        https URL is honoured with the TLS connection class
        rather than refused — a remote deployment's wire is
        TLS (Doc 18), and an example that only spoke plain
        loopback HTTP would teach the wrong default.
        """
        parts = urlsplit(url)
        self._scheme = parts.scheme or "http"
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port or (443 if self._scheme == "https" else 80)
        self._path = parts.path or "/mcp"
        self._token = token
        # The session id arrives WITH the initialize answer;
        # until then there is no session to name.
        self.session_id: str | None = None
        self._next_id = 0

    def _post(self, message: dict | None) -> tuple[int, dict | None]:
        """POST one JSON-RPC message; return (status, answer).

        ``message=None`` is not a case here — notifications
        are messages too, just id-less ones; the answer is
        None for them (a 202 with an empty body). The
        session header rides every request after initialize
        assigned one, and the Accept header names both body
        shapes this transport may answer with — a client
        that accepts only JSON is refused by the transport,
        by the specification's own rule.
        """
        connection_class = (
            http.client.HTTPSConnection
            if self._scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(self._host, self._port, timeout=30)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        try:
            connection.request(
                "POST",
                self._path,
                body=json.dumps(message),
                headers=headers,
            )
            response = connection.getresponse()
            raw = response.read().decode("utf-8")
            status = response.status
            content_type = response.getheader("Content-Type") or ""
            # The session is named by the initialize RESPONSE:
            # capture it whenever a response offers one, so
            # the very next request already carries it.
            offered = response.getheader("mcp-session-id")
            if offered:
                self.session_id = offered
        finally:
            connection.close()
        return status, _parse_body(raw, content_type)

    def request(self, method: str, params: dict) -> dict:
        """Send one request and return its ``result``.

        A JSON-RPC error answer raises, carrying the error's
        own message: a teaching client should fail in the
        protocol's words, not invent a friendlier story over
        a refusal the server meant the caller to read.
        """
        self._next_id += 1
        status, answer = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params,
            }
        )
        if answer is None:
            raise RuntimeError(f"{method}: no answer (HTTP {status})")
        if "error" in answer:
            raise RuntimeError(f"{method}: {answer['error']}")
        return answer["result"]

    def notify(self, method: str, params: dict | None = None) -> None:
        """Send one notification: a message with no answer.

        Notifications carry no id and expect no result; the
        transport answers 202 with an empty body, and this
        method's whole job is to have sent it in order.
        """
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._post(message)


def fetch_summary(url: str, token: str = "") -> dict:
    """Run the whole conversation; return the summary facts.

    Kept apart from :func:`main` so a test (or an embedding
    operator) can drive the protocol functions directly and
    read the facts as data, while ``main`` owns only the
    printing. The facts mirror the stdio example's summary
    exactly — the pair are meant to be compared.
    """
    client = McpHttpClient(url, token)
    # Step 1: the handshake. The result names the settled
    # protocol version and the server, in the server's words.
    init = client.request(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "gpa-http-example", "version": "1.0"},
        },
    )
    # Step 2: the initialized notification completes the
    # handshake; tools may not be listed before it is sent.
    client.notify("notifications/initialized")
    # Step 3: the served tools, counted as listed.
    tools = client.request("tools/list", {})
    # Step 4: the identity call, shaped as the catalogue's
    # example_call for gpa_server_info (which takes no args).
    call = client.request(
        "tools/call", {"name": "gpa_server_info", "arguments": {}}
    )
    # A call's data rides its structured content when the
    # tool provides it, else the JSON text of its first
    # content block — the same duality as the body shapes.
    if call.get("structuredContent"):
        info = dict(call["structuredContent"])
    else:
        info = json.loads(call["content"][0]["text"])
    # Step 5: the catalogue document, read as a resource.
    read = client.request("resources/read", {"uri": CATALOGUE_URI})
    catalogue = json.loads(read["contents"][0]["text"])
    return {
        "server_name": info["name"],
        "init_server_name": init["serverInfo"]["name"],
        "protocol_version": init["protocolVersion"],
        "tool_count": len(tools["tools"]),
        "catalogue_count": catalogue["count"],
        "catalogue_digest": catalogue["digest"],
    }


def main() -> int:
    """Run the example from the environment; print the summary.

    ``GPA_MCP_URL`` and ``GPA_MCP_TOKEN`` are the example's
    whole configuration (see the module docstring for the
    defaults and the token's provenance). The printed lines
    match the stdio example's contract: server name,
    protocol version, tool count, catalogue count + digest.
    """
    url = os.environ.get("GPA_MCP_URL", "http://127.0.0.1:8765/mcp")
    token = os.environ.get("GPA_MCP_TOKEN", "")
    summary = fetch_summary(url, token)
    print(f"Server: {summary['server_name']}")
    print(f"Protocol version: {summary['protocol_version']}")
    print(f"Tools served: {summary['tool_count']}")
    print(
        f"Catalogue: {summary['catalogue_count']} entries, "
        f"digest {summary['catalogue_digest'][:12]}…"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
