"""Architecture tests — the module boundary law (roadmap B-161).

These tests guard §6A.3's promise: every domain is a module with
one public surface, cross-module work goes through the internal
API (B-162) or is pinned here by name, and no file ever reaches
into another file's private names. The checker lives in
``gpa.internal.architecture``; this file carries the two pinned
inventories and the deliberately violating fixture that proves
the rules bite (B-161's acceptance, in the roadmap's words).

THE PINNING LAW: both sets below may only SHRINK, and only in
the same commit that removes the edge (a rewired call through
the internal API, a helper promoted into a shared module). If
one of these tests fails because you ADDED an import, the fix
is almost never to extend the pin — it is to route the call
properly. Extending a pin requires a recorded reason in
PROJECT_MEMORY_MAP.md.
"""

from pathlib import Path

from gpa.internal.architecture import check_tree

REPO_ROOT = Path(__file__).resolve().parents[1]

# Cross-module service edges, pinned at the Phase 17 opening
# (B-161): a services file importing a different module's
# service file, a services file importing the ai package, an ai
# file importing a service. Audit and crypto targets are the
# shared common modules and never appear here (§6A.3).
PINNED_SERVICE_EDGES = frozenset(
    {
        ("src/gpa/ai/evaluation.py", "gpa.services.quality"),
        ("src/gpa/services/announcements.py", "gpa.services.subgroups"),
        ("src/gpa/services/attendance.py", "gpa.services.events"),
        ("src/gpa/services/attendance.py", "gpa.services.planning"),
        ("src/gpa/services/calendar_views.py", "gpa.services.resources"),
        ("src/gpa/services/calendar_views.py", "gpa.services.subgroups"),
        ("src/gpa/services/candidates.py", "gpa.services.quality"),
        ("src/gpa/services/candidates.py", "gpa.services.sources"),
        ("src/gpa/services/demo.py", "gpa.services.announcements"),
        ("src/gpa/services/demo.py", "gpa.services.events"),
        ("src/gpa/services/demo.py", "gpa.services.paste"),
        ("src/gpa/services/demo.py", "gpa.services.sources"),
        ("src/gpa/services/digests.py", "gpa.services.jobs"),
        ("src/gpa/services/digests.py", "gpa.services.planning"),
        ("src/gpa/services/discussion.py", "gpa.services.planning"),
        ("src/gpa/services/discussion.py", "gpa.services.polls"),
        ("src/gpa/services/erasure.py", "gpa.services.ical"),
        ("src/gpa/services/erasure.py", "gpa.services.integrations"),
        ("src/gpa/services/erasure.py", "gpa.services.jobs"),
        ("src/gpa/services/erasure.py", "gpa.services.recurrence"),
        ("src/gpa/services/erasure.py", "gpa.services.settings_pack"),
        ("src/gpa/services/events.py", "gpa.ai.client"),
        ("src/gpa/services/events.py", "gpa.services.files"),
        ("src/gpa/services/events.py", "gpa.services.images"),
        ("src/gpa/services/events.py", "gpa.services.reminders"),
        ("src/gpa/services/events.py", "gpa.services.sources"),
        ("src/gpa/services/events.py", "gpa.services.subgroups"),
        ("src/gpa/services/events.py", "gpa.services.usage"),
        ("src/gpa/services/events.py", "gpa.services.vision"),
        ("src/gpa/services/extraction.py", "gpa.ai.client"),
        ("src/gpa/services/extraction.py", "gpa.ai.extraction"),
        ("src/gpa/services/extraction.py", "gpa.services.jobs"),
        ("src/gpa/services/extraction.py", "gpa.services.quality"),
        ("src/gpa/services/extraction.py", "gpa.services.sources"),
        ("src/gpa/services/facebook.py", "gpa.services.ical"),
        ("src/gpa/services/flyers.py", "gpa.services.calendar_views"),
        # B-180: the wall opens flyer transcripts through the
        # sources module's sealing funnel (the seal lives with
        # the data's owner, as with B-167's quote/chunk pins).
        ("src/gpa/services/flyers.py", "gpa.services.sources"),
        ("src/gpa/services/google_inbound.py", "gpa.services.sources"),
        ("src/gpa/services/groups.py", "gpa.services.digests"),
        ("src/gpa/services/groups.py", "gpa.services.events"),
        ("src/gpa/services/groups.py", "gpa.services.identity"),
        ("src/gpa/services/groups.py", "gpa.services.merging"),
        ("src/gpa/services/groups.py", "gpa.services.notifications"),
        ("src/gpa/services/groups.py", "gpa.services.planning"),
        ("src/gpa/services/groups.py", "gpa.services.reminders"),
        ("src/gpa/services/jobqueue.py", "gpa.ai.client"),
        # B-169: keymgmt registers its rotation handler on the
        # jobs module's public registry — the same surface
        # every handler-bearing service uses (digests above).
        ("src/gpa/services/keymgmt.py", "gpa.services.jobs"),
        # B-173: the arq composition imports the repair crew so
        # its data_repair handler registers, like the others.
        ("src/gpa/services/jobqueue.py", "gpa.services.repair"),
        ("src/gpa/services/jobqueue.py", "gpa.services.digests"),
        ("src/gpa/services/jobqueue.py", "gpa.services.extraction"),
        ("src/gpa/services/jobqueue.py", "gpa.services.mailer"),
        ("src/gpa/services/jobqueue.py", "gpa.services.messenger_transport"),
        ("src/gpa/services/jobqueue.py", "gpa.services.notifications"),
        ("src/gpa/services/jobqueue.py", "gpa.services.outbound"),
        ("src/gpa/services/jobqueue.py", "gpa.services.reconcile"),
        ("src/gpa/services/jobqueue.py", "gpa.services.retention"),
        ("src/gpa/services/jobqueue.py", "gpa.services.transcribe"),
        ("src/gpa/services/jobqueue.py", "gpa.services.vision"),
        # B-173: the job chassis asks the resilience registry
        # for a permit before running dependency-bound work —
        # the breaker/bulkhead gate is the chassis' own law.
        ("src/gpa/services/jobs.py", "gpa.services.resilience"),
        ("src/gpa/services/livechat.py", "gpa.services.groups"),
        ("src/gpa/services/livechat.py", "gpa.services.sources"),
        ("src/gpa/services/mailer.py", "gpa.services.mail"),
        ("src/gpa/services/member_io.py", "gpa.services.identity"),
        ("src/gpa/services/member_io.py", "gpa.services.subgroups"),
        ("src/gpa/services/memory.py", "gpa.ai.client"),
        ("src/gpa/services/memory.py", "gpa.ai.drivers_local"),
        ("src/gpa/services/memory.py", "gpa.ai.extraction"),
        ("src/gpa/services/memory.py", "gpa.ai.promptlib"),
        ("src/gpa/services/memory.py", "gpa.ai.pruning"),
        ("src/gpa/services/memory.py", "gpa.services.attendance"),
        ("src/gpa/services/memory.py", "gpa.services.discussion"),
        ("src/gpa/services/memory.py", "gpa.services.quality"),
        ("src/gpa/services/memory.py", "gpa.services.sources"),
        ("src/gpa/services/memory.py", "gpa.services.usage"),
        ("src/gpa/services/merging.py", "gpa.services.events"),
        ("src/gpa/services/merging.py", "gpa.services.sources"),
        ("src/gpa/services/notifications.py", "gpa.services.jobs"),
        ("src/gpa/services/outbound.py", "gpa.services.jobs"),
        ("src/gpa/services/planning.py", "gpa.services.events"),
        ("src/gpa/services/planning.py", "gpa.services.groups"),
        ("src/gpa/services/planning.py", "gpa.services.subgroups"),
        ("src/gpa/services/polls.py", "gpa.services.events"),
        ("src/gpa/services/reconcile.py", "gpa.services.jobs"),
        # B-173: the repair crew registers its handler on the
        # jobs registry, re-renders stale digests through the
        # one rendering door, and judges alt text by the vision
        # module's genuineness rule — recompute, never reimplement.
        ("src/gpa/services/repair.py", "gpa.services.digests"),
        ("src/gpa/services/repair.py", "gpa.services.jobs"),
        ("src/gpa/services/repair.py", "gpa.services.vision"),
        ("src/gpa/services/reminders.py", "gpa.ai.client"),
        ("src/gpa/services/reminders.py", "gpa.ai.promptlib"),
        ("src/gpa/services/resources.py", "gpa.services.events"),
        ("src/gpa/services/resources.py", "gpa.services.planning"),
        ("src/gpa/services/retention.py", "gpa.services.jobs"),
        ("src/gpa/services/rsvps.py", "gpa.services.reminders"),
        ("src/gpa/services/samtal_calendar.py", "gpa.services.sources"),
        ("src/gpa/services/seed.py", "gpa.services.groups"),
        ("src/gpa/services/seed.py", "gpa.services.templates"),
        ("src/gpa/services/settings_pack.py", "gpa.services.digests"),
        ("src/gpa/services/settings_pack.py", "gpa.services.notifications"),
        ("src/gpa/services/settings_pack.py", "gpa.services.reminders"),
        ("src/gpa/services/settings_pack.py", "gpa.services.retention"),
        ("src/gpa/services/shifts.py", "gpa.services.events"),
        ("src/gpa/services/sources.py", "gpa.services.tombstones"),
        ("src/gpa/services/stream.py", "gpa.services.jobs"),
        ("src/gpa/services/templates.py", "gpa.services.events"),
        ("src/gpa/services/tombstones.py", "gpa.services.events"),
        ("src/gpa/services/tombstones.py", "gpa.services.sources"),
        ("src/gpa/services/transcribe.py", "gpa.ai.client"),
        ("src/gpa/services/transcribe.py", "gpa.services.jobs"),
        ("src/gpa/services/transcribe.py", "gpa.services.sources"),
        ("src/gpa/services/usage.py", "gpa.ai.client"),
        ("src/gpa/services/vision.py", "gpa.ai.client"),
        ("src/gpa/services/vision.py", "gpa.ai.segmentation"),
        ("src/gpa/services/vision.py", "gpa.ai.vision"),
        ("src/gpa/services/vision.py", "gpa.services.images"),
        ("src/gpa/services/vision.py", "gpa.services.jobs"),
        ("src/gpa/services/vision.py", "gpa.services.quality"),
        ("src/gpa/services/vision.py", "gpa.services.sources"),
        ("src/gpa/services/voice.py", "gpa.services.transcribe"),
        ("src/gpa/services/webhooks_in.py", "gpa.services.sources"),
    }
)

# Router -> model edges, pinned at the same opening: the small
# projection fetches a handful of routers legitimately carry.
PINNED_ROUTER_MODEL_EDGES = frozenset(
    {
        ("src/gpa/routers/calendar.py", "gpa.models.groups"),
        ("src/gpa/routers/events.py", "gpa.models.events"),
        ("src/gpa/routers/events.py", "gpa.models.groups"),
        ("src/gpa/routers/integrations.py", "gpa.models.identity"),
        ("src/gpa/routers/invites.py", "gpa.models.notifications"),
        ("src/gpa/routers/jobs.py", "gpa.models.groups"),
        ("src/gpa/routers/jobs.py", "gpa.models.integrations"),
        ("src/gpa/routers/jobs.py", "gpa.models.notifications"),
        ("src/gpa/routers/jobs.py", "gpa.models.sources"),
        ("src/gpa/routers/memory.py", "gpa.models.memory"),
        ("src/gpa/routers/nonprofit.py", "gpa.models.groups"),
        ("src/gpa/routers/notifications.py", "gpa.models.notifications"),
        ("src/gpa/routers/privacy.py", "gpa.models.groups"),
        ("src/gpa/routers/privacy.py", "gpa.models.identity"),
    }
)


def _report(result) -> str:
    """A readable failure: every violation, one per line."""
    return "\n".join(
        f"{v.rule} {v.path}:{v.lineno} {v.message}" for v in result.violations
    )


def test_real_tree_obeys_the_boundary_law():
    """The whole src/gpa tree passes every architecture rule:
    layering, no private imports, every service file mapped,
    and both edge inventories exactly as pinned."""
    result = check_tree(REPO_ROOT, PINNED_SERVICE_EDGES, PINNED_ROUTER_MODEL_EDGES)
    assert result.violations == [], _report(result)


def test_checker_bites_on_a_violating_fixture(tmp_path):
    """B-161's acceptance, in the roadmap's words: the rules
    FAIL on a cross-module private import — proven here by a
    deliberately violating fixture tree, alongside one breach
    of each other rule family."""
    gpa = tmp_path / "src" / "gpa"
    for layer in ("models", "schemas", "services", "routers"):
        (gpa / layer).mkdir(parents=True)
        (gpa / layer / "__init__.py").write_text("", encoding="utf-8")
    (gpa / "__init__.py").write_text("", encoding="utf-8")
    # A cross-module PRIVATE import (services/events.py reaching
    # into services/vision.py's underscore names).
    (gpa / "services" / "events.py").write_text(
        "from gpa.services.vision import _is_genuine_alt\n", encoding="utf-8"
    )
    (gpa / "services" / "vision.py").write_text(
        "def _is_genuine_alt(text):\n    return bool(text)\n", encoding="utf-8"
    )
    # A schema importing a service (upward lean).
    (gpa / "schemas" / "events.py").write_text(
        "from gpa.services.events import publish_event\n", encoding="utf-8"
    )
    # A model importing a service (upward lean).
    (gpa / "models" / "events.py").write_text(
        "from gpa.services.events import publish_event\n", encoding="utf-8"
    )
    # An unpinned cross-module service edge.
    (gpa / "services" / "groups.py").write_text(
        "from gpa.services import notifications\n", encoding="utf-8"
    )
    # A service file nobody placed in the module map.
    (gpa / "services" / "wibble.py").write_text("", encoding="utf-8")

    result = check_tree(tmp_path, frozenset(), frozenset())
    seen = {(v.rule, v.path) for v in result.violations}
    assert ("private", "src/gpa/services/events.py") in seen
    assert ("layer", "src/gpa/schemas/events.py") in seen
    assert ("layer", "src/gpa/models/events.py") in seen
    assert ("inventory", "src/gpa/services/groups.py") in seen
    assert ("unmapped", "src/gpa/services/wibble.py") in seen


def test_stale_pin_is_a_violation(tmp_path):
    """The pin is exact in both directions: a pinned edge that
    no longer exists fails too, so the inventory cannot quietly
    rot into a list of permissions nobody uses."""
    gpa = tmp_path / "src" / "gpa"
    (gpa / "services").mkdir(parents=True)
    (gpa / "__init__.py").write_text("", encoding="utf-8")
    (gpa / "services" / "__init__.py").write_text("", encoding="utf-8")
    (gpa / "services" / "events.py").write_text("", encoding="utf-8")
    stale = frozenset({("src/gpa/services/events.py", "gpa.services.notifications")})
    result = check_tree(tmp_path, stale, frozenset())
    assert any(v.rule == "inventory" for v in result.violations)
