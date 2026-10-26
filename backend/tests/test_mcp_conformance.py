"""MCP conformance + client pack tests (roadmap B-219, Phase 19).

The promises these guard:

* the generated tool catalogue is SOUND — its checker finds
  nothing — and TAMPER-EVIDENT: a dropped example, a wrong
  internal scope, and a flipped annotation each fail the
  checker BY THE TOOL'S NAME;
* the catalogue is SERVED: the static shelf lists
  ``gpa://tool-catalogue.json`` as application/json, and
  reading it returns every tool's entry with a digest that
  recomputes from the catalogue module itself;
* the conformance runner passes its whole case list against
  a built server — ten named cases, identity to refusals;
* the documentation does not drift: Doc 13's generated
  catalogue section equals the renderer's output exactly;
* both worked client examples RUN FOR REAL: the SDK stdio
  client against a spawned server subprocess, and the
  hand-rolled HTTP client against a streamable-HTTP server
  subprocess behind a minted OAuth bearer — each exiting 0
  and naming the server it met.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from gpa.mcp.bridge import InternalBridge
from gpa.mcp.catalogue import (
    catalogue_digest,
    check_catalogue,
    render_catalogue_markdown,
    tool_catalogue,
)
from gpa.mcp.conformance import run_conformance
from gpa.mcp.server import build_server

ROOT = Path(__file__).resolve().parent.parent

# Doc 13's generated-section fence, verbatim from the chapter
# and from scripts/generate_mcp_catalogue.py: the drift test
# reads the section between exactly these two markers.
BEGIN = "<!-- BEGIN GENERATED MCP TOOL CATALOGUE (B-219) -->"
END = "<!-- END GENERATED MCP TOOL CATALOGUE -->"


def _read(server, uri):
    """Read one resource through the server's own dispatch."""

    async def run():
        """The one read, on the server's event loop."""
        return await server.read_resource(uri)

    contents = asyncio.run(run())
    return contents[0].content


def _list_statics(server):
    """List the shelf's static resources as uri -> mime type."""

    async def run():
        """The one listing, on the server's event loop."""
        return await server.list_resources()

    return {str(r.uri): r.mimeType for r in asyncio.run(run())}


def _server_env(settings) -> dict[str, str]:
    """The environment a spawned server/client process inherits.

    The pattern is tests/test_mcp_core.py's ``_server_env``:
    proxy variables are stripped (a loopback wire must never
    travel a proxy, and the sandbox's proxy values break the
    SDK client's parsing), and the deployment's GPA_* facts
    come from the test's own settings.
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


def _free_port() -> int:
    """A currently-free loopback port for the HTTP server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --- the catalogue: sound, and tamper-evident by name ----------------------------


def test_catalogue_check_is_clean():
    """The generated catalogue passes its own checker whole:
    44 entries, coverage exact in both directions, every
    hash, annotation, scope, and example leg satisfied."""
    entries = tool_catalogue()
    assert len(entries) == 44
    assert check_catalogue() == []
    assert check_catalogue(entries) == []


def test_catalogue_tamper_dropped_example_fails_by_name():
    """An entry whose example is dropped fails the checker,
    and the failure NAMES the tool it belongs to."""
    entries = copy.deepcopy(tool_catalogue())
    victim = next(e for e in entries if e["name"] == "gpa_list_tasks")
    del victim["example"]
    failures = check_catalogue(entries)
    assert any(f.startswith("gpa_list_tasks:") for f in failures), failures


def test_catalogue_tamper_wrong_internal_scope_fails_by_name():
    """An entry claiming an internal scope the registry does
    not require for its operation fails, naming the tool."""
    entries = copy.deepcopy(tool_catalogue())
    victim = next(e for e in entries if e["name"] == "gpa_get_group")
    victim["internal_scope"] = "bogus:scope"
    failures = check_catalogue(entries)
    assert any(f.startswith("gpa_get_group:") for f in failures), failures


def test_catalogue_tamper_flipped_annotation_fails_by_name():
    """An entry whose read-only hint is flipped away from the
    B-218 derivation fails the checker, naming the tool."""
    entries = copy.deepcopy(tool_catalogue())
    victim = next(e for e in entries if e["name"] == "gpa_list_events")
    victim["annotations"]["readOnlyHint"] = False
    failures = check_catalogue(entries)
    assert any(f.startswith("gpa_list_events:") for f in failures), failures


# --- the catalogue, served as a resource --------------------------------------------


def test_catalogue_resource_is_listed_and_reads_whole(app, settings):
    """The static shelf lists the catalogue resource as
    application/json beside B-213's pair; reading it returns
    44 entries whose digest equals the catalogue module's
    own digest over a freshly built catalogue."""
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    statics = _list_statics(server)
    assert statics["gpa://tool-catalogue.json"] == "application/json"
    assert statics["gpa://openapi.json"] == "application/json"
    assert statics["gpa://docs.json"] == "application/json"
    document = json.loads(_read(server, "gpa://tool-catalogue.json"))
    assert document["count"] == 44
    assert len(document["tools"]) == 44
    assert document["digest"] == catalogue_digest(tool_catalogue())
    names = [entry["name"] for entry in document["tools"]]
    assert "gpa_server_info" in names and "gpa_approve_candidate" in names


# --- the conformance runner ------------------------------------------------------------


def test_conformance_run_passes_every_named_case(app, settings):
    """run_conformance against a built server: every case ok,
    at least the ten spec cases, each one named in the report."""
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    report = run_conformance(server)
    assert report["total"] >= 10, report
    assert report["ok"] == report["total"], report
    assert report["passed"] is True
    names = [case["name"] for case in report["cases"]]
    assert len(names) == len(set(names)), "case names must be unique"
    for expected in (
        "server_identity",
        "tools_list_matches_catalogue",
        "served_annotations_match_catalogue",
        "prompts_list_matches_catalogue",
        "resources_list_contains_statics",
        "resource_templates_cover_group_and_docs",
        "catalogue_resource_reads_and_digest_matches",
        "unknown_tool_refused",
        "unknown_resource_refused",
        "unknown_prompt_refused",
    ):
        assert expected in names, expected
    assert all(case["ok"] for case in report["cases"])


# --- the documentation does not drift -----------------------------------------------------


def test_doc13_generated_section_matches_the_renderer():
    """Doc 13's fenced catalogue section IS the renderer's
    current output, byte for byte between the markers: a
    registry change without a regeneration fails here."""
    text = (ROOT / "docs" / "13-security-and-cryptography.md").read_text(
        encoding="utf-8"
    )
    assert BEGIN in text and END in text
    section = text.split(BEGIN + "\n", 1)[1].split("\n" + END, 1)[0]
    assert section == render_catalogue_markdown()


# --- the worked examples, run for real -------------------------------------------------------


def test_stdio_example_runs_and_names_the_server(settings):
    """The SDK stdio example, as a subprocess from the repo
    root: it spawns its own server, handshakes, lists, calls,
    reads the catalogue resource, and prints the summary —
    exit 0, the server's name and the 44-tool count in it."""
    completed = subprocess.run(
        [sys.executable, "examples/mcp_stdio_client.py"],
        cwd=ROOT,
        env=_server_env(settings),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert "group-planning-assistant" in completed.stdout
    assert "44" in completed.stdout


def test_http_example_runs_and_names_the_server(settings):
    """The hand-rolled HTTP example, as a subprocess against a
    streamable-HTTP server subprocess: the bearer is minted
    for the server's audience exactly as in
    tests/test_mcp_core.py's HTTP test, and the example's own
    JSON-RPC conversation (initialize, initialized, list,
    call, catalogue read) prints the same summary shape."""
    from gpa.mcp.auth import issue_oauth_token

    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
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
        token = issue_oauth_token(
            settings,
            subject="user:mcp-conformance-http",
            scopes=["read", "draft", "publish", "admin"],
            audience=url,
        )
        env = _server_env(settings)
        env.update(GPA_MCP_URL=url, GPA_MCP_TOKEN=token)
        completed = subprocess.run(
            [sys.executable, "examples/mcp_http_client.py"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr[-3000:]
        assert "group-planning-assistant" in completed.stdout
        assert "44" in completed.stdout
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
