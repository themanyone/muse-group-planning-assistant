"""Truth-keeping read models (Phase 6): tombstones (B-080) and
the data-quality report (B-081).

A tombstone is shown with its window: ``restorable`` says
whether the 30 days are still open, and ``purge_after`` is the
day they close — the frontend never has to guess either.

The quality report is four lists and one count. The count —
``needs_attention`` — is what the frontend's badge renders;
the lists are what a person works through.
"""

from datetime import datetime

from pydantic import BaseModel

from gpa.models.identity import now_utc


class TombstoneResponse(BaseModel):
    id: str
    entity_type: str  # "event" | "source"
    entity_id: str
    title: str
    deleted_by: str
    deleted_at: datetime
    purge_after: datetime
    restorable: bool


def tombstone_view(stone) -> TombstoneResponse:
    return TombstoneResponse(
        id=stone.id,
        entity_type=stone.entity_type,
        entity_id=stone.entity_id,
        title=stone.title,
        deleted_by=stone.deleted_by,
        deleted_at=stone.deleted_at,
        purge_after=stone.purge_after,
        restorable=now_utc() <= stone.purge_after,
    )


class QualityEventRef(BaseModel):
    event_id: str
    title: str


class QualityFieldRef(BaseModel):
    candidate_id: str
    title: str
    field: str


class QualityContradictionRef(BaseModel):
    event_id: str
    event_title: str
    contradiction_id: str
    field: str


class QualityReportResponse(BaseModel):
    group_id: str
    missing_place: list[QualityEventRef]
    missing_host: list[QualityEventRef]
    low_confidence_fields: list[QualityFieldRef]
    unresolved_contradictions: list[QualityContradictionRef]
    # The badge number: the total across all four lists.
    needs_attention: int
