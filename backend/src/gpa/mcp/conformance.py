"""MCP conformance — the automated spec-case half (roadmap B-219).

What this module is, and what it honestly is not:

* **This module is the automated spec-case half of B-219's
  conformance work.** It runs ten named cases, in-process,
  against one built server (``gpa.mcp.server.build_server``):
  the server's identity and negotiated protocol versions, the
  tools list against the B-219 catalogue, the served
  annotations against the catalogue's derivation, the prompts
  list against the B-215 catalogue, the resource shelf's
  statics and templates (B-213, plus this slice's catalogue
  resource), the catalogue resource's own digest, and the
  three refusal cases — an unknown tool, an unknown resource,
  and an unknown prompt must each be REFUSED, never served.
  A case records its own outcome (name, ok, detail) and one
  case's failure never hides another's: the runner reports
  every case, pass or fail, so a conformance run reads as a
  checklist, not a verdict with the evidence removed.
* **The MCP Inspector is NOT run here.** The Inspector
  (``npx @modelcontextprotocol/inspector``) is an external,
  interactive tool a human drives in a browser; it needs npm
  and a network install, and this project's suite runs with
  neither. Nothing in this module shells out to it, downloads
  it, or pretends to its results. The Inspector road — the
  exact command, and what a human checks with it — is
  documented in Doc 13's B-219 section for a human who has
  the Inspector installed. This module is the half that CAN
  run on every commit, in the suite, with no network: the
  specification's testable claims about THIS server, checked
  against the server's own live surface.
* **The catalogue is the reference, the wire is the subject.**
  Every comparison runs between the catalogue module's facts
  (``gpa.mcp.catalogue`` — itself generated from the registry)
  and what the built server actually lists, serves, and
  answers. A registry can be perfect on paper while a server
  quietly serves something else; conformance exists to close
  exactly that gap, which is why these cases drive the
  server's async list/call/read methods directly (the same
  dispatch the SDK's protocol handlers serve) rather than
  re-reading the registry and calling the agreement proof.

This module builds no server and registers nothing: a caller
hands it one built server, and it reports. It imports the
catalogue, the prompt catalogue, and the server module's
protocol-version table — no service, router, or model, as
everywhere in this package.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from gpa.mcp.catalogue import catalogue_digest, tool_catalogue
from gpa.mcp.prompts import PROMPTS
from gpa.mcp.server import supported_protocol_versions

# The static shelf's URIs, stated once for the cases below:
# the B-213 pair plus this slice's catalogue resource. The
# group templates and the docs template are listed beside
# them — seven templates a conforming server must advertise.
CATALOGUE_URI = "gpa://tool-catalogue.json"
STATIC_URIS = ("gpa://openapi.json", "gpa://docs.json", CATALOGUE_URI)
GROUP_TEMPLATES = (
    "gpa://groups/{group_id}/calendar.json",
    "gpa://groups/{group_id}/calendar.ics",
    "gpa://groups/{group_id}/events/{event_id}.json",
    "gpa://groups/{group_id}/decisions.json",
    "gpa://groups/{group_id}/queue.json",
    "gpa://groups/{group_id}/memory-map.json",
)
DOCS_TEMPLATE = "gpa://docs/{chapter}"

# Names that are deliberately NOT part of the served surface:
# the refusal cases call exactly these, so a future real tool,
# resource, or prompt that happened to take one of these names
# would fail the case loudly instead of silently voiding it.
UNKNOWN_TOOL = "gpa_no_such_tool_b219"
UNKNOWN_RESOURCE = "gpa://no-such-resource-b219.json"
UNKNOWN_PROMPT = "no_such_prompt_b219"


# --- Reading the server's answers ---------------------------------------------------


def _tool_data(result: Any) -> dict:
    """One tool call's data dict, from the server's dispatch.

    FastMCP's in-process ``call_tool`` answers a tuple of
    (content blocks, structured dict) for the dict-returning
    tools this server serves; a bare dict passes through, and
    a content-only answer is read as the JSON text of its
    first block — the same three shapes
    tests/test_mcp_resources.py's ``_call`` helper reads.
    """
    # The structured half is the data: prefer it whenever the
    # dispatch provides one, because the text half is its
    # rendering, and parsing a rendering is the fallback.
    if isinstance(result, dict):
        return result
    if isinstance(result, (tuple, list)):
        if len(result) > 1 and isinstance(result[1], dict):
            return result[1]
        if result and hasattr(result[0], "text"):
            return json.loads(result[0].text)
    raise ValueError(f"unreadable tool result shape: {type(result).__name__}")


def _is_error_result(result: Any) -> bool:
    """Does this call result itself report an error?

    Over a real wire an unknown tool can come back as a result
    carrying ``isError`` rather than as a raised error; the
    in-process dispatch raises (the case handles that first),
    and this check covers the other shape so the refusal case
    means the same thing on both roads.
    """
    # A CallToolResult-shaped object states it directly; a
    # tuple's structured half can carry the same word.
    if getattr(result, "isError", False):
        return True
    if isinstance(result, (tuple, list)) and len(result) > 1 and isinstance(result[1], dict):
        return bool(result[1].get("isError"))
    return False


def _resource_text(contents: Any) -> str:
    """One resource read's text, from the dispatch's contents.

    ``read_resource`` answers a list of content objects whose
    payload rides ``content`` in this SDK's in-process shape
    (``text`` on the wire types); both spellings are read, so
    the case checks the DOCUMENT, not the SDK's attribute name.
    """
    first = contents[0]
    text = getattr(first, "content", None)
    if text is None:
        text = getattr(first, "text", "")
    return str(text)


def _served_annotations(tool: Any) -> dict[str, bool]:
    """One served tool's four hints, as a plain dict.

    The list dispatch answers Tool objects whose
    ``annotations`` is the SDK's ToolAnnotations model (or
    None for a tool served without hints — which this server
    never builds, and which reads here as all-False so the
    comparison against the catalogue fails honestly by name).
    """
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    return {
        "readOnlyHint": bool(annotations.readOnlyHint),
        "destructiveHint": bool(annotations.destructiveHint),
        "idempotentHint": bool(annotations.idempotentHint),
        "openWorldHint": bool(annotations.openWorldHint),
    }


# --- The cases ------------------------------------------------------------------------


async def _run_cases(server: Any) -> list[dict]:
    """Run every conformance case against one built server.

    Each case appends exactly one record — ``{name, ok,
    detail}`` — whether it passed, failed, or raised: a case
    that raises has FAILED (the exception's words become its
    detail), never vanished. Cases run in the order below,
    identity first and refusals last, so a report reads from
    "what is this server" to "what does it refuse".
    """
    cases: list[dict] = []

    def record(name: str, ok: bool, detail: str) -> None:
        """Append one case's outcome, in the report's shape."""
        cases.append({"name": name, "ok": bool(ok), "detail": detail})

    # The catalogue is computed once per run: every case below
    # compares the live surface against THIS snapshot of it.
    catalogue = tool_catalogue()
    by_name = {entry["name"]: entry for entry in catalogue}

    # (a) Server identity: the info tool answers the product's
    # name, and the protocol versions it claims include every
    # version the SDK's own table says the handshake supports.
    try:
        info = _tool_data(await server.call_tool("gpa_server_info", {}))
        supported = supported_protocol_versions()
        missing = [v for v in supported if v not in info.get("protocol_versions", [])]
        ok = info.get("name") == "group-planning-assistant" and not missing
        record(
            "server_identity",
            ok,
            f"name={info.get('name')!r}; protocol_versions missing: {missing or 'none'}",
        )
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case
        record("server_identity", False, f"raised {type(exc).__name__}: {exc}")

    # (b) The tools list IS the catalogue: the same names, the
    # same count, no extras and no gaps, in either direction.
    try:
        served_tools = await server.list_tools()
        served_names = {tool.name for tool in served_tools}
        ok = served_names == set(by_name) and len(served_tools) == len(catalogue)
        record(
            "tools_list_matches_catalogue",
            ok,
            f"served {len(served_tools)} tools; catalogue {len(catalogue)}; "
            f"unserved: {sorted(set(by_name) - served_names) or 'none'}; "
            f"undeclared: {sorted(served_names - set(by_name)) or 'none'}",
        )
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case
        record("tools_list_matches_catalogue", False, f"raised {type(exc).__name__}: {exc}")
        served_tools = []

    # (c) Served annotations equal the catalogue's, per tool:
    # the hints a client decides its ceremony from are the
    # hints the signed catalogue states, tool by tool.
    try:
        mismatched = [
            tool.name
            for tool in served_tools
            if tool.name in by_name
            and _served_annotations(tool) != by_name[tool.name]["annotations"]
        ]
        record(
            "served_annotations_match_catalogue",
            not mismatched,
            f"mismatched tools: {mismatched or 'none'}",
        )
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case
        record("served_annotations_match_catalogue", False, f"raised {type(exc).__name__}: {exc}")

    # (d) The prompts list IS the B-215 catalogue: every named
    # workflow is served, and nothing unlisted rides along.
    try:
        served_prompts = await server.list_prompts()
        served_prompt_names = {prompt.name for prompt in served_prompts}
        expected = {spec.name for spec in PROMPTS}
        record(
            "prompts_list_matches_catalogue",
            served_prompt_names == expected,
            f"served {sorted(served_prompt_names)}; catalogue {sorted(expected)}",
        )
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case
        record("prompts_list_matches_catalogue", False, f"raised {type(exc).__name__}: {exc}")

    # (e) The static shelf: all three static resources are
    # listed as resources (not templates) — the contract, the
    # docs index, and this slice's tool catalogue.
    try:
        statics = {str(resource.uri) for resource in await server.list_resources()}
        missing = [uri for uri in STATIC_URIS if uri not in statics]
        record(
            "resources_list_contains_statics",
            not missing,
            f"static resources listed: {sorted(statics)}; missing: {missing or 'none'}",
        )
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case
        record("resources_list_contains_statics", False, f"raised {type(exc).__name__}: {exc}")

    # (f) The templates: the six group templates of B-213 plus
    # the docs chapter template are all advertised.
    try:
        templates = {
            template.uriTemplate for template in await server.list_resource_templates()
        }
        expected_templates = set(GROUP_TEMPLATES) | {DOCS_TEMPLATE}
        missing = sorted(expected_templates - templates)
        record(
            "resource_templates_cover_group_and_docs",
            not missing,
            f"templates listed: {len(templates)}; missing: {missing or 'none'}",
        )
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case
        record("resource_templates_cover_group_and_docs", False, f"raised {type(exc).__name__}: {exc}")

    # (g) The catalogue resource reads, parses, and agrees with
    # the catalogue module: its digest field recomputes the
    # catalogue's digest and it carries every tool's entry.
    try:
        document = json.loads(_resource_text(await server.read_resource(CATALOGUE_URI)))
        expected_digest = catalogue_digest(tool_catalogue())
        ok = (
            document.get("digest") == expected_digest
            and document.get("count") == len(catalogue)
            and len(document.get("tools", [])) == len(catalogue)
        )
        record(
            "catalogue_resource_reads_and_digest_matches",
            ok,
            f"entries={len(document.get('tools', []))}; "
            f"digest matches catalogue: {document.get('digest') == expected_digest}",
        )
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case
        record("catalogue_resource_reads_and_digest_matches", False, f"raised {type(exc).__name__}: {exc}")

    # (h) An unknown tool is REFUSED: over the wire that is an
    # error result; in this dispatch it is a raised ToolError.
    # Either shape is the refusal; a served answer is the fail.
    try:
        result = await server.call_tool(UNKNOWN_TOOL, {})
        record(
            "unknown_tool_refused",
            _is_error_result(result),
            "call returned a non-error result for an unknown tool"
            if not _is_error_result(result)
            else "call returned an error result",
        )
    except Exception as exc:  # noqa: BLE001 — the refusal IS the pass here
        record("unknown_tool_refused", True, f"refused by exception: {type(exc).__name__}: {exc}")

    # (i) An unknown resource is REFUSED: the shelf serves its
    # allow-list and nothing else, so the read must raise.
    try:
        await server.read_resource(UNKNOWN_RESOURCE)
        record("unknown_resource_refused", False, "read of an unknown resource was served")
    except Exception as exc:  # noqa: BLE001 — the refusal IS the pass here
        record("unknown_resource_refused", True, f"refused by exception: {type(exc).__name__}: {exc}")

    # (j) An unknown prompt is REFUSED: the prompt catalogue is
    # closed, and an invented name meets its typed refusal.
    try:
        await server.get_prompt(UNKNOWN_PROMPT, {})
        record("unknown_prompt_refused", False, "an unknown prompt was served")
    except Exception as exc:  # noqa: BLE001 — the refusal IS the pass here
        record("unknown_prompt_refused", True, f"refused by exception: {type(exc).__name__}: {exc}")

    return cases


# --- The runner -------------------------------------------------------------------------


def run_conformance(server: Any) -> dict:
    """Run the spec cases against one built server; report all.

    The report is ``{passed, total, ok, cases}``: ``total``
    cases ran, ``ok`` of them passed, and ``passed`` is True
    only when every case passed — a single red case makes the
    run red, with the case's own detail naming what moved.
    Synchronous by design (it drives the async dispatch on a
    fresh event loop), so a test, a script, or an operator's
    check can call it exactly as written here.
    """
    cases = asyncio.run(_run_cases(server))
    ok_count = sum(1 for case in cases if case["ok"])
    return {
        "passed": ok_count == len(cases),
        "total": len(cases),
        "ok": ok_count,
        "cases": cases,
    }
