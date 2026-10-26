"""Worked MCP client example — the official SDK over stdio (B-219).

    python examples/mcp_stdio_client.py

Run it from the repo root, with the GPA_* environment set for
the deployment you want to meet (GPA_DATABASE_URL,
GPA_SECRET_KEY, GPA_STORAGE_DIR, GPA_OUTBOX_DIR — the same
variables the server itself reads; see .env.example).

What it does, in order:

1. Spawns the MCP server as a subprocess over stdio —
   ``python -m gpa.mcp.server --transport stdio`` — the
   local-agent road: no port, no token, the server acts as
   the spawning operator's own trusted local principal
   (unless GPA_MCP_SEAT_API_KEY binds it to a seat; see
   Doc 13). Proxy variables are stripped from the child's
   environment: a loopback process must never travel a
   proxy, and sandbox proxy values break the SDK client's
   parsing (the same fact tests/test_mcp_core.py records).
2. Initialises the session (the SDK negotiates a protocol
   version with the server) and lists the tools.
3. Calls ``gpa_server_info`` — the meta tool that reports
   the server's identity, with no group content on it.
4. Reads the ``gpa://tool-catalogue.json`` resource — the
   B-219 catalogue, served as a resource by the same server
   — and prints a plain summary: the server's name, the
   negotiated protocol version, how many tools are served,
   and how many catalogue entries the resource carries,
   with the catalogue digest's first twelve characters.

This is ONE of B-219's two independent worked clients. The
other, ``examples/mcp_http_client.py``, speaks to a remote
server with no SDK at all — hand-rolled JSON-RPC over the
standard library — so the pack proves the wire from both
sides: the SDK's reading of it, and the bare protocol's.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


def _server_env() -> dict[str, str]:
    """The environment the spawned server process inherits.

    The operator's own environment, minus every proxy
    variable: the child is a loopback process, and proxy
    values (this sandbox's included) exist for the wide
    network, not for the wire between a client and the
    server it just spawned.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.lower().endswith("_proxy") and key.lower() != "no_proxy"
    }


def _result_data(result) -> dict:
    """A tool call's data, from structured content or JSON text.

    The SDK answers a call with content blocks and, for
    dict-returning tools, a structured content mapping —
    prefer the structured half, fall back to parsing the
    first block's text as JSON.
    """
    if getattr(result, "structuredContent", None):
        return dict(result.structuredContent)
    return json.loads(result.content[0].text)


async def _run() -> dict:
    """Drive one stdio session and collect the summary facts.

    The session's whole life is inside the two context
    managers: leaving them closes the wire and ends the
    server subprocess, so a failing step cannot leak a
    server process behind the example.
    """
    # The SDK imports live here, not at module top: reading
    # this file (or importing it in a test) must not require
    # a working SDK install — running it must.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import AnyUrl

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gpa.mcp.server", "--transport", "stdio"],
        env=_server_env(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # Step 2: the handshake. The result carries the
            # version the two sides settled on and the
            # server's own name, in the server's words.
            init = await session.initialize()
            tools = await session.list_tools()
            # Step 3: the identity call — data only, asserted
            # nowhere here; the summary prints what it says.
            info = _result_data(await session.call_tool("gpa_server_info", {}))
            # Step 4: the catalogue, read as a resource. The
            # read answers a result whose contents list holds
            # one text block per content item; its text is one
            # JSON document: tools, count, digest.
            read = await session.read_resource(
                AnyUrl("gpa://tool-catalogue.json")
            )
            catalogue = json.loads(read.contents[0].text)
            return {
                "server_name": info["name"],
                "init_server_name": init.serverInfo.name,
                "protocol_version": init.protocolVersion,
                "tool_count": len(tools.tools),
                "catalogue_count": catalogue["count"],
                "catalogue_digest": catalogue["digest"],
            }


def main() -> int:
    """Run the example and print its plain summary.

    The printed lines are the example's contract with its
    reader (and with tests/test_mcp_conformance.py): the
    server's name, the negotiated protocol version, the
    served tool count, and the catalogue's count and digest
    prefix — enough to see the connection worked and the
    catalogue agrees with the wire.
    """
    summary = asyncio.run(_run())
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
