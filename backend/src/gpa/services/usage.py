"""The AI usage ledger + per-group budget (roadmap B-047).

Every model call writes an ``AiUsage`` row — model, tier, tokens,
integer-exact cost in micros — inside the caller's transaction
(the live driver accounts per call, B-037; the extraction service
records through here). On top of the ledger sits the group's
daily budget: a soft-stop, stated plainly. When a group has spent
its day, extraction does not fail and nothing is dropped — the
run is deferred to the next UTC day and the review queue's status
reads "paused — budget" (B-047's acceptance words). The stop is
checked before each kind's extraction step: work never STARTS at
or over the cap, though one step may carry spending past it
(budgets pace the day; they do not gag work already paid for).
For the single-window sources that are the norm, a step is
exactly one model call.
"""

from datetime import datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from gpa.ai.client import ModelResult
from gpa.models.ai import AiUsage
from gpa.models.groups import Group


def record_usage(
    db: Session,
    *,
    result: ModelResult,
    group_id: str | None = None,
    source_id: str | None = None,
    operation: str = "",
    prompt_name: str = "",
    prompt_version: int | None = None,
) -> AiUsage:
    """Ledger one model call. NO commit — the caller's transaction
    owns it, so a usage row can never outlive the work it paid
    for (nor the work outlive its bill)."""
    row = AiUsage(
        group_id=group_id,
        source_id=source_id,
        operation=operation,
        model_id=result.model_id,
        tier=result.tier,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_micros=result.cost_micros,
    )
    db.add(row)
    return row


def _day_start(now: datetime) -> datetime:
    """The current UTC day's start, as a naive UTC instant (the
    storage convention, D-21). Budgets are per UTC day — one
    rule, every timezone, no arguments."""
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return datetime.combine(now.date(), time.min)


def spent_today_micros(db: Session, group_id: str, *, now: datetime | None = None) -> int:
    """What this group has spent on AI since UTC midnight."""
    start = _day_start(now or datetime.now(timezone.utc))
    rows = (
        db.query(AiUsage)
        .filter(AiUsage.group_id == group_id, AiUsage.created_at >= start)
        .all()
    )
    return sum(row.cost_micros for row in rows)


def budget_state(db: Session, group: Group, *, now: datetime | None = None) -> dict:
    """The queue-facing truth: the cap, today's spend, and whether
    extraction is currently paused by the budget."""
    spent = spent_today_micros(db, group.id, now=now)
    budget = group.ai_daily_budget_micros
    return {
        "budget_micros": budget,
        "spent_today_micros": spent,
        "paused": budget is not None and spent >= budget,
        "pause_reason": "budget" if budget is not None and spent >= budget else None,
    }


def next_budget_reset(now: datetime | None = None) -> datetime:
    """When a budget-deferred run should fire: the next UTC
    midnight — the moment the new day's budget opens."""
    return _day_start(now or datetime.now(timezone.utc)) + timedelta(days=1)
