"""The MCP tool catalogue — worked examples + signed documentation (B-219).

What this module is, and the law it works under:

* **The catalogue is generated, like the registry it documents.**
  B-206 generated the tool SPECS (name, operation, scope,
  description) from the internal operation registry, so the
  served surface and its paper could never drift. This module
  is that paper, completed: for every tool it joins the spec
  to the facts a client author or a reviewer actually needs —
  the internal scope the operation rides, the tool's truthful
  B-218 annotations, its description hash, and ONE curated
  worked example call, in the JSON-RPC shape a client sends.
* **Examples are curated, never capability.** The EXAMPLES
  table below is annotation in B-206's sense: it cannot create
  a tool, widen a scope, or soften a law. Its ids are
  placeholders (``grp_example`` and kin) — an example shows the
  SHAPE of a call, and a real call names real ids the caller
  learned from real reads. Consent-gated tools (B-218's
  ``CONSENT_TOOLS``) carry ``consent: True`` in their examples,
  because an example that omitted the gate would teach a caller
  to make a call this server must refuse.
* **The catalogue is a signed fact and a served resource.**
  :func:`catalogue_digest` hashes the whole catalogue in
  :func:`gpa.mcp.tools.manifest_digest`'s canonical form, so
  the same recomputation discipline that guards descriptions
  (B-206) guards examples and annotations here. Doc 13 carries
  the catalogue's index table as a generated section (the
  drift test compares it byte for byte), and the MCP resource
  ``gpa://tool-catalogue.json`` (B-213's shelf, in
  ``gpa.mcp.resources``) serves the full JSON — an agent can
  read the manual through the same connection it works through.
* **The checker names every drift.** :func:`check_catalogue`
  fails BY TOOL NAME when coverage moves in either direction,
  when a description hash stops recomputing, when annotations
  stop matching the B-218 derivation, when an internal scope
  stops matching the registry, or when an example goes missing
  or drops its consent. A catalogue that cannot be wrong
  quietly is the whole point of generating it.

This module imports the registry's specs, the security
module's annotation derivation, and the internal registry's
scope table. It imports no service, router, or model, builds
no server, and calls no operation — it states the catalogue;
the server serves it and the conformance runner proves it.
"""

from __future__ import annotations

import copy

from gpa.mcp.security import CONSENT_TOOLS, tool_annotations
from gpa.mcp.tools import ToolSpec, all_tool_specs, description_sha256, manifest_digest

# --- The curated example layer --------------------------------------------------
#
# Tool name -> one minimal, well-shaped example argument dict.
# Writing rules for the next editor, mirroring the registry's:
#
# * One entry per served tool, keyed by TOOL name (the surface
#   a caller actually addresses), in catalogue order below so
#   a diff reads as the change itself.
# * Placeholder ids only (grp_example, evt_example, ...): an
#   example teaches a call's shape, never a real group's facts,
#   and no real id may ever be written here.
# * Required arguments always; optional arguments only where
#   the option itself teaches something (a draft body, a chunk
#   list, a changes dict) — the served schema remains the full
#   word on what a call may carry.
# * A consent-gated tool's example states consent: True. The
#   gate (B-218) is part of that call's honest shape; an
#   example without it would be an example of a refused call.

EXAMPLES: dict[str, dict] = {
    # The two meta tools take no arguments at all: their empty
    # dicts are the honest example, and the checker allows an
    # empty example for these two names only.
    "gpa_server_info": {},
    "gpa_list_internal_operations": {},
    # Drafting: the draft body rides along because its presence
    # is the tool's whole point — a title alone drafts a stub.
    "gpa_create_candidate_draft": {
        "group_id": "grp_example",
        "kind": "event",
        "title": "Example supper",
        "created_by": "user:usr_example",
        "draft": {"title": "Example supper"},
    },
    # Publishing is destructive (B-218): consent rides the call.
    "gpa_publish_event": {
        "group_id": "grp_example",
        "event_id": "evt_example",
        "actor": "user:usr_example",
        "consent": True,
    },
    # Event reads carry the viewer's role: visibility is role
    # law (the B-049 bright line), stated in the call itself.
    "gpa_list_events": {"group_id": "grp_example", "role": "member"},
    "gpa_get_event": {
        "group_id": "grp_example",
        "event_id": "evt_example",
        "role": "member",
    },
    # Cancelling is destructive and role-gated: both facts show.
    "gpa_cancel_event": {
        "group_id": "grp_example",
        "event_id": "evt_example",
        "actor": "user:usr_example",
        "role": "organiser",
        "consent": True,
    },
    "gpa_list_rsvps": {"group_id": "grp_example", "event_id": "evt_example"},
    # Answering is the answering principal's own act, so the
    # example names the principal label beside the answer.
    "gpa_set_rsvp": {
        "group_id": "grp_example",
        "event_id": "evt_example",
        "principal_label": "user:usr_example",
        "status": "coming",
    },
    # The agenda window is stated with offsets, as served; the
    # operation converts — an agent never does timezone maths.
    "gpa_get_agenda": {
        "group_id": "grp_example",
        "window_start": "2026-11-01T00:00:00+00:00",
        "window_end": "2026-11-08T00:00:00+00:00",
    },
    "gpa_list_sources": {"group_id": "grp_example"},
    "gpa_get_source": {"group_id": "grp_example", "source_id": "src_example"},
    # Filing carries its permission record (granted scope in
    # the granter's words) and its chunks in order — the two
    # facts an example filer most needs to see stated.
    "gpa_create_source": {
        "group_id": "grp_example",
        "actor": "user:usr_example",
        "kind": "chat_paste",
        "title": "Example chat",
        "granted_scope": "group use",
        "chunks": [{"text": "Hello from the example.", "sender": "Example Person"}],
    },
    # Appending continues the source's one true order: chunks
    # are handed over unnumbered, positions are the service's.
    "gpa_append_source_chunks": {
        "group_id": "grp_example",
        "source_id": "src_example",
        "chunks": [{"text": "One more line.", "sender": "Example Person"}],
    },
    # An extraction request QUEUES a job; the example's shape
    # (actor + role, the commissioning facts) says exactly that.
    "gpa_request_extraction": {
        "group_id": "grp_example",
        "source_id": "src_example",
        "actor": "user:usr_example",
        "role": "organiser",
    },
    "gpa_list_tasks": {"group_id": "grp_example"},
    "gpa_list_decisions": {"group_id": "grp_example"},
    "gpa_get_decision_chain": {
        "group_id": "grp_example",
        "decision_id": "dec_example",
    },
    "gpa_list_questions": {"group_id": "grp_example"},
    # A task edit is a presence-stated changes dict: the field
    # named is set, every other field is untouched.
    "gpa_update_task": {
        "group_id": "grp_example",
        "task_id": "tsk_example",
        "actor": "user:usr_example",
        "changes": {"status": "in_progress"},
    },
    "gpa_resolve_question": {
        "group_id": "grp_example",
        "question_id": "qst_example",
        "actor": "user:usr_example",
    },
    # Asking for a missing fact: the kind is the closed
    # vocabulary's word (place / time / yes_no / free_text).
    "gpa_ask_for_missing_fact": {
        "group_id": "grp_example",
        "question": "Where should the supper be held?",
        "fact_kind": "place",
        "actor": "seat:example",
    },
    # Sampling is gated open-world work: consent rides the call,
    # and the tool itself is off unless the operator enabled it.
    "gpa_request_sampling": {
        "group_id": "grp_example",
        "prompt": "Draft a one-line reminder for the supper.",
        "actor": "seat:example",
        "consent": True,
    },
    "gpa_list_announcements": {"group_id": "grp_example", "role": "member"},
    # Announcing is a PERSON's organiser act: the example names
    # the person's user id, as the operation's law requires.
    "gpa_create_announcement": {
        "group_id": "grp_example",
        "actor": "user:usr_example",
        "role": "organiser",
        "title": "Example announcement",
        "body": "The supper is on.",
        "user_id": "usr_example",
    },
    "gpa_get_inbox": {"user_id": "usr_example"},
    "gpa_get_notification_preferences": {
        "group_id": "grp_example",
        "user_id": "usr_example",
    },
    "gpa_list_digests": {"group_id": "grp_example"},
    # Composing a digest preview is an organiser act; the
    # composed digest is stored UNSENT — sending is not here.
    "gpa_preview_digest": {"group_id": "grp_example", "role": "organiser"},
    # A notification request writes ROWS (the outbox law — it
    # never sends); its audience names a label or an event.
    "gpa_request_notification": {
        "kind": "announcement",
        "subject": "Example subject",
        "body_text": "Example body.",
        "audience": {"label": "user:usr_example", "user_id": "usr_example"},
        "group_id": "grp_example",
    },
    "gpa_notify_rsvp_promotion": {
        "group_id": "grp_example",
        "event_id": "evt_example",
        "rsvp_id": "rsv_example",
    },
    "gpa_enqueue_job": {"kind": "notify"},
    "gpa_submit_ai_job": {"kind": "extraction"},
    # Emitting announces outward (open-world, consent-gated):
    # the example carries an explicit data payload + consent.
    "gpa_emit_integration_event": {
        "group_id": "grp_example",
        "kind": "event.published",
        "data": {"example": True},
        "consent": True,
    },
    "gpa_get_group": {"group_id": "grp_example"},
    "gpa_list_members": {"group_id": "grp_example"},
    # Group settings travel the admin rung and only the five
    # plain fields; the example edits exactly one of them.
    "gpa_update_group": {
        "group_id": "grp_example",
        "actor": "user:usr_example",
        "name": "Example Group",
    },
    # A profile read names exactly one principal: this example
    # reads a person's profile by user id (the spec's shape).
    "gpa_get_profile": {"user_id": "usr_example"},
    "gpa_update_profile": {
        "actor": "user:usr_example",
        "user_id": "usr_example",
        "name": "Example Person",
    },
    "gpa_list_candidates": {"group_id": "grp_example"},
    "gpa_get_candidate": {
        "group_id": "grp_example",
        "candidate_id": "cand_example",
    },
    # Deciding is publish-grade and destructive: role + consent
    # both ride the approval example, as the one law demands.
    "gpa_approve_candidate": {
        "group_id": "grp_example",
        "candidate_id": "cand_example",
        "actor": "user:usr_example",
        "role": "organiser",
        "consent": True,
    },
    "gpa_reject_candidate": {
        "group_id": "grp_example",
        "candidate_id": "cand_example",
        "actor": "user:usr_example",
        "consent": True,
    },
    # Editing replaces the draft and stays UNDECIDED; the
    # example's whole draft is the one corrected field's shape.
    "gpa_edit_candidate": {
        "group_id": "grp_example",
        "candidate_id": "cand_example",
        "actor": "user:usr_example",
        "draft": {"title": "Corrected example title"},
    },
}

# The only tools whose honest example is the empty dict: the
# two no-argument meta tools. The checker allows {} for these
# names and no others — an operation tool with an empty
# example would teach a caller to call it with nothing.
NO_ARGUMENT_TOOLS = frozenset({"gpa_server_info", "gpa_list_internal_operations"})


# --- Building the catalogue -------------------------------------------------------


def _internal_scope(spec: ToolSpec) -> str | None:
    """One spec's required internal scope, from the registry.

    Operation tools report their operation's required scope —
    the scope a caller of the internal API must hold — read
    from ``gpa.internal.registry`` after registration, the
    same table the registry checker reads. Meta tools travel
    no operation and honestly report None.
    """
    # Registration is lazy in the internal API, exactly as in
    # gpa.mcp.tools.operation_specs: register first, or a cold
    # process would read an empty registry and report None
    # for real operations — a lie this function must not tell.
    from gpa.internal import handlers, registry

    handlers.register_all()
    if spec.operation is None:
        return None
    operation = registry.OPERATIONS.get(spec.operation)
    return operation.required_scope if operation is not None else None


def tool_catalogue() -> list[dict]:
    """The full tool catalogue: one entry per served tool.

    Entries stand in ``all_tool_specs`` order (meta tools
    first, then operation tools in operation-name order), so
    two builds of an unchanged registry produce byte-identical
    catalogues and the digest below means something. Each
    entry joins every surface's facts in one dict:

    * ``name`` / ``kind`` / ``operation`` — the tool's identity
      (``operation`` is None for the two meta tools);
    * ``credential_scope`` — the B-205 ladder word a call
      requires; ``internal_scope`` — the internal scope the
      travelled operation requires (None for meta tools);
    * ``description`` + ``description_sha256`` — the served
      words and their B-206 hash, recomputed here so the
      catalogue can be verified without trusting it;
    * ``annotations`` — the tool's four truthful B-218 hints;
    * ``example`` — the curated example arguments (a deep
      copy: a caller mutating an entry must never rewrite
      the curated table every later build reads);
    * ``example_call`` — that example as the JSON-RPC call a
      client actually sends (``tools/call``).
    """
    entries: list[dict] = []
    for spec in all_tool_specs():
        # A spec with no curated example is a build error, not
        # a gap to paper over: generation is total or it does
        # not happen, in the registry module's own discipline.
        if spec.name not in EXAMPLES:
            raise ValueError(
                f"MCP tool '{spec.name}' has no curated catalogue "
                "example — add its line to gpa.mcp.catalogue.EXAMPLES"
            )
        example = copy.deepcopy(EXAMPLES[spec.name])
        entries.append(
            {
                "name": spec.name,
                "kind": spec.kind,
                "operation": spec.operation,
                "credential_scope": spec.credential_scope,
                "internal_scope": _internal_scope(spec),
                "description": spec.description,
                "description_sha256": description_sha256(spec.description),
                "annotations": tool_annotations(spec),
                "example": example,
                "example_call": {
                    "method": "tools/call",
                    "params": {"name": spec.name, "arguments": example},
                },
            }
        )
    return entries


def catalogue_digest(entries: list[dict]) -> str:
    """One SHA-256 over catalogue entries, canonically.

    The canonical form is the manifest's own (compact JSON,
    sorted keys, entries in catalogue order) — this function
    delegates to :func:`gpa.mcp.tools.manifest_digest` so the
    catalogue and the release manifest can never grow two
    different ideas of what a digest covers. Any change — a
    reworded example, a flipped annotation, a re-scoped tool —
    changes this digest, which is what Doc 13 prints beside
    the catalogue and the served resource carries in its body.
    """
    return manifest_digest(entries)


# --- The checker --------------------------------------------------------------------


def check_catalogue(entries: list[dict] | None = None) -> list[str]:
    """Every catalogue failure, each naming its tool.

    An empty list is a sound catalogue. The legs, in order:

    * coverage, both directions against ``all_tool_specs`` —
      a tool with no entry is undocumented surface; an entry
      naming no served tool is documentation of a fiction;
    * each entry's ``description_sha256`` recomputes from its
      own ``description`` (the B-206 tripwire, per entry);
    * each entry's annotations equal the B-218 derivation for
      its spec — a hand-flipped hint fails here by name;
    * each operation entry's ``internal_scope`` equals the
      registry's required scope for its operation, and a meta
      entry's is None;
    * every entry carries an example: a dict, present in the
      entry, empty only for the two no-argument meta tools,
      with string keys throughout;
    * every consent-gated tool's example states
      ``consent: True`` — the gate is part of the call's
      shape, and an example that drops it teaches a refusal;
    * each entry's ``example_call`` is the example, addressed
      to the entry's own tool by the ``tools/call`` method.
    """
    entries = tool_catalogue() if entries is None else entries
    specs = {spec.name: spec for spec in all_tool_specs()}
    failures: list[str] = []

    # Coverage first, both directions, because every later leg
    # compares an entry against its spec and a missing spec
    # would turn a coverage failure into a confusing KeyError.
    seen: set[str] = set()
    for entry in entries:
        name = entry.get("name")
        if name in seen:
            failures.append(f"{name}: duplicate catalogue entry")
        seen.add(name)
        if name not in specs:
            failures.append(f"{name}: catalogue entry names no served tool")
    for name in sorted(specs):
        if name not in seen:
            failures.append(f"{name}: served tool has no catalogue entry")

    for entry in entries:
        name = entry.get("name")
        spec = specs.get(name)  # type: ignore[arg-type]
        if spec is None:
            # Already failed by name in the coverage leg; the
            # per-entry legs have no spec to compare against.
            continue
        # The description hash must recompute from the entry's
        # own words: a swapped description with a stale hash
        # is exactly the tamper B-206 taught the release to see.
        if entry.get("description_sha256") != description_sha256(
            str(entry.get("description", ""))
        ):
            failures.append(f"{name}: description_sha256 does not recompute")
        # Annotations are derived facts (B-218): the entry must
        # carry the derivation's answer, not a hand-tuned one.
        if entry.get("annotations") != tool_annotations(spec):
            failures.append(f"{name}: annotations differ from the B-218 derivation")
        # The internal scope is the registry's fact about the
        # operation; the catalogue reports it, never decides it.
        if entry.get("internal_scope") != _internal_scope(spec):
            failures.append(
                f"{name}: internal_scope differs from the registry's "
                "required scope for its operation"
            )
        # The example legs: presence, shape, and the consent law.
        example = entry.get("example")
        if not isinstance(example, dict):
            failures.append(f"{name}: catalogue example is missing or not an object")
        else:
            if example == {} and name not in NO_ARGUMENT_TOOLS:
                failures.append(f"{name}: catalogue example is empty for a tool that takes arguments")
            if any(not isinstance(key, str) for key in example):
                failures.append(f"{name}: catalogue example has a non-string key")
            if name in CONSENT_TOOLS and example.get("consent") is not True:
                failures.append(
                    f"{name}: consent-gated tool's example does not state consent=True"
                )
        # The example call must be the example, addressed home:
        # a call naming another tool would send a reader's
        # first experiment to the wrong door.
        example_call = entry.get("example_call")
        if (
            not isinstance(example_call, dict)
            or example_call.get("method") != "tools/call"
            or example_call.get("params", {}).get("name") != name
            or example_call.get("params", {}).get("arguments") != example
        ):
            failures.append(f"{name}: example_call is not the example addressed to this tool")
    return failures


# --- The documentation rendering -----------------------------------------------------


def _annotation_letters(annotations: dict[str, bool]) -> str:
    """One tool's hints as the doc table's compact letters.

    R (read-only), D (destructive), I (idempotent), O
    (open-world), in that fixed order, joined without a
    separator; a tool with no hint set renders as an em dash,
    so an empty cell can never be mistaken for a lost value.
    The full hint words live in the JSON resource — the table
    is the index, and an index must stay scannable.
    """
    letters = ""
    if annotations.get("readOnlyHint"):
        letters += "R"
    if annotations.get("destructiveHint"):
        letters += "D"
    if annotations.get("idempotentHint"):
        letters += "I"
    if annotations.get("openWorldHint"):
        letters += "O"
    return letters or "—"


def render_catalogue_markdown() -> str:
    """The catalogue as Doc 13's generated index table.

    Deterministic and compact by design: an intro line stating
    the counts and the catalogue digest, then one table row
    per tool in catalogue order — tool, operation, credential
    scope, internal scope, annotation letters. Full
    descriptions and worked examples live in the JSON resource
    (``gpa://tool-catalogue.json``); this table is the index a
    reader scans, and ``scripts/generate_mcp_catalogue.py``
    regenerates it between Doc 13's markers, so the chapter
    and this function can never carry two catalogues. The
    returned text carries no trailing newline: the generator
    owns the section's framing newlines, exactly.
    """
    entries = tool_catalogue()
    digest = catalogue_digest(entries)
    # The counts are computed, never typed: a hand-typed count
    # in the intro line would be the first thing to go stale.
    operations = sum(1 for entry in entries if entry["kind"] == "operation")
    meta = len(entries) - operations
    lines = [
        (
            f"The MCP tool catalogue: {len(entries)} tools "
            f"({operations} operation tools, {meta} meta tools). "
            f"Catalogue digest: `{digest}`. "
            "Annotation letters: R = read-only, D = destructive, "
            "I = idempotent, O = open-world."
        ),
        "",
        "| Tool | Operation | Credential scope | Internal scope | Annotations (R/D/I/O letters) |",
        "|---|---|---|---|---|",
    ]
    for entry in entries:
        # Meta tools travel no operation and hold no internal
        # scope: both cells render as an em dash, the table's
        # one spelling of "none, by design".
        lines.append(
            "| {name} | {operation} | {credential} | {internal} | {letters} |".format(
                name=entry["name"],
                operation=entry["operation"] or "—",
                credential=entry["credential_scope"],
                internal=entry["internal_scope"] or "—",
                letters=_annotation_letters(entry["annotations"]),
            )
        )
    return "\n".join(lines)
