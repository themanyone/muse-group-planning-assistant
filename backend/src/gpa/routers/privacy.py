"""Privacy routes (Phase 13): group export + erasure
(B-143) and the per-group permission listing (B-144).

Export and erasure are the OWNER's acts, and a person's —
never a seat's, never an API key's: the caller must be the
group's owner, signed in with a live session (a key, however
widely scoped, is a delegated credential, and delegating
ERASURE was never on the table).
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from gpa.deps import (
    GroupContext,
    Principal,
    get_db,
    get_principal,
    require_group_role,
    require_scope,
)
from gpa.errors import AppError
from gpa.schemas.privacy import (
    EraseRequest,
    EraseResponse,
    RetentionRunResponse,
    SourcePermissionEntry,
)
from gpa.services import erasure as erasure_service
from gpa.services import retention as retention_service
from gpa.services import sources as source_service

router = APIRouter(prefix="/api/v1/groups", tags=["privacy"])


def require_owner_person(
    context: GroupContext = Depends(require_group_role("owner")),
    principal: Principal = Depends(get_principal),
) -> GroupContext:
    """Owner role (the group door) PLUS a live person session:
    seats hold no owner role and are refused by the door;
    this guard adds the API-key refusal."""
    if principal.kind != "user" or principal.key_id is not None:
        raise AppError(
            "forbidden",
            detail=(
                "This needs the group's owner, signed in — "
                "an API key cannot export or erase a group."
            ),
        )
    return context


@router.post("/{group_id}/export")
def export_group(
    group_id: str,
    request: Request,
    context: GroupContext = Depends(require_owner_person),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Download the group's whole archive (B-143): JSON by
    domain, the calendar as iCal, the stored files as
    themselves — one ZIP a human can open."""
    data, filename = erasure_service.build_export(
        db,
        settings=request.app.state.settings,
        storage=request.app.state.storage,
        group=context.group,
        actor_label=principal.label,
    )
    db.commit()  # the export's audit row lands before answering
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{group_id}/erase", response_model=EraseResponse)
def erase_group_route(
    group_id: str,
    body: EraseRequest,
    request: Request,
    context: GroupContext = Depends(require_owner_person),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Hard-erase the group (B-143). Double confirmation: the
    group's exact name typed back, and the word ERASE. A
    mismatch refuses with the group untouched."""
    if body.confirm_name != context.group.name or body.confirm_word != "ERASE":
        raise AppError(
            "validation_error",
            detail=(
                "To erase this group, type its exact name and "
                "the word ERASE. Nothing was erased."
            ),
        )
    return erasure_service.erase_group(
        db,
        database=request.app.state.db,
        storage=request.app.state.storage,
        group=context.group,
        actor_label=principal.label,
    )


@router.get(
    "/{group_id}/sources/permissions",
    response_model=list[SourcePermissionEntry],
)
def source_permissions(
    group_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Who shared what, on what permission (B-144) — every
    source's permission record in plain words, readable by
    any member: a group should be able to audit what was
    brought into it and on whose say-so."""
    from gpa.models.groups import AgentSeat
    from gpa.models.identity import User

    def who(label: str) -> str:
        if label.startswith("user:"):
            user = db.get(User, label[5:])
            return user.name if user is not None else "A member"
        if label.startswith("seat:"):
            seat = db.get(AgentSeat, label[5:])
            return f"{seat.name} (agent seat)" if seat is not None else "An agent seat"
        return label

    entries = []
    for source in source_service.list_sources(db, context.group):
        entries.append(
            SourcePermissionEntry(
                source_id=source.id,
                title=source.title,
                kind=source.kind,
                added_by=who(source.granted_by),
                permission=(
                    source.granted_scope
                    or "No permission note was recorded for this source."
                ),
                added_on=source.created_at.isoformat(),
            )
        )
    return entries


@router.post(
    "/{group_id}/retention/run", response_model=RetentionRunResponse
)
def run_retention(
    group_id: str,
    request: Request,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Run the group's retention sweep NOW (B-144) — the same
    sweep the nightly job runs; the receipt is counts only.
    With no window set, the sweep honestly expires nothing."""
    return retention_service.run_sweep(
        db, storage=request.app.state.storage, group=context.group
    )
