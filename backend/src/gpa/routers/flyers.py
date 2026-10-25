"""Flyer routes (roadmap Phase 5: B-065, B-070, B-072).

The wall, the human alt-text edit, and the print month — nested
under their group like every surface here. Reads are member+;
the alt-text edit is member+ with the draft scope (describing a
picture is ordinary member work, like posting a comment).
Routers never touch a model directly (B-017) — every rule lives
in services/flyers.py.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.flyers import (
    AltTextUpdateRequest,
    FlyerWallResponse,
    PrintMonthResponse,
)
from gpa.schemas.sources import FlyerResponse
from gpa.services import flyers as flyer_service

router = APIRouter(prefix="/api/v1/groups", tags=["flyers"])


def _flyer_response(flyer) -> FlyerResponse:
    """The picture record's wire shape — one builder, shared
    with routers/sources.py's detail view in spirit."""
    return FlyerResponse(
        id=flyer.id, source_id=flyer.source_id,
        width=flyer.width, height=flyer.height,
        thumbnail_key=flyer.thumbnail_key,
        alt_text=flyer.alt_text, alt_text_status=flyer.alt_text_status,
        transcript=flyer.transcript,
        parent_flyer_id=flyer.parent_flyer_id,
        region_json=flyer.region_json,
        generated=flyer.generated,
    )


@router.get("/{group_id}/flyers", response_model=FlyerWallResponse)
def flyer_wall(
    period: str | None = None,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The period's wall (B-070): every poster in the month —
    the ones approval has made events of AND the ones still
    waiting, which show as ``pending_review``, never invisible."""
    resolved_period, entries = flyer_service.flyer_wall(
        db, group=context.group, period=period
    )
    return FlyerWallResponse(period=resolved_period, entries=entries)


@router.patch("/{group_id}/flyers/{flyer_id}", response_model=FlyerResponse)
def update_alt_text(
    flyer_id: str,
    body: AltTextUpdateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Give a flyer a person's own description (B-065) — the
    human road to alt-text ``complete``. Words that are not a
    description are refused in plain words by the service."""
    flyer = flyer_service.update_alt_text(
        db, actor_label=principal.label, group=context.group,
        flyer_id=flyer_id, alt_text=body.alt_text,
    )
    return _flyer_response(flyer)


@router.get("/{group_id}/print/month", response_model=PrintMonthResponse)
def print_month(
    month: str | None = None,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """A month's listing for a real noticeboard (B-072): every
    event's facts present as text, in the structured rows and in
    the rendered plain-text document alike."""
    period, rows, text = flyer_service.print_month(
        db, group=context.group, month=month
    )
    return PrintMonthResponse(
        period=period, timezone=context.group.home_timezone or "UTC",
        events=rows, text=text,
    )
