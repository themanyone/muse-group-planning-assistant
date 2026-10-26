"""Durability routes (roadmap B-171).

Two surfaces under the group prefix:

* **Resumable uploads** — ``POST /{group_id}/uploads`` opens
  a session, ``PUT .../parts/{index}`` accepts parts in any
  order (the part bytes are the request body),
  ``GET ...`` reports received/missing parts, and
  ``POST .../complete`` assembles, verifies, and ingests.
* **Draft autosaves** — ``PUT /{group_id}/autosaves/{kind}/{id}``
  saves an editor's draft payload with optimistic
  concurrency; ``GET`` reads the live autosave and any
  conflict copies. A stale save is answered 409 with both
  versions in the body.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.errors import AppError
from gpa.schemas.durability import (
    AutosaveRequest,
    AutosaveResponse,
    BeginUploadRequest,
    UploadStatusResponse,
)
from gpa.services import autosaves as autosave_service
from gpa.services import uploads as upload_service

router = APIRouter(prefix="/api/v1/groups", tags=["durability"])


# --- Resumable uploads -------------------------------------------------------


@router.post("/{group_id}/uploads", response_model=UploadStatusResponse, status_code=201)
def begin_upload(
    body: BeginUploadRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    row = upload_service.begin(
        db, user_id=principal.user_id or "", group=context.group,
        filename=body.filename, total_bytes=body.total_bytes,
        chunk_bytes=body.chunk_bytes, sha256=body.sha256,
    )
    return UploadStatusResponse(**upload_service.status_dict(row))


@router.get("/{group_id}/uploads/{upload_id}", response_model=UploadStatusResponse)
def upload_status(
    upload_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    row = upload_service.get_session(db, context.group, upload_id)
    return UploadStatusResponse(**upload_service.status_dict(row))


@router.put("/{group_id}/uploads/{upload_id}/parts/{index}", response_model=UploadStatusResponse)
async def upload_part(
    request: Request,
    upload_id: str,
    index: int,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    row = upload_service.get_session(db, context.group, upload_id)
    data = await request.body()
    result = upload_service.put_part(db, request.app.state.settings, row, index, data)
    return UploadStatusResponse(**result)


@router.post("/{group_id}/uploads/{upload_id}/complete", status_code=201)
def complete_upload(
    request: Request,
    upload_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    row = upload_service.get_session(db, context.group, upload_id)
    source = upload_service.complete(
        db, settings=request.app.state.settings,
        storage=request.app.state.storage, actor_label=principal.label,
        group=context.group, row=row,
    )
    return {"source_id": source.id, "upload_id": row.id, "status": "completed"}


# --- Draft autosaves ---------------------------------------------------------


@router.put("/{group_id}/autosaves/{target_kind}/{target_id}", response_model=AutosaveResponse)
def save_autosave(
    group_id: str,
    target_kind: str,
    target_id: str,
    body: AutosaveRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    if target_kind not in autosave_service.TARGET_KINDS:
        raise AppError("validation_error", detail="Autosaves cover candidates and events.")
    row, conflicted = autosave_service.save(
        db, group_id=context.group.id, user_id=principal.user_id or "",
        target_kind=target_kind, target_id=target_id,
        payload=body.payload, base_version=body.base_version,
    )
    if conflicted:
        current = autosave_service.read(
            db, group_id=context.group.id, user_id=principal.user_id or "",
            target_kind=target_kind, target_id=target_id,
        )
        raise AppError(
            "draft_conflict",
            detail="This draft was saved elsewhere since it was opened; "
            "your version is kept as a conflict copy.",
            errors=[
                {
                    "server_version": current["version"] if current else 0,
                    "server_payload": current["payload"] if current else {},
                    "your_payload": body.payload,
                }
            ],
        )
    state = autosave_service.read(
        db, group_id=context.group.id, user_id=principal.user_id or "",
        target_kind=target_kind, target_id=target_id,
    )
    return AutosaveResponse(
        payload=state["payload"], version=state["version"],
        updated_at=state["updated_at"], conflicts=state["conflicts"],
    )


@router.get("/{group_id}/autosaves/{target_kind}/{target_id}", response_model=AutosaveResponse)
def read_autosave(
    target_kind: str,
    target_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    state = autosave_service.read(
        db, group_id=context.group.id, user_id=principal.user_id or "",
        target_kind=target_kind, target_id=target_id,
    )
    if state is None:
        raise AppError("not_found", detail="No autosave rests for that draft.")
    return AutosaveResponse(**state)
