"""The MCP tool registry — one source of truth (roadmap B-206).

What this module is, and the law it makes mechanical:

* **Generated, not hand-listed.** The MCP tool catalogue is
  GENERATED from the internal operation registry (B-162) plus
  the curated annotation layer in this file. An operation that
  is registered on the internal API has exactly one MCP tool;
  an MCP operation tool names exactly one registered operation.
  The two directions are both checked — by
  :func:`check_tool_registry` here and by the B-201 parity
  checker, which now runs this check as its MCP leg — so a
  capability cannot exist on one surface and be missing from
  the other without a test failing BY NAME.
* **The curated layer is annotation, never capability.** The
  annotation table below gives each operation its tool name,
  its served description, and the credential scope a call to it
  requires. It cannot create a capability: an annotation naming
  no registered operation is a checker failure, and an operation
  with no annotation is a checker failure too — the fix in both
  cases is a deliberate act in this file, visible in review.
* **Meta tools are declared, not smuggled.** Two tools are not
  operation-backed: ``gpa_server_info`` and
  ``gpa_list_internal_operations`` report on the server itself
  and carry no group content. They are registered here with
  ``kind="meta"`` and ``operation=None`` so the checker can tell
  a declared meta tool from an unregistered capability.
* **Descriptions are release facts (tool-poisoning defence).**
  The description an agent reads is part of how it decides to
  call a tool; a swapped description is a swapped instruction.
  :func:`tool_manifest` therefore emits every tool's name,
  operation, scope, and description together with a SHA-256 of
  the description and one digest over the whole manifest, and
  the release builder (scripts/make_release_zip.py) stamps both
  into the SIGNED release manifest — a release whose served
  descriptions differ from its manifest's is a tampered
  release, detectable by anyone holding the manifest.
* **Scope law.** An operation tool's credential scope must map,
  through B-205's ``EXTERNAL_TO_INTERNAL`` table, onto the
  operation's required internal scope. The checker enforces it,
  so a tool can never be callable by a credential whose mapped
  internal scopes could not carry its operation.

This module imports the internal registry and (lazily, inside
the checker) the handlers that fill it — the same door the rest
of the MCP package uses. It imports no service, router, or
model directly, and it builds no server: ``gpa.mcp.server``
consumes these specs, it does not restate them.

How generation walks, step by step
----------------------------------
1. ``handlers.register_all()`` fills the internal registry —
   registration is lazy in the internal API by design (the
   client's first call does it), and a generator that read the
   registry cold would generate from an empty table and call
   the result a catalogue.
2. For each registered operation, in name order, the curated
   annotation layer is consulted. A hit becomes a ToolSpec
   verbatim: the annotation's tool name, scope, and description
   are the spec's, because those words ARE the release facts.
3. A miss raises, naming the operation. There is no default
   description and no default scope to fall back to: a new
   operation is a new capability, and a new capability's words
   and scope are a reviewer's decision, recorded in this file.
4. The meta tools are prepended from their own declared tuple.
   They are not generated (they travel no operation) and they
   are not hidden — a reader of ``all_tool_specs`` sees the
   whole served catalogue in one list, kinds labelled.

How the checker walks, and why each leg exists
----------------------------------------------
* Coverage, both directions, because B-206's sentence is
  two-directional: an operation without a tool is a capability
  MCP users cannot reach; a tool naming no operation is a
  promise the backend cannot keep. Either alone is drift.
* Uniqueness first, because every later leg compares sets, and
  a duplicated name would let one tool impersonate another in
  a set comparison and pass a check it should fail.
* The scope-mapping leg, because a tool's credential scope is
  only a word until it is mapped: B-205's table says which
  internal scopes a credential's scopes become, and a tool
  demanding a credential that cannot carry its operation would
  be a door that opens onto a refusal — worse, a door whose
  refusal looks like a bug instead of a law.
* The enforcement-table leg, because the call-time guard reads
  ``TOOL_SCOPES`` from the auth module (B-205's seam, kept):
  two tables that can disagree are two sources of truth, so
  the checker makes disagreement a failure by name, per tool.
* The served-surface leg, when a caller passes a built server's
  tool names, because a registry can be perfect on paper while
  the server quietly serves something else; comparing against
  the live list closes the last gap between paper and wire.

The manifest form, and what a verifier does with it
---------------------------------------------------
``tool_manifest`` emits plain JSON-ready dicts — the release
builder stamps them, and the SHA256SUMS + Ed25519 signature
over the ZIP cover them like every other manifest fact. The
verifier (scripts/release_signing.py) recomputes each entry's
description hash and the whole-list digest from the manifest
itself, so a manifest whose descriptions were swapped after
stamping refuses its own verification; comparing a manifest
against a LIVE server's served descriptions (the registry
tests do exactly this against the specs) catches the other
direction — a server whose words moved after its release.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool, as the registry declares it.

    ``kind`` is ``"operation"`` (the tool travels one registered
    internal operation through the bridge) or ``"meta"`` (the
    tool reports on the server itself; ``operation`` is None).
    ``credential_scope`` is the B-014/B-205 ladder word a caller
    must hold at call time; the server's guard reads it through
    ``gpa.mcp.auth.TOOL_SCOPES``, and the checker below asserts
    the two tables agree — there is one truth, stated here, and
    the auth table is its enforcement copy.
    """

    # The tool's wire name (``gpa_``-prefixed, snake case).
    name: str
    # The internal operation this tool travels, or None (meta).
    operation: str | None
    # The description the server serves for this tool, verbatim.
    description: str
    # The credential scope a call requires (read/draft/publish/admin).
    credential_scope: str
    # "operation" or "meta" — see the class docstring.
    kind: str


# --- The curated annotation layer -------------------------------------------
#
# Operation name -> (tool name, credential scope, description).
# This is the ONLY hand-written part of the catalogue, and it is
# annotation: a name an agent can remember, a scope a reviewer
# can judge, and the exact words an agent will read. Adding an
# operation to the internal API without adding its line here
# fails the checker by name — deliberately, so the catalogue
# grows by decision, never by drift.
#
# How to write an annotation line, for the next editor:
#
# * The tool name is ``gpa_`` + a verb phrase an agent can
#   guess the meaning of before reading the description
#   (``gpa_enqueue_job``, not ``gpa_jobs_enqueue`` — the agent
#   reads names first, module paths second, if ever).
# * The description states, in order: what the tool does, the
#   road it travels (the internal operation it names), and the
#   law that binds the act (draft-never-publication, outbox
#   rows-never-sends, the AI door's kind check). An agent that
#   reads only descriptions should still learn the house law
#   from them — descriptions are instructions, which is
#   precisely why the signed manifest carries them verbatim.
# * The description never promises what the operation refuses:
#   if the handler can refuse (and every handler can), the
#   description names the refusal or stays silent about the
#   outcome — it never guarantees success.
# * The scope is the LEAST credential scope whose mapped
#   internal scopes (B-205's table) include the operation's
#   required scope. Least, not most convenient: the checker
#   enforces "can carry", and review enforces "least that can".
# * One line per operation, in operation-name order, so the
#   diff of a catalogue change reads as the change itself.

ANNOTATIONS: dict[str, tuple[str, str, str]] = {
    "candidates.create": (
        "gpa_create_candidate_draft",
        "draft",
        "Draft one candidate into a group's review queue. A draft, never "
        "a publication: the candidate lands pending, exactly as a human's "
        "draft does, and approval is a separate act under the one "
        "approval law. Kinds are the queue's own (event, task, decision, "
        "question, and their kin).",
    ),
    # B-208's queue family, annotated by the same rules: the
    # reads take "read" (candidates:read is the read rung's),
    # and the three decisions take "publish" — never "draft".
    # That asymmetry is the queue's law stated in scopes: a
    # seat's default key (read+draft) can draft INTO the queue
    # and read it back, but deciding is a publish-grade act at
    # every door this product has, and the one approval law
    # (B-203) inside the approve operation checks the approver
    # AGAIN — role floor and approver count — so this scope is
    # the door, not the whole guard.
    # A note on the approve annotation's wording, since these
    # descriptions are release facts an agent acts on: it says
    # the law applies and names its outcomes (recorded vs
    # deciding, the mint), and it does NOT promise the caller
    # their approval will decide — whether it does depends on
    # the group's own rules and on who has already approved,
    # which is exactly what gpa_get_candidate's progress fields
    # exist to tell the caller BEFORE they act. The reject and
    # edit descriptions likewise state what stays true after
    # the act (the row stays; the candidate stays undecided),
    # because a reviewer planning a sequence of calls needs
    # the aftermath, not just the act.
    "candidates.list": (
        "gpa_list_candidates",
        "read",
        "List one group's review queue through the internal "
        "candidates.list operation — newest first, with the "
        "queue's own triage filters (kind, status, minimum "
        "confidence). Rows only: drafts, flags, and who drafted "
        "each; deciding anything on the list is a separate, "
        "publish-grade act at the decision tools.",
    ),
    "candidates.get": (
        "gpa_get_candidate",
        "read",
        "Read one candidate in full through the internal "
        "candidates.get operation: its draft, its server-computed "
        "flags, the per-field provenance (the quote and support "
        "verdict behind every field), and its approval progress "
        "under the group's multi-approver rule — everything a "
        "reviewer weighs before a decision, and nothing decided.",
    ),
    "candidates.approve": (
        "gpa_approve_candidate",
        "publish",
        "Approve one candidate through the internal "
        "candidates.approve operation — the one approval law "
        "(B-203), the same service act a human reviewer's "
        "approval performs: the group's approver-role floor and "
        "distinct-approver count apply to seats and people "
        "identically, a below-count approval is recorded and "
        "the candidate stays pending, and a deciding approval "
        "mints what the draft becomes. No second door exists.",
    ),
    "candidates.reject": (
        "gpa_reject_candidate",
        "publish",
        "Reject one candidate through the internal "
        "candidates.reject operation. The row, its draft, and "
        "its provenance all stay — a rejection is a decision "
        "about the draft, never an erasure of it — and a decided "
        "candidate cannot be decided again.",
    ),
    "candidates.edit": (
        "gpa_edit_candidate",
        "publish",
        "Edit one candidate's draft through the internal "
        "candidates.edit operation: a reviewer's correction, "
        "replacing the draft and recomputing its flags. The "
        "candidate is marked edited and stays UNDECIDED — "
        "editing is not approving, and the queue still waits "
        "for a decision afterwards.",
    ),
    # B-209's family (events / RSVPs / calendar): three rungs
    # for three kinds of act, each decided by the HTTP door
    # for the same act — reads at "read", answering at
    # "draft" (participation; a seat's default key can answer
    # for its seat), cancelling at "publish" (an organiser
    # act). The read annotations say the bright line out loud:
    # an agent reading as a member must not be surprised that
    # drafts are absent rather than redacted.
    #
    # Note the asymmetry an agent will meet between the two
    # list tools in this family: the EVENTS list is
    # role-filtered (its rows depend on who asks), while the
    # RSVP list is not — answers to a published event are
    # group-common knowledge, the same for every member, so
    # its tool takes no role. And the agenda's description
    # promises occurrences, not events: a weekly supper asked
    # about for November is five rows, not one, and the
    # annotation says so before the caller counts them.
    #
    # The cancel annotation earns its length: it states the
    # aftermath (the event stays, marked; the answered are
    # told at once) and the refusal (below organiser), because
    # cancellation is the one act in this family whose effects
    # reach people who never called anything — an agent
    # weighing it needs the whole shape of the act in the
    # description it reads before deciding, not in an error
    # afterwards.
    "events.list": (
        "gpa_list_events",
        "read",
        "List a group's events through the internal events.list "
        "operation, as the given role may see them: published "
        "and cancelled events for any member; drafts only at "
        "organiser role and above — a draft below that role is "
        "absent from the list, never shown redacted.",
    ),
    "events.get": (
        "gpa_get_event",
        "read",
        "Read one event through the internal events.get "
        "operation, under the same visibility law as the list: "
        "a draft asked for below organiser role answers "
        "not_found, exactly as if it did not exist.",
    ),
    "events.cancel": (
        "gpa_cancel_event",
        "publish",
        "Cancel one event through the internal events.cancel "
        "operation, with the canceller's reason. Cancellation "
        "is a state, never a disappearance: the event stays, "
        "marked cancelled everywhere it was shown, and the "
        "people who answered are told at once. The operation "
        "refuses a role below organiser.",
    ),
    "rsvps.list": (
        "gpa_list_rsvps",
        "read",
        "List one event's RSVPs through the internal "
        "rsvps.list operation, in arrival order, with the "
        "summary counts (coming, waiting, maybe, can't) a "
        "calendar surface shows.",
    ),
    "rsvps.set": (
        "gpa_set_rsvp",
        "draft",
        "Set one principal's own RSVP answer through the "
        "internal rsvps.set operation: 'coming', 'cant', or "
        "'maybe', with guests and a note. Answering is the "
        "principal's own act — the operation upserts their "
        "answer and runs the waitlist rebalance, and a "
        "cancelled event refuses answers in plain words.",
    ),
    "calendar.agenda": (
        "gpa_get_agenda",
        "read",
        "Read one page of a group's agenda through the "
        "internal calendar.agenda operation: occurrences "
        "(recurrences expanded) between window_start and "
        "window_end, soonest first. Pass next_cursor back "
        "unchanged for the following page; it is opaque.",
    ),
    # B-210's family (sources / extraction requests): filing
    # is "draft" (handing the group material is a member act
    # at its door), reading is "read", and commissioning a
    # run is "publish" — the split is the family's law, and
    # the annotations say which side of it each tool stands
    # on, including the request tool's honest limit: it
    # QUEUES the run; the candidates arrive when the worker
    # runs it, not when this call returns.
    #
    # The create annotation carries the permission record
    # explicitly because it is the one argument set in this
    # family an agent might otherwise treat as boilerplate:
    # granted_by and granted_scope are not bookkeeping ABOUT
    # the filing, they are part of the filing's legitimacy —
    # the record a later provenance question ("who said we
    # could use this chat?") is answered from. An agent
    # filing on a member's behalf must carry THEIR grant, in
    # their words, not invent a scope to fill the field.
    #
    # The append annotation's "never number chunks yourself"
    # is load-bearing guidance, not style: provenance (B-208's
    # reads) cites chunks BY position, so a caller-assigned
    # numbering that forked from the service's would corrupt
    # every quote pointing into the source. The service owns
    # positions for exactly the reason the registry owns tool
    # generation: one numbering, stated in one place.
    "sources.list": (
        "gpa_list_sources",
        "read",
        "List a group's sources through the internal "
        "sources.list operation: kind, title, status, who "
        "granted the material and under what scope, and how "
        "many chunks each holds. Metadata only — no chunk "
        "text is opened to build the list.",
    ),
    "sources.get": (
        "gpa_get_source",
        "read",
        "Read one source in full through the internal "
        "sources.get operation: its metadata plus every "
        "chunk in position order, text and sender opened as "
        "the group may read them. Positions are the order "
        "provenance quotes point into.",
    ),
    "sources.create": (
        "gpa_create_source",
        "draft",
        "File one new source through the internal "
        "sources.create operation, with its chunks in order. "
        "The permission record is part of the act: granted_by "
        "is your actor label and granted_scope travels "
        "verbatim onto the row — a source never exists "
        "without the record of who granted the group its "
        "material.",
    ),
    "sources.append": (
        "gpa_append_source_chunks",
        "draft",
        "Append chunks to an existing source through the "
        "internal sources.append operation. Positions "
        "continue the source's one true order; you hand over "
        "an ordered list and never number chunks yourself.",
    ),
    "extraction.request": (
        "gpa_request_extraction",
        "publish",
        "Ask for an extraction run over one source through "
        "the internal extraction.request operation "
        "(organiser role and above). A durable job is QUEUED "
        "and its id returned; the run itself is the worker's, "
        "and the candidates it drafts land in the review "
        "queue when it runs — this call commissions the work, "
        "it does not perform it.",
    ),
    # B-211's family (planning: tasks, decisions, questions):
    # reads at "read", upkeep at "draft" — the family's doors
    # are member doors, and the annotations keep two
    # distinctions visible that an agent could blur: a task's
    # SUGGESTED owner is evidence from the source words, not
    # an assignment; and "what did we decide?" is answered by
    # the CHAIN, not by whichever decision row is newest.
    #
    # The resolve annotation states both of its shapes —
    # with words (truth minted) and without (a plain close)
    # — because an agent choosing between them is making the
    # group's epistemic decision for it: some questions end
    # because they were ANSWERED, and some end because they
    # stopped mattering, and the record should say which.
    # The annotation is where that choice is framed; the
    # service's law (a settled question refuses a second
    # settling) is what makes the framing consequential.
    #
    # The task list's annotation likewise teaches the
    # owner/suggested-owner distinction at the point of
    # reading, not only in the model's comments: an agent
    # that reported "Ben owns the hall booking" from a
    # suggestion would be inventing an assignment the group
    # never made — the exact failure this backend's planning
    # law (draft words are evidence, never assignments) was
    # written against, restated here because this is the
    # surface where an agent could commit it.
    "planning.list_tasks": (
        "gpa_list_tasks",
        "read",
        "List a group's tasks through the internal "
        "planning.list_tasks operation, soonest-due first "
        "(undated last). Each row states its owner — and, "
        "separately, its SUGGESTED owner: the words the "
        "source used, kept as evidence. A suggestion is "
        "never an assignment.",
    ),
    "planning.list_decisions": (
        "gpa_list_decisions",
        "read",
        "List a group's decisions log through the internal "
        "planning.list_decisions operation, newest first. "
        "With current_only, only the standing decisions — "
        "the settled-truth view; superseded rows remain "
        "walkable through the chain tool.",
    ),
    "planning.decision_chain": (
        "gpa_get_decision_chain",
        "read",
        "Walk one decision's whole supersede chain through "
        "the internal planning.decision_chain operation, "
        "oldest first: what was first decided, what replaced "
        "it, and what stands now. Answer 'what did we "
        "decide?' from the chain, never from one row.",
    ),
    "planning.list_questions": (
        "gpa_list_questions",
        "read",
        "List a group's questions through the internal "
        "planning.list_questions operation, oldest open "
        "first — the group's unsettled business in the "
        "order it was asked.",
    ),
    "planning.update_task": (
        "gpa_update_task",
        "draft",
        "Edit one task through the internal "
        "planning.update_task operation: title, notes, due "
        "moment, status, owner. Naming an owner ASSIGNS the "
        "task (a member of the group); an explicit null "
        "owner unassigns it — both are recorded acts, "
        "neither is a suggestion.",
    ),
    # B-216's bridge (elicitation): asking is a draft-grade
    # act — it files a question and informs drafts, and its
    # annotation closes with the limit, like B-212's digest
    # annotation did, because the dangerous misunderstanding
    # here is identical: an agent mistaking a human's ANSWER
    # for a human's APPROVAL. The operation rides the
    # planning write scope (it creates a planning row — an
    # open question — through the planning service's door).
    "elicitation.ask": (
        "gpa_ask_for_missing_fact",
        "draft",
        "Ask the human for one missing fact through the "
        "internal elicitation.ask operation: where the "
        "client supports elicitation the server asks live; "
        "otherwise the question is filed as one of the "
        "group's open questions with your identity attached "
        "and routed to the organiser inbox. Kinds: place, "
        "time, yes_no, free_text. An answer can inform a "
        "draft; it can never be an approval — the draft "
        "still faces the review queue.",
    ),
    # B-217's sampling policy (sampling): the tool is the
    # server's one request for the CLIENT's model, and its
    # annotation closes with the posture, because the
    # dangerous misunderstanding here is an agent assuming
    # sampling is a normal, always-on capability: it is off
    # by default, an operator enables it, and every call is
    # budget-checked and audit-logged before the client is
    # asked. The operation rides the sampling request scope,
    # which maps only from the publish credential rung
    # (commissioning model work is publish-grade, B-210).
    "sampling.sample": (
        "gpa_request_sampling",
        "publish",
        "Ask the connected client's own model to generate "
        "text through the internal sampling.sample operation "
        "— server-initiated sampling, OFF by default and "
        "enabled only by the operator (GPA_MCP_SAMPLING_"
        "ENABLED), because it spends the client operator's "
        "model budget on a server-chosen prompt. Where "
        "enabled, each call is budget-checked on the group's "
        "AI ledger and audit-logged before the client is "
        "asked, and the returned text is data, never "
        "instructions.",
    ),
    "planning.resolve_question": (
        "gpa_resolve_question",
        "draft",
        "Resolve one open question through the internal "
        "planning.resolve_question operation. With "
        "decision_text, the answer is minted as a Decision "
        "in the same act and linked from the question — "
        "settled truth, not a note. Without it, the question "
        "simply closes. A settled question refuses a second "
        "resolution with conflict.",
    ),
    # B-212's family (announcements / notifications /
    # digests): reads at "read", the two organiser acts at
    # "publish". The create annotation states the person law
    # BEFORE the caller tries it: announcing as a seat is
    # refused by the backend's own law, and the description
    # is where an agent learns that — not from a failed call
    # it might retry under a borrowed label.
    #
    # The preferences annotation is written as a question —
    # "what WOULD reach them, and when would it be held" —
    # because that is the question the read exists to
    # answer: not the raw switches (a screen renders those)
    # but their consequence for one person in one group,
    # quiet hours included. An agent planning to notify
    # someone reads this first and learns whether the
    # message would land now, land later, or not land —
    # and the digest preview annotation likewise closes
    # with its limit ("nothing is delivered by this call")
    # rather than its capability, since the dangerous
    # misunderstanding here is overreach, not underuse.
    "announcements.list": (
        "gpa_list_announcements",
        "read",
        "List a group's announcement board through the "
        "internal announcements.list operation: pinned "
        "first, then newest. Expired posts appear only when "
        "include_expired is asked AND the given role is "
        "organiser or above — the board's own rule.",
    ),
    "announcements.create": (
        "gpa_create_announcement",
        "publish",
        "Post one announcement through the internal "
        "announcements.create operation. Announcing is a "
        "PERSON's act: the caller must name a person "
        "(user_id) standing at organiser role or above — a "
        "seat is refused by the backend's own law, in its "
        "own words, however the call is dressed.",
    ),
    "notifications.inbox": (
        "gpa_get_inbox",
        "read",
        "Read one person's notification inbox through the "
        "internal notifications.inbox operation: the same "
        "rows the email channel sends from, across their "
        "groups, with the unread count stated. Inboxes "
        "belong to people — there is no seat inbox to read.",
    ),
    "notifications.preferences": (
        "gpa_get_notification_preferences",
        "read",
        "Read one person's effective notification policy in "
        "one group through the internal "
        "notifications.preferences operation: every channel "
        "× category switch with defaults filled in, plus "
        "their quiet hours — what WOULD reach them, and "
        "when it would be held.",
    ),
    "digests.list": (
        "gpa_list_digests",
        "read",
        "List a group's stored digests through the internal "
        "digests.list operation, newest first — composed "
        "previews and sent runs alike, told apart by "
        "sent_at.",
    ),
    "digests.preview": (
        "gpa_preview_digest",
        "publish",
        "Compose this week's digest through the internal "
        "digests.preview operation and store it UNSENT "
        "(organiser role and above): the week's published "
        "events, unclaimed tasks, open questions and "
        "undecided polls, in the digest's own words. Nothing "
        "is delivered by this call — sending stays at the "
        "human door.",
    ),
    "events.publish": (
        "gpa_publish_event",
        "publish",
        "Publish one draft event through the internal events.publish "
        "operation — the same operation a human organiser's approval "
        "calls. This tool adds no second road around it, and the "
        "operation's own law (draft state, the service's permission "
        "checks) applies unchanged.",
    ),
    # B-207's family (groups / members / profile), annotated by
    # the same rules as every line here, with its scope choices
    # stated for the reviewer: the two reads take "read"
    # because their operations' internal scopes (groups:read,
    # profile:read) are exactly the read rung's mapping — a
    # read credential can finally travel an operation, where
    # before B-207 it could only read the meta tools. The
    # profile edit takes "draft" rather than "admin" by a
    # deliberate least-scope judgement: editing a principal's
    # own face is the lightest write in the product, and a
    # seat's default key is born carrying read+draft, so the
    # seat the profile belongs to can maintain it without an
    # operator widening its key. The group-settings edit takes
    # "admin" because groups:write maps from no lesser rung:
    # renaming a group or changing its visibility is an
    # organiser-grade act at the human doors too, and the
    # checker would refuse a lesser annotation here by name.
    "groups.get": (
        "gpa_get_group",
        "read",
        "Read one group's plain facts — name, slug, description, "
        "visibility, home timezone, default place — through the "
        "internal groups.get operation. Facts only: no member "
        "content, no settings internals, and a group id that does "
        "not exist is refused not_found, exactly as at the API door.",
    ),
    "groups.list_members": (
        "gpa_list_members",
        "read",
        "List one group's full roster through the internal "
        "groups.list_members operation: its person memberships "
        "(role and invite status) and its agent seats (role and "
        "profile) in one answer, each kind labelled as itself — "
        "humans and seats are equal principals here (B-202), and "
        "the roster is how an agent learns who it is planning with.",
    ),
    "groups.update": (
        "gpa_update_group",
        "admin",
        "Edit one group's plain settings — name, description, "
        "visibility, home timezone, default place — through the "
        "internal groups.update operation. Only those five fields "
        "travel this road: roles, scopes, and membership are "
        "administration acts at their own doors, never settings, "
        "and the edit is audited under the acting principal's label.",
    ),
    "notify.request": (
        "gpa_request_notification",
        "admin",
        "Write notification rows for one audience — an event's "
        "recipients or one named label — through the internal "
        "notify.request operation. Rows only, under the outbox law: "
        "this never sends; delivery is the notification jobs' work, "
        "and preferences and quiet hours are applied at creation.",
    ),
    "notify.rsvp_promotion": (
        "gpa_notify_rsvp_promotion",
        "admin",
        "Notify a waitlisted member that they have been promoted to a "
        "place, through the internal notify.rsvp_promotion operation — "
        "the same notification the RSVP service writes when a seat "
        "frees, with the same wording and the same audit trail.",
    ),
    "jobs.enqueue": (
        "gpa_enqueue_job",
        "admin",
        "Commit one queued background job through the internal "
        "jobs.enqueue operation. Submission commits, as the direct "
        "form does; the job then runs under the job chassis' claim, "
        "backoff, and dead-letter law, and its progress is read back "
        "through the same job model the human API serves.",
    ),
    "ai.submit_job": (
        "gpa_submit_ai_job",
        "admin",
        "Submit one AI job — extraction, vision, or transcription — "
        "through the internal ai.submit_job operation. Any other job "
        "kind is refused at this door: a caller may not use the AI "
        "door to submit arbitrary background work.",
    ),
    "integrations.emit": (
        "gpa_emit_integration_event",
        "admin",
        "Announce one act to a group's subscribed outbound webhook "
        "endpoints through the internal integrations.emit operation, "
        "in the caller's transaction — either an event's own wire "
        "data, built from its row, or an explicit data payload.",
    ),
    "profile.get": (
        "gpa_get_profile",
        "read",
        "Read one principal's profile through the internal "
        "profile.get operation: a person's (name, timezone, locale) "
        "by user id, or a seat's (name, description, avatar URL) by "
        "seat id inside its group. Profile only — never credentials, "
        "never a seat's role or scopes, which are administration.",
    ),
    "profile.update": (
        "gpa_update_profile",
        "draft",
        "Edit one principal's profile through the internal "
        "profile.update operation: a person's name, timezone, or "
        "locale, or a seat's name, description, or avatar URL. "
        "The edit is audited under the acting principal's own label "
        "— a seat's face is the seat's to maintain (B-202) — and "
        "fields outside the kind's profile set are refused by name.",
    ),
}

# The declared meta tools: server facts, no group content, no
# operation travelled. Their scopes are "read" — and even that
# is a real gate (B-205): a credential with no scopes at all
# learns nothing here, not even the operation list's shape.
META_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="gpa_server_info",
        operation=None,
        description=(
            "Report this server's identity and negotiated capabilities: "
            "server name and backend version, the MCP service identity's "
            "key id, the transports this build serves, and the protocol "
            "versions the initialize handshake can negotiate. No group "
            "content exists on this tool."
        ),
        credential_scope="read",
        kind="meta",
    ),
    ToolSpec(
        name="gpa_list_internal_operations",
        operation=None,
        description=(
            "List the internal operations this surface may call — name, "
            "owning module, required scope, one-line summary — together "
            "with the generated tool registry that covers them. An "
            "operation missing here is a capability MCP does not have, "
            "by the bridge's law."
        ),
        credential_scope="read",
        kind="meta",
    ),
)


# --- Generation ---------------------------------------------------------------


def operation_specs(
    annotations: dict[str, tuple[str, str, str]] | None = None,
) -> list[ToolSpec]:
    """Generate one tool spec per registered internal operation.

    Registration is lazy in the internal API (the client's first
    call registers the handlers), so this registers them itself —
    a cold process generates from the real registry, never from
    an empty one. An operation with no annotation raises, naming
    it: generation is total or it does not happen, because a
    silently partial catalogue is exactly what B-206 forbids.
    """
    from gpa.internal import handlers, registry

    handlers.register_all()
    table = ANNOTATIONS if annotations is None else annotations
    specs: list[ToolSpec] = []
    for operation in sorted(registry.OPERATIONS.values(), key=lambda op: op.name):
        annotation = table.get(operation.name)
        if annotation is None:
            raise ValueError(
                f"internal operation '{operation.name}' has no MCP tool "
                "annotation — add its curated line to gpa.mcp.tools.ANNOTATIONS"
            )
        tool_name, scope, description = annotation
        specs.append(
            ToolSpec(
                name=tool_name,
                operation=operation.name,
                description=description,
                credential_scope=scope,
                kind="operation",
            )
        )
    return specs


def all_tool_specs(
    annotations: dict[str, tuple[str, str, str]] | None = None,
) -> list[ToolSpec]:
    """Every tool the server serves: meta tools + operation tools.

    Meta tools first (an agent meets the server before it works),
    then the operation tools in operation-name order — a stable
    order, so manifests and catalogues diff cleanly between
    releases.
    """
    return [*META_TOOLS, *operation_specs(annotations)]


def tool_scopes(specs: list[ToolSpec] | None = None) -> dict[str, str]:
    """Tool name -> required credential scope, from the specs.

    This is the registry's reading of the scope law; the auth
    module's ``TOOL_SCOPES`` is the enforcement copy the call-time
    guard reads, and the checker asserts the two agree exactly.
    """
    return {spec.name: spec.credential_scope for spec in (specs or all_tool_specs())}


# --- The checker ----------------------------------------------------------------


def check_tool_registry(
    specs: list[ToolSpec] | None = None,
    *,
    server_tool_names: set[str] | None = None,
    scopes_table: dict[str, str] | None = None,
) -> list[str]:
    """Every registry failure, each naming its tool or operation.

    An empty list is a sound registry. The legs, in order:

    * every registered internal operation has exactly one tool,
      and every operation tool names a registered operation
      (the two directions of B-206's parity sentence);
    * tool names are unique, meta tools name no operation, and
      operation tools name one;
    * each operation tool's credential scope maps (through
      B-205's table) onto its operation's required internal
      scope;
    * the enforcement scope table (``gpa.mcp.auth.TOOL_SCOPES``,
      or a caller-supplied table) agrees with the specs exactly;
    * when ``server_tool_names`` is given (a built server's live
      tool list), the served tools are exactly the specs — a
      tool the server serves but the registry does not declare
      is the reverse-direction failure, caught at the surface
      where an agent would actually meet it.
    """
    from gpa.internal import handlers, registry

    handlers.register_all()
    specs = all_tool_specs() if specs is None else specs
    failures: list[str] = []

    # Names must be unique before any set comparison means
    # anything: a duplicated name would hide a missing tool
    # behind a same-named one in every set leg below.
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            failures.append(f"{spec.name}: duplicate MCP tool name in the registry")
        seen.add(spec.name)

    operations = registry.OPERATIONS
    covered: dict[str, str] = {}
    for spec in specs:
        if spec.kind == "meta":
            if spec.operation is not None:
                failures.append(
                    f"{spec.name}: meta tool names operation '{spec.operation}'"
                )
            continue
        if spec.operation is None:
            failures.append(f"{spec.name}: operation tool names no operation")
            continue
        if spec.operation not in operations:
            failures.append(
                f"{spec.name}: names unregistered internal operation "
                f"'{spec.operation}'"
            )
            continue
        if spec.operation in covered:
            failures.append(
                f"{spec.name}: operation '{spec.operation}' already has "
                f"tool '{covered[spec.operation]}'"
            )
        covered[spec.operation] = spec.name
    for operation_name in sorted(operations):
        if operation_name not in covered:
            failures.append(
                f"{operation_name}: internal operation has no MCP tool"
            )

    # The scope-mapping leg: the credential a tool demands must
    # actually be able to carry the operation it travels. The
    # mapping table is B-205's; it is imported inside the check
    # so this module's import never cycles with the auth module
    # (which the server imports alongside this one).
    from gpa.mcp.auth import EXTERNAL_TO_INTERNAL

    for spec in specs:
        if spec.kind != "operation" or spec.operation not in operations:
            continue
        required_internal = operations[spec.operation].required_scope
        mapped = EXTERNAL_TO_INTERNAL.get(spec.credential_scope, frozenset())
        if required_internal not in mapped:
            failures.append(
                f"{spec.name}: credential scope '{spec.credential_scope}' "
                f"does not map onto internal scope '{required_internal}'"
            )

    # The enforcement copy must agree with the registry exactly:
    # a scope stated in one place and enforced from another is
    # two sources of truth, which is what this slice abolishes.
    if scopes_table is None:
        from gpa.mcp.auth import TOOL_SCOPES

        scopes_table = TOOL_SCOPES
    expected_scopes = tool_scopes(specs)
    if dict(scopes_table) != expected_scopes:
        for name in sorted(set(expected_scopes) | set(scopes_table)):
            if scopes_table.get(name) != expected_scopes.get(name):
                failures.append(
                    f"{name}: scope table says "
                    f"'{scopes_table.get(name)}', registry says "
                    f"'{expected_scopes.get(name)}'"
                )

    if server_tool_names is not None and server_tool_names != seen:
        for name in sorted(seen - server_tool_names):
            failures.append(f"{name}: registered tool is not served by the server")
        for name in sorted(server_tool_names - seen):
            failures.append(f"{name}: server serves a tool the registry does not declare")

    return failures


# --- The release manifest -------------------------------------------------------


def description_sha256(description: str) -> str:
    """The SHA-256 of one tool description, exactly as served."""
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def tool_manifest(specs: list[ToolSpec] | None = None) -> list[dict]:
    """The tool catalogue as release-manifest data.

    One entry per tool: name, operation (null for meta tools),
    credential scope, the full description, and its SHA-256.
    The release builder stamps this list into the signed
    release manifest; :func:`manifest_digest` gives the one
    hash a verifier recomputes to prove no description moved.
    """
    # Annotations ride the manifest too (B-218): a release's
    # per-tool hints are signed facts like its descriptions,
    # imported lazily here because the security module reads
    # this module's specs — the same lazy seam the checker
    # uses for the auth table.
    from gpa.mcp.security import tool_annotations

    return [
        {
            "name": spec.name,
            "operation": spec.operation,
            "credential_scope": spec.credential_scope,
            "kind": spec.kind,
            "description": spec.description,
            "description_sha256": description_sha256(spec.description),
            "annotations": tool_annotations(spec),
        }
        for spec in (specs or all_tool_specs())
    ]


def manifest_digest(entries: list[dict]) -> str:
    """One SHA-256 over a manifest's tool entries, canonically.

    Canonical form: the entries as compact JSON with sorted
    keys, in manifest order. Any change — a swapped description,
    a renamed tool, a re-scoped tool — changes this digest,
    which is the whole point of stamping it into a signed
    manifest beside the entries it covers.
    """
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
