"""The group settings pack (roadmap B-111).

One validated schema for the dials a group runs on, read and
written as a single pack instead of scattered fields:

- the review-queue rules — the lowest role that may approve a
  candidate (``review_min_role``) and how many DISTINCT
  approvers a candidate needs (``review_approvals_required``);
- reminder defaults — the group's reminder rules (B-096's
  closed vocabulary, shared with the group PATCH road);
- the digest's day and moment (B-099), and whether it runs;
- the template set the group's shelf draws from — a closed
  vocabulary; a new set registers here, never by free text;
- the daily AI budget cap (B-047), in micro-units; null means
  no cap;
- the memory dials (Phase 10): the pruning pass cap and
  per-run token budget (B-114, hard-capped here AND in the
  engine), and which engine judges the group's history —
  hosted or the group's own local endpoint (B-119);
- the Phase 13 dials: the per-group upload quota in bytes
  (B-141; null = no quota) and the source-retention window
  in days (B-144; null = keep sources forever).

Every change is audited with its old and new values in one
``group.settings_updated`` row, and a digest change re-anchors
the digest job chain in the same transaction — the audit, the
settings, and the schedule never disagree.

In the pack, a field that is PRESENT governs — including null
where null means something (budget → no cap; digest weekday →
the default). A field that is absent is left exactly as it
was. (The older group PATCH road drops nulls instead; that is
its documented convention, and the pack is the deliberate one.)
"""

import json

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import ROLE_ORDER, Group
from gpa.services import audit
from gpa.services import reminders as reminder_service

# The template-set vocabulary (B-111). "starter" is the shelf
# every group begins with (templates.py); new sets are added
# here, in code, never by a caller's free text.
TEMPLATE_SETS: tuple[str, ...] = ("starter",)

# The pruning-driver vocabulary (Phase 10, B-119): "hosted" is
# the process's configured MetaModelClient; "local" is the
# group's own OpenAI-compatible endpoint. The engine enforces
# the pass ceiling itself (ai/pruning.MAX_PASSES_CEILING); the
# pack's bounds below mirror it so a write past the ceiling is
# refused at the door, not merely ignored in the engine.
PRUNE_DRIVERS: tuple[str, ...] = ("hosted", "local")
PRUNE_MAX_PASSES_CAP = 3
PRUNE_TOKEN_BUDGET_MAX = 10_000_000

# Roles that may be named as the review floor. "guest" is on
# the ladder but is not a membership a group grants its
# reviewers — the floor starts at member.
REVIEW_ROLES: tuple[str, ...] = tuple(
    role for role in ROLE_ORDER if role != "guest"
)


def settings_view(group: Group) -> dict:
    """The pack as read: stored values, with the reminder
    rules resolved the way the reminder service resolves them
    (stored JSON, or the roadmap defaults when none)."""
    return {
        "review_min_role": group.review_min_role,
        "review_approvals_required": group.review_approvals_required,
        "reminder_rules": reminder_service.rules_for(group),
        "digest_weekday": group.digest_weekday,
        "digest_time": group.digest_time,
        "digest_enabled": group.digest_enabled,
        "template_set": group.template_set,
        "ai_daily_budget_micros": group.ai_daily_budget_micros,
        "prune_max_passes": group.prune_max_passes,
        "prune_token_budget": group.prune_token_budget,
        "prune_driver": group.prune_driver,
        # Phase 13 dials (B-141/B-144): the storage quota and
        # the source-retention window; null = no quota / keep
        # sources forever.
        "upload_quota_bytes": group.upload_quota_bytes,
        "source_retention_days": group.source_retention_days,
    }


def _invalid(detail: str) -> AppError:
    return AppError("validation_error", detail=detail)


def update_settings(
    db: Session, *, actor_label: str, group: Group, changes: dict
) -> dict:
    """Validate and apply the provided sections of the pack.

    ``changes`` carries ONLY the fields the caller sent (the
    schema door drops the rest), each validated whole before
    anything is written — a pack with one bad dial changes
    nothing at all.
    """
    detail: dict = {}

    if "review_min_role" in changes:
        value = changes["review_min_role"]
        if value not in REVIEW_ROLES:
            raise _invalid(
                "review_min_role must be member, organiser, admin, or owner."
            )
        detail["review_min_role"] = {"from": group.review_min_role, "to": value}
        group.review_min_role = value

    if "review_approvals_required" in changes:
        value = changes["review_approvals_required"]
        if not isinstance(value, int) or not 1 <= value <= 3:
            raise _invalid("review_approvals_required must be 1, 2, or 3.")
        detail["review_approvals_required"] = {
            "from": group.review_approvals_required, "to": value,
        }
        group.review_approvals_required = value

    if "reminder_rules" in changes:
        rules = changes["reminder_rules"]
        normalised = []
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("kind") not in (
                "days_before", "morning_of",
            ):
                raise _invalid(
                    "reminder rules are days_before (with days) or morning_of."
                )
            if rule["kind"] == "days_before":
                days = rule.get("days")
                if not isinstance(days, int) or not 1 <= days <= 365:
                    raise _invalid("A days_before rule needs days between 1 and 365.")
                normalised.append({"kind": "days_before", "days": days})
            else:
                normalised.append({"kind": "morning_of"})
        detail["reminder_rules"] = {
            "from": reminder_service.rules_for(group), "to": normalised,
        }
        group.reminder_rules_json = json.dumps(normalised)

    if "digest_weekday" in changes:
        value = changes["digest_weekday"]
        if value is not None and (
            not isinstance(value, int) or not 0 <= value <= 6
        ):
            raise _invalid("digest_weekday is 0 (Monday) to 6 (Sunday), or null.")
        detail["digest_weekday"] = {"from": group.digest_weekday, "to": value}
        group.digest_weekday = value

    if "digest_time" in changes:
        value = changes["digest_time"] or ""
        if value:
            # The same HH:MM reader the group PATCH road uses —
            # one definition of a valid moment (B-099).
            from gpa.services.notifications import _parse_hhmm

            _parse_hhmm(value, "digest_time")
        detail["digest_time"] = {"from": group.digest_time, "to": value}
        group.digest_time = value

    if "digest_enabled" in changes:
        value = changes["digest_enabled"]
        if not isinstance(value, bool):
            raise _invalid("digest_enabled is true or false.")
        detail["digest_enabled"] = {"from": group.digest_enabled, "to": value}
        group.digest_enabled = value

    if "template_set" in changes:
        value = changes["template_set"]
        if value not in TEMPLATE_SETS:
            raise _invalid(
                f"template_set must be one of: {', '.join(TEMPLATE_SETS)}."
            )
        detail["template_set"] = {"from": group.template_set, "to": value}
        group.template_set = value

    if "ai_daily_budget_micros" in changes:
        value = changes["ai_daily_budget_micros"]
        if value is not None and (not isinstance(value, int) or value < 0):
            raise _invalid("ai_daily_budget_micros is a non-negative amount, or null for no cap.")
        detail["ai_daily_budget_micros"] = {
            "from": group.ai_daily_budget_micros, "to": value,
        }
        group.ai_daily_budget_micros = value

    if "prune_max_passes" in changes:
        value = changes["prune_max_passes"]
        if not isinstance(value, int) or not 1 <= value <= PRUNE_MAX_PASSES_CAP:
            raise _invalid(
                f"prune_max_passes must be 1 to {PRUNE_MAX_PASSES_CAP} — "
                "the engine's hard cap, not a suggestion."
            )
        detail["prune_max_passes"] = {
            "from": group.prune_max_passes, "to": value,
        }
        group.prune_max_passes = value

    if "prune_token_budget" in changes:
        value = changes["prune_token_budget"]
        if (
            not isinstance(value, int)
            or not 0 <= value <= PRUNE_TOKEN_BUDGET_MAX
        ):
            raise _invalid(
                "prune_token_budget is a whole number of tokens "
                f"(0 to {PRUNE_TOKEN_BUDGET_MAX}); 0 means the engine may not spend."
            )
        detail["prune_token_budget"] = {
            "from": group.prune_token_budget, "to": value,
        }
        group.prune_token_budget = value

    if "prune_driver" in changes:
        value = changes["prune_driver"]
        if value not in PRUNE_DRIVERS:
            raise _invalid(
                f"prune_driver must be one of: {', '.join(PRUNE_DRIVERS)}."
            )
        detail["prune_driver"] = {"from": group.prune_driver, "to": value}
        group.prune_driver = value

    if "upload_quota_bytes" in changes:
        value = changes["upload_quota_bytes"]
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise _invalid(
                "upload_quota_bytes is a whole number of bytes, "
                "or null for no quota."
            )
        detail["upload_quota_bytes"] = {
            "from": group.upload_quota_bytes, "to": value,
        }
        group.upload_quota_bytes = value

    if "source_retention_days" in changes:
        value = changes["source_retention_days"]
        if value is not None and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= 3650
        ):
            raise _invalid(
                "source_retention_days is a whole number of days "
                "(1 to 3650), or null to keep sources forever."
            )
        detail["source_retention_days"] = {
            "from": group.source_retention_days, "to": value,
        }
        group.source_retention_days = value

    if not detail:
        # An empty pack is a valid read of intent but changes
        # nothing — and writes no audit row claiming it did.
        return settings_view(group)

    audit.record(
        db, actor=actor_label, action="group.settings_updated",
        group_id=group.id, entity_type="group", entity_id=group.id,
        detail=detail,
    )
    # A digest change re-anchors the digest chain in this same
    # transaction (the B-099 law the group PATCH road follows).
    if {"digest_weekday", "digest_time", "digest_enabled"} & set(changes):
        from gpa.services import digests as digest_service

        digest_service.ensure_scheduled(db, group=group)
    # A retention change re-anchors the sweep chain the same
    # way (B-144).
    if "source_retention_days" in changes:
        from gpa.services import retention as retention_service

        retention_service.ensure_scheduled(db, group=group)
    db.commit()
    return settings_view(group)
