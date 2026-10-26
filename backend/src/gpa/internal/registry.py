"""The operation registry — what may be asked, of whom (B-162).

§6A.4: internal operations are named, typed, and registered —
operation → owning module → required internal scope. This file
is that registry, plus the caller grants the local profile
authorises against: which module may exercise which scope.
The grants are least-privilege, written from the call sites
that exist (a module holds exactly the scopes its rewired
calls exercise). B-164 replaces this static table with real
service identities (key ids, rotation); the client's question
— "may this caller exercise this scope?" — does not change.

Handlers register from ``gpa.internal.handlers`` (the package's
composition root for operations); the registry itself imports
no service, so importing it is always safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# A handler receives the caller's database session and the
# envelope payload, and returns plain result data. In the
# in-process profile the session is the CALLER's session: a
# handler's writes land in the caller's transaction, exactly
# as the direct calls they replace did (the outbox pattern
# depends on it). Commit discipline belongs to the caller,
# except for operations whose direct form committed (jobs
# submission), which keep committing — the handler docstrings
# say which is which.
Handler = Callable[..., dict]


@dataclass(frozen=True)
class Operation:
    """One registered operation and its law."""

    name: str
    owner: str  # the module that owns the behaviour
    required_scope: str  # the internal scope a caller must hold
    summary: str  # one plain sentence, for docs and audits


# operation name -> Operation (with its handler attached when
# the handlers module has registered it).
OPERATIONS: dict[str, Operation] = {}
HANDLERS: dict[str, Handler] = {}


def register(operation: Operation, handler: Handler) -> None:
    """Register (or replace) one operation and its handler."""
    OPERATIONS[operation.name] = operation
    HANDLERS[operation.name] = handler


def get_operation(name: str) -> Operation | None:
    """The registered operation of this name, or None."""
    return OPERATIONS.get(name)


# Caller grants for the local profile: module name -> scopes it
# may exercise. Written from the rewired call sites of B-162;
# a module that gains a new internal call gains exactly that
# call's scope here, in the same commit, where a reviewer can
# see the grant widen.
CALLER_GRANTS: dict[str, frozenset[str]] = {
    # events asks notifications to write (cancellations) and
    # integrations to announce its acts outward.
    "events": frozenset({"notify:write", "integrations:write"}),
    # extraction submits AI jobs and announces machine drafts.
    "extraction": frozenset({"ai:submit", "integrations:write"}),
    # sources submits transcription jobs (voice ingest).
    "sources": frozenset({"ai:submit"}),
    # groups drafts candidate stubs and announces them.
    "groups": frozenset({"candidates:write", "integrations:write"}),
    # integrations drafts change candidates from outside edits
    # and asks notifications to report drift.
    "integrations": frozenset({"candidates:write", "notify:write", "jobs:write"}),
    # notifications submits send/digest jobs (the committing
    # enqueue sites; its same-transaction queue() calls are the
    # jobs module's in-process surface and stay direct).
    "notifications": frozenset({"jobs:write"}),
    # privacy submits the retention sweep's follow-up jobs.
    "privacy": frozenset({"jobs:write"}),
    # memory drafts candidates from group summaries.
    "memory": frozenset({"candidates:write"}),
    # The application process itself: the door a future split
    # frontend process would use to ask the core to publish.
    # In-process today, routers call the events service
    # directly; this grant exists so the operation is real and
    # exercised, not decorative.
    "core": frozenset({"events:publish"}),
    # The MCP server process (B-204): the gateway identity.
    # Its grants span the registered operations because a
    # gateway's job is to carry an authorised principal's call
    # to whichever operation the tool names — the per-principal
    # authorisation (which seat, holding which scopes) is
    # B-205's check at call time, layered over this door, and
    # the approval law itself still lives in the operations'
    # owning services, unchanged for MCP callers.
    "mcp": frozenset(
        {
            "candidates:write",
            "events:publish",
            "notify:write",
            "jobs:write",
            "ai:submit",
            "integrations:write",
            # B-207: the groups/members/profile family's scopes —
            # the gateway carries the family's reads and edits
            # for an authorised principal, under the same
            # per-principal check as every scope above.
            "groups:read",
            "groups:write",
            "profile:read",
            "profile:write",
            # B-208: the review queue's scopes — its reads and
            # its decision doors (approve / reject / edit).
            "candidates:read",
            "candidates:decide",
            # B-209: events and the calendar — reads, the
            # cancellation write, and the RSVP write
            # (participation-grade at the credential mapping).
            "events:read",
            "events:write",
            "rsvps:write",
            # B-210: sources and extraction requests — reads,
            # the filing writes, and the request scope
            # (publish-grade at the credential mapping).
            "sources:read",
            "sources:write",
            "extraction:request",
            # B-211: planning — the minted things' reads and
            # their upkeep writes (draft-grade at the mapping).
            "planning:read",
            "planning:write",
            # B-212: announcements, inboxes/preferences, and
            # digests — reads, plus the two publish-grade
            # writes (announcing, composing the digest).
            "announcements:read",
            "announcements:write",
            "notifications:read",
            "notifications:write",
            # B-217: the sampling authorisation scope — the
            # gateway carries the policy/record call for an
            # authorised (publish-grade) principal, under the
            # same per-principal check as every scope above.
            "sampling:request",
        }
    ),
}


def caller_may(caller: str, scope: str) -> bool:
    """May this caller exercise this scope (local profile)?

    Extra grants can be added at runtime by composition roots
    and tests via :func:`grant`; B-164's identity-backed
    authoriser answers the same question from key material.
    """
    return scope in grants_for(caller)


def grants_for(caller: str) -> frozenset[str]:
    """Every scope a caller holds (static table + runtime
    grants) — the read side B-164's identities report."""
    return CALLER_GRANTS.get(caller, frozenset()) | _EXTRA_GRANTS.get(
        caller, frozenset()
    )


def known_callers() -> list[str]:
    """Every caller name granted anything, static or runtime."""
    return sorted(set(CALLER_GRANTS) | set(_EXTRA_GRANTS))


_EXTRA_GRANTS: dict[str, set[str]] = {}


def grant(caller: str, *scopes: str) -> None:
    """Add scopes to a caller for this process (composition and
    tests; the static table above is the shipped posture)."""
    _EXTRA_GRANTS.setdefault(caller, set()).update(scopes)
