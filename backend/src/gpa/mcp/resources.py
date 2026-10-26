"""MCP resources — the read-only ``gpa://`` shelf (roadmap B-213).

What this module is, and the law it works under:

* **Resources are the server's library, not a second API.**
  B-204 → B-212 gave agents tools — verbs, each travelling
  one internal operation through the bridge. This module
  gives them NOUNS: stable ``gpa://`` URIs an agent (or its
  client's UI) can read, subscribe to (B-214, in
  ``gpa.mcp.subscriptions``), and
  cite — the group's calendar in both its shapes, one event,
  the standing decisions, a queue summary, a map of the
  group's product data, the OpenAPI document itself, and the
  product's own docs set. An agent can read the manual
  through the same connection it works through; that is the
  slice's whole point, stated in the roadmap in those words.
* **One road in, as always.** Every group-data resource is
  composed from the SAME internal operations the tools
  travel, through the SAME bridge (``gpa.mcp.bridge``), under
  the SAME internal scopes — this module imports no service,
  no router, and no model, exactly as the rest of the MCP
  package is forbidden to. A resource is a presentation of
  operation results, never a new capability: anything a
  resource shows, a tool call could already read.
* **Read scope, checked at read time.** A resource read is a
  read: the server hands this module a guard (its own
  principal resolution, B-205) that resolves who is asking
  RIGHT NOW and demands the ``read`` credential scope (admin
  carries it, as everywhere). The check runs inside every
  resource function, per read — a credential re-scoped after
  connecting loses its reading on the next read, the tools'
  call-time law restated for the shelf.
* **The bright line holds on the shelf.** Group resources
  read at MEMBER role: published and cancelled events are
  group-common knowledge, and a draft is absent — from the
  calendar, from the single-event resource (not_found), from
  every count in the map. A resource must never become the
  crack a draft leaks through merely because nobody thought
  of resources as a door. Organiser-grade reading stays at
  the role-carrying tools (B-209), where the role is stated
  in the call and audited with it.
* **Correct MIME types are part of the contract.** Calendar
  JSON and the map are ``application/json``; the calendar's
  iCalendar form is ``text/calendar``; docs chapters are
  ``text/markdown``; the OpenAPI document is
  ``application/json``. A client renders by MIME type, so a
  wrong one is a wrong answer, and the tests pin each.
* **The static shelf is the repo's own paper.** The OpenAPI
  document and the docs chapters are read from the tree this
  server ships in (repo root: ``OPENAPI.json``, ``docs/``) —
  the same files the release ZIP carries. A chapter name is
  honoured only when it names a file actually present in the
  docs directory (listed fresh per read), so path tricks
  (``..``, nested paths, other files) meet not_found, never
  the filesystem at large. A missing shelf (a partial
  install) is also not_found, in plain words — never a
  traceback and never invented content.
* **The iCalendar form is rendered HERE.** The internal
  operations return event facts, not renderings, so the
  ``text/calendar`` body is composed in this module from the
  ``events.list`` result: one VEVENT per event, UTC moments
  (the store's convention — starts/ends ride the bus as UTC
  ISO strings), DATE form for all-day events, cancelled
  carried as ``STATUS:CANCELLED`` and never vanished, text
  escaped by RFC 5545's rules. Rendering is presentation,
  which is this layer's job everywhere in the product (the
  frontend does zero date maths; neither does an agent).

The catalogue, for the reviewer
-------------------------------
Static resources (exact URIs):

* ``gpa://openapi.json`` — the product's OpenAPI document.
* ``gpa://docs.json`` — the docs index: every chapter's
  name, title line, URI, and MIME type.
* ``gpa://tool-catalogue.json`` — the B-219 tool catalogue:
  every tool's scopes, annotations, and worked example,
  with the catalogue digest (``gpa.mcp.catalogue``).

Resource templates (one per group shape):

* ``gpa://groups/{group_id}/calendar.json`` — the group's
  events as one JSON document (member view).
* ``gpa://groups/{group_id}/calendar.ics`` — the same
  events as iCalendar (member view).
* ``gpa://groups/{group_id}/events/{event_id}.json`` — one
  event (member view: a draft is not_found).
* ``gpa://groups/{group_id}/decisions.json`` — the CURRENT
  decisions only: settled truth, not the whole log.
* ``gpa://groups/{group_id}/queue.json`` — the review
  queue summarised: totals by status and kind, and the
  pending rows an agent would triage.
* ``gpa://groups/{group_id}/memory-map.json`` — the map of
  the group's product data: the group's plain facts, the
  roster's size by kind, and the counts of everything the
  group holds (sources, events, standing decisions, queue),
  with the sibling resources' URIs spelled out — the one
  read an agent makes first to learn a group's shape.
* ``gpa://docs/{chapter}`` — one docs chapter, verbatim.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gpa.errors import AppError
from gpa.mcp.structured import get_logger, log_event

# The role every group resource reads at (see the module
# docstring's bright-line paragraph): member. Published and
# cancelled events show; drafts are absent, exactly as a
# member's own calendar shows them.
RESOURCE_ROLE = "member"

# The repo root this server ships in: resources.py lives at
# src/gpa/mcp/, so the root is three parents up. The static
# shelf (OPENAPI.json, docs/) sits there in the repo and in
# the release ZIP's gpa-backend/ tree alike.
REPO_ROOT = Path(__file__).resolve().parents[3]

# The docs directory and the contract document, named once.
DOCS_DIR = REPO_ROOT / "docs"
OPENAPI_PATH = REPO_ROOT / "OPENAPI.json"


# --- Small shared helpers -----------------------------------------------------


def _guarded(guard: Callable[[], Any], operation: str, fn: Callable[[], str]) -> str:
    """Run one resource read under the read-scope guard.

    The guard resolves the principal and checks its scope
    (the server's B-205 machinery); the body composes the
    resource. AppError refusals — the guard's, the bridge's —
    are re-raised as plain resource errors LEADING with the
    backend's problem code, the same rendering the tool layer
    uses, so an agent reads one refusal language everywhere.
    The read is logged (resource, operation, outcome — never
    content), as every tool call is.
    """
    logger = get_logger()
    try:
        guard()
        text = fn()
    except AppError as exc:
        log_event(
            logger, "mcp.resource.completed", operation=operation,
            result="error", error_code=exc.code,
        )
        raise RuntimeError(f"{exc.code}: {exc.detail}") from exc
    log_event(logger, "mcp.resource.completed", operation=operation, result="ok")
    return text


def _json(data: dict) -> str:
    """Render one resource body as JSON text.

    Indented and key-sorted: a resource is a document a human
    may open, and a stable byte order makes two reads of an
    unchanged group byte-identical (which is what lets a
    client diff, cache, and cite honestly).
    """
    return json.dumps(data, indent=2, sort_keys=True)


# --- The iCalendar rendering ----------------------------------------------------


def _ical_escape(text: str) -> str:
    """Escape one text value by RFC 5545's rules.

    Backslash first (it is the escape character), then the
    two delimiters, then newlines as the literal ``\\n`` the
    standard prescribes. The calendar service's own
    serialiser applies the same rules at its door; this shelf
    renders from bus facts, so the rule is restated here —
    presentation is this layer's work.
    """
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ical_moment(value: str | None, *, all_day: bool) -> str | None:
    """Format one bus moment (UTC ISO) for an iCalendar line.

    Timed events become ``YYYYMMDDTHHMMSSZ`` (the store keeps
    UTC; the ``Z`` says so); all-day events become the DATE
    form. A missing moment stays missing (None) — the caller
    omits the line rather than invent a time.
    """
    if not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        # The bus's convention is naive-UTC (the store's own
        # convention); a naive value IS a UTC value here.
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    if all_day:
        return moment.strftime("%Y%m%d")
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _render_ical(events: list[dict]) -> str:
    """Render the member-view events list as iCalendar text.

    One VEVENT per event, in the service's own order. A
    cancelled event rides with STATUS:CANCELLED (it never
    vanishes — a subscriber's calendar must learn of the
    cancellation, which is the B-053 law restated for this
    shelf); place combines name and address as one LOCATION;
    lines are CRLF-joined, as the standard requires.
    """
    lines: list[str] = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GPA//MCP//EN"]
    for event in events:
        all_day = bool(event.get("all_day"))
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{event['event_id']}@gpa")
        starts = _ical_moment(event.get("starts_at"), all_day=all_day)
        ends = _ical_moment(event.get("ends_at"), all_day=all_day)
        if starts:
            suffix = ";VALUE=DATE" if all_day else ""
            lines.append(f"DTSTART{suffix}:{starts}")
        if ends:
            suffix = ";VALUE=DATE" if all_day else ""
            lines.append(f"DTEND{suffix}:{ends}")
        lines.append(f"SUMMARY:{_ical_escape(str(event.get('title') or ''))}")
        place = ", ".join(
            part for part in (event.get("place_name"), event.get("place_address")) if part
        )
        if place:
            lines.append(f"LOCATION:{_ical_escape(place)}")
        if event.get("description"):
            lines.append(f"DESCRIPTION:{_ical_escape(str(event['description']))}")
        if event.get("status") == "cancelled":
            lines.append("STATUS:CANCELLED")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# --- The static shelf -------------------------------------------------------------


def _docs_chapters() -> list[str]:
    """The docs set's chapter file names, in reading order.

    Listed fresh from the directory per call: the shelf is
    what ships, and a listing taken at import time would go
    stale in a long-lived server the day a release swaps the
    tree beneath it. A missing docs directory is an empty
    shelf (the caller's not_found names the gap honestly).
    """
    if not DOCS_DIR.is_dir():
        return []
    return sorted(p.name for p in DOCS_DIR.iterdir() if p.suffix == ".md")


def _chapter_title(path: Path) -> str:
    """One chapter's title: its first Markdown heading line.

    Falls back to the file name when a chapter opens without
    a heading — the index must describe the shelf as it is,
    not as it ought to be.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.name


# --- Registration -------------------------------------------------------------------


def register_resources(server, bridge, guard: Callable[[], Any]) -> None:
    """Register every B-213 resource on one built server.

    ``server`` is the FastMCP object ``gpa.mcp.server`` built;
    ``bridge`` is its one door to the backend; ``guard`` is
    the server's read-scope check (resolves the principal per
    read and refuses without the read scope). Registration is
    a function, not import-time decoration on a global,
    because the server is BUILT per process/test — the shelf
    belongs to the instance, and tests stand up their own.
    """

    def _events(group_id: str) -> list[dict]:
        """The group's events as the member role sees them.

        The one fetch both calendar forms render from, so the
        JSON and iCalendar shelves can never disagree about
        which events a member's calendar holds.
        """
        data = bridge.call(
            "events.list", {"group_id": group_id, "role": RESOURCE_ROLE}
        )
        return data["events"]

    # --- The group's calendar, both forms -------------------------------------

    @server.resource(
        "gpa://groups/{group_id}/calendar.json",
        mime_type="application/json",
    )
    def calendar_json(group_id: str) -> str:
        """The group's calendar as JSON (member view).

        Events in the service's order — drafts absent, per
        the bright line — with the group's id and the count
        stated, so a reader can cite the document's shape
        without recounting it.
        """
        def build() -> str:
            """Compose the calendar document from the one fetch."""
            events = _events(group_id)
            return _json(
                {"group_id": group_id, "events": events, "count": len(events)}
            )

        return _guarded(guard, "events.list", build)

    @server.resource(
        "gpa://groups/{group_id}/calendar.ics",
        mime_type="text/calendar",
    )
    def calendar_ics(group_id: str) -> str:
        """The group's calendar as iCalendar (member view).

        The same fetch as the JSON form, rendered by this
        module's iCalendar writer — the form a calendar
        application subscribes to.
        """
        def build() -> str:
            """Render the shared fetch into iCalendar text."""
            return _render_ical(_events(group_id))

        return _guarded(guard, "events.list", build)

    # --- One event ----------------------------------------------------------------

    @server.resource(
        "gpa://groups/{group_id}/events/{event_id}.json",
        mime_type="application/json",
    )
    def event_json(group_id: str, event_id: str) -> str:
        """One event as JSON (member view).

        Travels events.get at member role: a draft answers
        not_found through the operation itself, exactly as if
        it did not exist — the resource adds no redaction of
        its own, because the door it travels already decides.
        """
        def build() -> str:
            """Read the one event through the operation."""
            data = bridge.call(
                "events.get",
                {"group_id": group_id, "event_id": event_id, "role": RESOURCE_ROLE},
            )
            return _json(data)

        return _guarded(guard, "events.get", build)

    # --- Settled truth ----------------------------------------------------------------

    @server.resource(
        "gpa://groups/{group_id}/decisions.json",
        mime_type="application/json",
    )
    def decisions_json(group_id: str) -> str:
        """The group's CURRENT decisions as JSON.

        The settled-truth view (current_only): superseded
        rows stay at the chain tool, where the history is the
        point; this document answers only "what stands now".
        """
        def build() -> str:
            """Read the standing decisions through the operation."""
            data = bridge.call(
                "planning.list_decisions",
                {"group_id": group_id, "current_only": True},
            )
            return _json(data)

        return _guarded(guard, "planning.list_decisions", build)

    # --- The queue, summarised ----------------------------------------------------------

    @server.resource(
        "gpa://groups/{group_id}/queue.json",
        mime_type="application/json",
    )
    def queue_json(group_id: str) -> str:
        """The review queue as a summary document.

        Totals by status and by kind over the whole queue,
        plus the pending rows themselves (id, kind, title,
        confidence, flags) — the triage shape. Full drafts and
        provenance stay at the queue tools, where a reviewer
        reads one candidate at a time on purpose.
        """
        def build() -> str:
            """Summarise the queue from its own list operation."""
            data = bridge.call("candidates.list", {"group_id": group_id})
            rows = data["candidates"]
            by_status: dict[str, int] = {}
            by_kind: dict[str, int] = {}
            pending: list[dict] = []
            for row in rows:
                by_status[row["status"]] = by_status.get(row["status"], 0) + 1
                by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
                if row["status"] == "pending":
                    pending.append(
                        {
                            "candidate_id": row["candidate_id"],
                            "kind": row["kind"],
                            "title": row["title"],
                            "confidence": row["confidence"],
                            "flags": row["flags"],
                        }
                    )
            return _json(
                {
                    "group_id": group_id,
                    "count": len(rows),
                    "by_status": by_status,
                    "by_kind": by_kind,
                    "pending": pending,
                }
            )

        return _guarded(guard, "candidates.list", build)

    # --- The map of the group's product data ----------------------------------------------

    @server.resource(
        "gpa://groups/{group_id}/memory-map.json",
        mime_type="application/json",
    )
    def memory_map_json(group_id: str) -> str:
        """The map of one group's product data as JSON.

        Composed from five read operations (group facts, the
        roster, the source list, the member-view events, the
        standing decisions, the queue): the group's shape in
        one document — what it is, who is in it, how much it
        holds — with the sibling resources' URIs named, so
        the map is also the shelf's own index for this group.
        Counts are counts of VISIBLE things: events at member
        view, decisions standing, the queue whole (the queue
        is a member-visible surface at its own doors).
        """
        def build() -> str:
            """Compose the map from the read operations."""
            group = bridge.call("groups.get", {"group_id": group_id})
            roster = bridge.call("groups.list_members", {"group_id": group_id})
            sources = bridge.call("sources.list", {"group_id": group_id})
            events = _events(group_id)
            decisions = bridge.call(
                "planning.list_decisions",
                {"group_id": group_id, "current_only": True},
            )
            queue = bridge.call("candidates.list", {"group_id": group_id})
            return _json(
                {
                    "group": group,
                    "counts": {
                        "members": len(roster["members"]),
                        "seats": len(roster["seats"]),
                        "sources": sources["count"],
                        "events": len(events),
                        "decisions_current": decisions["count"],
                        "queue_total": queue["count"],
                        "queue_pending": sum(
                            1
                            for row in queue["candidates"]
                            if row["status"] == "pending"
                        ),
                    },
                    "resources": {
                        "calendar_json": f"gpa://groups/{group_id}/calendar.json",
                        "calendar_ics": f"gpa://groups/{group_id}/calendar.ics",
                        "decisions": f"gpa://groups/{group_id}/decisions.json",
                        "queue": f"gpa://groups/{group_id}/queue.json",
                    },
                }
            )

        return _guarded(guard, "groups.get", build)

    # --- The product's own paper -------------------------------------------------------------

    @server.resource("gpa://openapi.json", mime_type="application/json")
    def openapi_json() -> str:
        """The product's OpenAPI document, verbatim.

        The contract the frontend builds against, readable
        through the same connection an agent works through —
        an agent unsure of a door reads the contract here
        rather than guessing at one.
        """
        def build() -> str:
            """Read the shipped contract document from the tree."""
            if not OPENAPI_PATH.is_file():
                raise AppError(
                    "not_found",
                    detail="The OpenAPI document is not present in this install.",
                )
            return OPENAPI_PATH.read_text(encoding="utf-8")

        return _guarded(guard, "static:openapi", build)

    @server.resource("gpa://docs.json", mime_type="application/json")
    def docs_index_json() -> str:
        """The docs index as JSON: every chapter on the shelf.

        Name, title (the chapter's own first heading), URI,
        and MIME type per chapter — the table of contents an
        agent scans before opening the chapter it needs.
        """
        def build() -> str:
            """List the chapters actually present in the tree."""
            chapters = [
                {
                    "name": name,
                    "title": _chapter_title(DOCS_DIR / name),
                    "uri": f"gpa://docs/{name}",
                    "mime_type": "text/markdown",
                }
                for name in _docs_chapters()
            ]
            return _json({"chapters": chapters, "count": len(chapters)})

        return _guarded(guard, "static:docs-index", build)

    @server.resource("gpa://tool-catalogue.json", mime_type="application/json")
    def tool_catalogue_json() -> str:
        """The B-219 tool catalogue as JSON: tools + digest.

        The catalogue is GENERATED by ``gpa.mcp.catalogue``
        from the same registry the tools are served from —
        every tool's operation, both scopes, annotations,
        description hash, and worked example — and the body
        carries the catalogue digest beside the entries, so
        a reader can recompute and verify what it was handed
        (the conformance runner does exactly that). The
        import is lazy for the shelf's own reason: the
        catalogue module reads the registry and the security
        derivation, and keeping its import inside the read
        keeps this module's import graph exactly B-213's.
        """
        def build() -> str:
            """Compose the catalogue document from its generator."""
            from gpa.mcp.catalogue import catalogue_digest, tool_catalogue

            entries = tool_catalogue()
            return _json(
                {
                    "tools": entries,
                    "count": len(entries),
                    "digest": catalogue_digest(entries),
                }
            )

        return _guarded(guard, "static:tool-catalogue", build)

    @server.resource("gpa://docs/{chapter}", mime_type="text/markdown")
    def docs_chapter(chapter: str) -> str:
        """One docs chapter, verbatim, as Markdown.

        The chapter name must name a file actually present in
        the docs directory (the fresh listing is the whole
        allow-list): anything else — another file, a parent
        path, a nested path — is not_found, so the shelf can
        only ever serve the manual, never the filesystem.
        """
        def build() -> str:
            """Read the named chapter from the allow-listed set."""
            if chapter not in _docs_chapters():
                raise AppError(
                    "not_found",
                    detail=f"No docs chapter named '{chapter}' is on the shelf.",
                )
            return (DOCS_DIR / chapter).read_text(encoding="utf-8")

        return _guarded(guard, "static:docs-chapter", build)
