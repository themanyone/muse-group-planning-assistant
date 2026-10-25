"""Budget-lite (roadmap B-089).

What one event cost, line by line, and who paid what — the
group's truth about money, and deliberately nothing more: no
ledger, no transfers, no payments. Amounts are integer cents
end to end (the request schema takes an int, the column is an
Integer, the summary adds ints), so the arithmetic a test — or
a treasurer — checks is exact, never float-rounded.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.planning import CostLine
from gpa.services import audit
from gpa.services import planning as planning_service


def add_cost_line(
    db: Session,
    *,
    actor_label: str,
    actor_user_id: str | None,
    group: Group,
    viewer_role: str | None,
    event_id: str,
    label: str,
    amount_cents: int,
    paid_by_id: str | None = None,
    paid_by_label: str = "",
) -> CostLine:
    """Record one line of an event's cost (B-089).

    ``amount_cents`` must arrive as an int — the schema refuses
    a float before this function ever runs, because money as a
    float is how quiet pennies vanish. The payer defaults to
    the caller (a person recording their own spend); naming
    another member as payer is allowed, and naming words alone
    (a guest paid cash) keeps the id empty and the words."""
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise AppError(
            "validation_error",
            detail="An amount is a whole number of cents — never a fraction.",
        )
    if not label or not label.strip():
        raise AppError("validation_error", detail="A cost line needs its label.")
    event = planning_service.visible_event(db, group, event_id, viewer_role)
    payer_id = paid_by_id
    payer_label = paid_by_label
    if payer_id is not None:
        found = planning_service.member_label(db, group, payer_id)
        if found is None:
            raise AppError(
                "validation_error",
                detail="The payer must be a member of this group.",
            )
        _user, payer_label = found
    elif payer_id is None and not payer_label and actor_user_id is not None:
        found = planning_service.member_label(db, group, actor_user_id)
        payer_id = actor_user_id
        payer_label = found[1] if found else actor_label
    line = CostLine(
        group_id=group.id, event_id=event.id, label=label.strip()[:240],
        amount_cents=amount_cents, paid_by_id=payer_id,
        paid_by_label=payer_label, created_by=actor_label,
    )
    db.add(line)
    db.flush()
    audit.record(
        db, actor=actor_label, action="cost.added",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"line_id": line.id, "amount_cents": amount_cents},
    )
    db.commit()
    return line


def list_cost_lines(db: Session, group: Group, event_id: str) -> list[CostLine]:
    """An event's cost lines, in the order they were recorded."""
    return (
        db.query(CostLine)
        .filter(CostLine.group_id == group.id, CostLine.event_id == event_id)
        .order_by(CostLine.created_at, CostLine.id)
        .all()
    )


def event_budget(db: Session, group: Group, event_id: str) -> dict:
    """The balance summary (B-089): the event's total, its
    lines, and per-member paid totals — every number an integer
    number of cents, summed in SQL where the database does the
    one true addition. "Balance" here is honestly scoped: what
    each person is in for, against the event's total; settling
    up is the group's business, off-platform (no payments)."""
    lines = list_cost_lines(db, group, event_id)
    totals = (
        db.query(
            CostLine.paid_by_id,
            CostLine.paid_by_label,
            func.coalesce(func.sum(CostLine.amount_cents), 0),
        )
        .filter(CostLine.group_id == group.id, CostLine.event_id == event_id)
        .group_by(CostLine.paid_by_id, CostLine.paid_by_label)
        .all()
    )
    balances = [
        {
            "member_id": paid_by_id,
            "member_label": paid_by_label,
            "paid_cents": int(paid_total),
        }
        for paid_by_id, paid_by_label, paid_total in totals
    ]
    balances.sort(key=lambda row: (-row["paid_cents"], row["member_label"]))
    return {
        "event_id": event_id,
        "total_cents": sum(int(row["paid_cents"]) for row in balances),
        "lines": lines,
        "balances": balances,
    }
