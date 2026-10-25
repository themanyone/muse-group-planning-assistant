"""Truth-keeping routes (Phase 6): the tombstone list (B-080)
and the data-quality report (B-081).

Thin doors, like every router: the permission checks are the
deps doors, the work is the services'. The tombstone list is
organiser+ (deletion is an organiser act; its record is too).
The quality report is member+ — knowing what needs attention
is every member's business.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, require_group_role
from gpa.schemas.truth import (
    QualityReportResponse,
    TombstoneResponse,
    tombstone_view,
)
from gpa.services import quality as quality_service
from gpa.services import tombstones as tombstone_service

router = APIRouter(prefix="/api/v1/groups", tags=["truth"])


@router.get("/{group_id}/tombstones", response_model=list[TombstoneResponse])
def list_tombstones(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """What's deleted-but-restorable in this group (B-080):
    each row names its window's closing day."""
    stones = tombstone_service.list_tombstones(db, context.group)
    return [tombstone_view(stone) for stone in stones]


@router.get("/{group_id}/quality-report", response_model=QualityReportResponse)
def quality_report(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The group's data-quality report (B-081): four lists and
    the needs_attention count the frontend badge renders."""
    return quality_service.group_quality_report(db, context.group)
