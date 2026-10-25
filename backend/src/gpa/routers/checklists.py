"""Checklist, role, supplies, and budget routes (roadmap
B-088, B-089).

Everything here hangs under an event except the merged
supplies view and lives at member+ / draft scope: claiming a
line or a role is a member's own promise, not a publishing
act. The budget door holds B-089's law in its schema — amounts
are integer cents, and a float body never reaches a service.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.events import to_utc_naive
from gpa.schemas.planning import (
    BudgetResponse,
    BalanceResponse,
    ChecklistItemCreateRequest,
    ChecklistItemPatchRequest,
    ChecklistItemResponse,
    CostLineCreateRequest,
    CostLineResponse,
    RoleSlotResponse,
    RoleSlotsCreateRequest,
    SupplyLineResponse,
    checklist_item_view,
    cost_line_view,
    role_slot_view,
)
from gpa.services import budget as budget_service
from gpa.services import checklists as checklist_service
from gpa.services import planning as planning_service

router = APIRouter(prefix="/api/v1/groups", tags=["checklists"])


# ------------------------------------------------------------------
# Checklist items (B-088)
# ------------------------------------------------------------------


@router.post(
    "/{group_id}/events/{event_id}/checklist",
    response_model=ChecklistItemResponse,
    status_code=201,
)
def add_checklist_item(
    event_id: str,
    body: ChecklistItemCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Add a bring/prepare line to an event's list."""
    item = checklist_service.add_item(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        event_id=event_id,
        label=body.label,
        quantity=body.quantity,
        section=body.section,
        note=body.note,
    )
    return checklist_item_view(item)


@router.get(
    "/{group_id}/events/{event_id}/checklist",
    response_model=list[ChecklistItemResponse],
)
def list_checklist_items(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    event = planning_service.visible_event(db, context.group, event_id, context.role)
    rows = checklist_service.list_items(db, context.group, event)
    return [checklist_item_view(row) for row in rows]


@router.patch(
    "/{group_id}/checklist/{item_id}", response_model=ChecklistItemResponse
)
def update_checklist_item(
    item_id: str,
    body: ChecklistItemPatchRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    item = checklist_service.update_item(
        db, actor_label=principal.label, group=context.group,
        item_id=item_id, changes=body.model_dump(exclude_unset=True),
    )
    return checklist_item_view(item)


@router.post(
    "/{group_id}/checklist/{item_id}/claim",
    response_model=ChecklistItemResponse,
)
def claim_checklist_item(
    item_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Promise this line yourself."""
    item = checklist_service.claim_item(
        db, actor_label=principal.label, user_id=principal.user_id,
        group=context.group, item_id=item_id, claim=True,
    )
    return checklist_item_view(item)


@router.post(
    "/{group_id}/checklist/{item_id}/unclaim",
    response_model=ChecklistItemResponse,
)
def unclaim_checklist_item(
    item_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Release a line you claimed."""
    item = checklist_service.claim_item(
        db, actor_label=principal.label, user_id=principal.user_id,
        group=context.group, item_id=item_id, claim=False,
    )
    return checklist_item_view(item)


# ------------------------------------------------------------------
# Role slots (B-088)
# ------------------------------------------------------------------


@router.post(
    "/{group_id}/events/{event_id}/roles",
    response_model=list[RoleSlotResponse],
    status_code=201,
)
def open_role_slots(
    event_id: str,
    body: RoleSlotsCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Open one or more slots of a named job on an event."""
    rows = checklist_service.add_role_slots(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        event_id=event_id,
        role_name=body.role_name,
        slots=body.slots,
    )
    return [role_slot_view(row) for row in rows]


@router.get(
    "/{group_id}/events/{event_id}/roles",
    response_model=list[RoleSlotResponse],
)
def list_role_slots(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    event = planning_service.visible_event(db, context.group, event_id, context.role)
    rows = checklist_service.list_slots(db, context.group, event)
    return [role_slot_view(row) for row in rows]


@router.post(
    "/{group_id}/roles/{slot_id}/claim", response_model=RoleSlotResponse
)
def claim_role_slot(
    slot_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Take one of the event's named jobs yourself."""
    slot = checklist_service.claim_slot(
        db, actor_label=principal.label, user_id=principal.user_id,
        group=context.group, slot_id=slot_id, claim=True,
    )
    return role_slot_view(slot)


@router.post(
    "/{group_id}/roles/{slot_id}/unclaim", response_model=RoleSlotResponse
)
def unclaim_role_slot(
    slot_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    slot = checklist_service.claim_slot(
        db, actor_label=principal.label, user_id=principal.user_id,
        group=context.group, slot_id=slot_id, claim=False,
    )
    return role_slot_view(slot)


# ------------------------------------------------------------------
# Merged supplies (B-088)
# ------------------------------------------------------------------


@router.get("/{group_id}/supplies", response_model=list[SupplyLineResponse])
def merged_supplies(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The "buy once" view: checklist lines folded across the
    window's events. Without a window the group gets this
    week (Monday → Monday, group-local) — the shopping trip's
    natural span."""
    if start is None or end is None:
        week_start, week_end = planning_service.week_window(context.group)
        start = start or week_start
        end = end or week_end
    rows = checklist_service.merged_supplies(
        db, context.group,
        start=to_utc_naive(start), end=to_utc_naive(end),
    )
    return rows


# ------------------------------------------------------------------
# Budget-lite (B-089)
# ------------------------------------------------------------------


@router.post(
    "/{group_id}/events/{event_id}/costs",
    response_model=CostLineResponse,
    status_code=201,
)
def add_cost_line(
    event_id: str,
    body: CostLineCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Record one line of what the event cost (integer cents;
    the payer defaults to you)."""
    line = budget_service.add_cost_line(
        db,
        actor_label=principal.label,
        actor_user_id=principal.user_id,
        group=context.group,
        viewer_role=context.role,
        event_id=event_id,
        label=body.label,
        amount_cents=body.amount_cents,
        paid_by_id=body.paid_by_id,
        paid_by_label=body.paid_by_label,
    )
    return cost_line_view(line)


@router.get(
    "/{group_id}/events/{event_id}/costs",
    response_model=list[CostLineResponse],
)
def list_cost_lines(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    planning_service.visible_event(db, context.group, event_id, context.role)
    rows = budget_service.list_cost_lines(db, context.group, event_id)
    return [cost_line_view(row) for row in rows]


@router.get("/{group_id}/events/{event_id}/budget", response_model=BudgetResponse)
def get_budget(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The balance summary: total + per-member paid totals,
    every number exact integer cents (B-089)."""
    planning_service.visible_event(db, context.group, event_id, context.role)
    budget = budget_service.event_budget(db, context.group, event_id)
    return BudgetResponse(
        event_id=event_id,
        total_cents=budget["total_cents"],
        lines=[cost_line_view(line) for line in budget["lines"]],
        balances=[BalanceResponse(**row) for row in budget["balances"]],
    )
