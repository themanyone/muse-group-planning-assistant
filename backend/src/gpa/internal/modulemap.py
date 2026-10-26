"""The module map — which files form which module (roadmap B-161).

§6A.3 names the module list: identity, groups, sources,
extraction, events, calendar, planning, notifications,
integrations, memory (pruning), crypto, internal, jobs, storage,
audit. The repository layout (§3) builds each module as a family
of files — ``models/<domain>.py``, ``schemas/<domain>.py``,
``services/<domain>.py``, ``routers/<domain>.py`` — rather than as
a directory of its own, and sixteen phases shipped on that
layout. This map is the machine-readable statement of which
family each file belongs to, so the architecture checker
(``gpa.internal.architecture``) can enforce the boundary law
over the layout that actually exists:

* a module owns its tables (its models file) and its service
  logic (its services files);
* cross-module work is requested through the internal API
  (``gpa.internal``) for every registered operation, and the
  remaining direct service-to-service edges are pinned by name
  in the architecture test — the set may only shrink;
* shared code lives in the named common modules — errors,
  crypto, internal, audit — never in a dumping ground.

Two judgement calls, recorded plainly:

* ``models/core.py`` holds the rows several modules share by
  design of the data layer (Job, AuditLog, FileAsset); it is
  mapped to the synthetic module ``core`` and the models layer
  as a whole is shared — services work on shared models inside
  one transaction, which is the shipped §3 design. The boundary
  law bites on SERVICE imports, which is where behaviour (not
  data shape) crosses a border.
* Composition roots — ``main.py``, ``worker.py``,
  ``arq_worker.py``, ``deps.py`` — exist to wire modules
  together; they are mapped to ``composition`` and are exempt
  from the service-edge inventory (their imports are the
  wiring itself, reviewed in the open).
"""

from __future__ import annotations

# services/<file>.py -> owning module. Every service file must
# appear here; the architecture test fails on an unmapped file,
# so a new service file is a deliberate module decision, never
# an accident.
SERVICE_MODULES: dict[str, str] = {
    # identity — accounts, sessions, keys, seats (Phase 1).
    "identity": "identity",
    "account_security": "identity",  # B-174: MFA, policy, lockout, sessions
    "mail": "identity",  # dev file-outbox carrying identity's mail
    "agent_pack": "identity",  # agent seats are identity principals
    # audit — the append-only B-019 stream; a named common module.
    "audit": "audit",
    "audit_chain": "audit",  # the B-170 chain: audit's own half
    # groups — groups, memberships, sub-groups (Phases 1, 9).
    "groups": "groups",
    "subgroups": "groups",
    # sources — every ingest road in (Phase 2 + live sources).
    "sources": "sources",
    "paste": "sources",
    "files": "sources",
    "uploads": "sources",  # B-171: the resumable road into files.ingest_file
    "newsletter": "sources",
    "images": "sources",
    "links": "sources",
    "linkfetch": "sources",
    "emailin": "sources",
    "voice": "sources",
    "live": "sources",
    "stream": "sources",
    # extraction — the AI pipeline's service half (Phases 3, 5, 6):
    # candidates, extraction runs, vision/flyers, transcription,
    # de-duplication matching/merging, AI usage accounting.
    "candidates": "extraction",
    "extraction": "extraction",
    "usage": "extraction",
    "vision": "extraction",
    "flyers": "extraction",
    "transcribe": "extraction",
    "matching": "extraction",
    "merging": "extraction",
    # events — the event lifecycle and its conversation (Phase 4).
    "events": "events",
    "rsvps": "events",
    "discussion": "events",
    "announce": "events",
    "invites": "events",
    # calendar — rendering events in time (Phase 4).
    "ical": "calendar",
    "calendar_views": "calendar",
    "recurrence": "calendar",
    # planning — tasks, polls, checklists, budgets (Phase 7).
    "planning": "planning",
    "polls": "planning",
    "checklists": "planning",
    "budget": "planning",
    "availability": "planning",
    "templates": "planning",
    # nonprofit — the member-organisation pack (Phase 9).
    "announcements": "nonprofit",
    "shifts": "nonprofit",
    "resources": "nonprofit",
    "attendance": "nonprofit",
    "member_io": "nonprofit",
    "settings_pack": "nonprofit",
    # notifications — the outbox and every send road (Phase 8).
    "notifications": "notifications",
    "mailer": "notifications",
    "messenger_transport": "notifications",
    "reminders": "notifications",
    "digests": "notifications",
    # integrations — every outside system (Phases 11, 12, 16).
    "integrations": "integrations",
    "outbound": "integrations",
    "facebook": "integrations",
    "facebook_events": "integrations",
    "google_calendar": "integrations",
    "google_inbound": "integrations",
    "samtal": "integrations",
    "samtal_calendar": "integrations",
    "livechat": "integrations",
    "messenger": "integrations",
    "discord": "integrations",
    "slack": "integrations",
    "webhooks_in": "integrations",
    "embed": "integrations",
    "health_board": "integrations",
    "reconcile": "integrations",
    # memory — context pruning and group memory (Phase 10).
    "memory": "memory",
    # truth — the truth board and tombstone ledger (Phases 6, 10).
    "quality": "truth",
    "tombstones": "truth",
    # jobs — the background-work chassis (Phases 0, 16).
    "jobs": "jobs",
    "jobqueue": "jobs",
    # crypto — sealing for secrets at rest (Phase 11). The field
    # service of B-167 joins this module as gpa.crypto lands.
    "sealing": "crypto",
    "keymgmt": "crypto",  # B-169: rotation + recovery, the key lifecycle
    # durability — the B-171 platform services that are not an
    # ingest road: draft autosaves rest beside the health board.
    "autosaves": "platform",
    # privacy — export, erasure, retention (Phase 13).
    "erasure": "privacy",
    "retention": "privacy",
    "me_data": "privacy",
    # demo — the seed story; composition by nature (Phase 14).
    "seed": "demo",
    "demo": "demo",
    # platform — whole-app health (Phase 15).
    "health": "platform",
    "recovery": "platform",  # B-172: startup assessment & repair
    "resilience": "platform",  # B-173: breakers + bulkheads per dependency
    "repair": "platform",  # B-173: the fix-forward data-repair crew
    "chaos": "platform",  # B-175: the crash-correction harness core
    "parity": "platform",  # B-201: the API-parity register + checker
}

# Modules whose public functions are their whole surface, open
# to every other module (§6A.3's named common modules). Edges
# INTO these are not cross-module behaviour calls: audit is the
# same-transaction writer every change flows through (B-019),
# and crypto is a box of primitives (sealing today; the B-167
# field service joins it). The architecture checker's service
# inventory exempts them as targets.
SHARED_TARGET_MODULES: frozenset[str] = frozenset({"audit", "crypto"})

# Files whose job is wiring modules together. They may import
# any module's public surface; they are reviewed as composition,
# and the service-edge inventory does not apply to them.
COMPOSITION_FILES: frozenset[str] = frozenset(
    {
        "src/gpa/main.py",
        "src/gpa/worker.py",
        "src/gpa/arq_worker.py",
        "src/gpa/deps.py",
    }
)


def service_module(filename: str) -> str | None:
    """The module owning ``services/<filename>.py``, or None if
    the file is not in the map (a new file nobody has placed)."""
    stem = filename.removesuffix(".py")
    return SERVICE_MODULES.get(stem)


def layer_of(relpath: str) -> str:
    """The layer a source file lives in, from its path under
    ``src/gpa``: one of models, schemas, services, routers, ai,
    storage, internal, or root (the package's own top-level
    files: config, db, errors, security, deps, main, worker)."""
    parts = relpath.split("/")
    # parts: ['src', 'gpa', ...]
    if len(parts) > 2 and parts[2] in (
        "models",
        "schemas",
        "services",
        "routers",
        "ai",
        "storage",
        "internal",
        "mcp",
    ):
        return parts[2]
    return "root"
