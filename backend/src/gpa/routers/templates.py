"""Template routes (roadmap B-091).

Reading templates is member+ (the starters are every group's
to use); saving and instantiating are organiser+ behind the
publish scope — instantiation builds a published event, which
is a publishing act by the codebase's standing rule.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.events import to_utc_naive
from gpa.schemas.planning import (
    InstantiationResponse,
    TemplateCreateRequest,
    TemplateFromEventRequest,
    TemplateInstantiateRequest,
    TemplateResponse,
    template_view,
)
from gpa.services import templates as template_service

router = APIRouter(prefix="/api/v1/groups", tags=["templates"])


@router.get("/{group_id}/templates", response_model=list[TemplateResponse])
def list_templates(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The global starters plus this group's own templates."""
    rows = template_service.list_templates(db, context.group)
    return [
        template_view(row, template_service.parse_payload(row)) for row in rows
    ]


@router.get("/{group_id}/templates/{template_id}", response_model=TemplateResponse)
def get_template(
    template_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    template = template_service.get_template(db, context.group, template_id)
    return template_view(template, template_service.parse_payload(template))


@router.post(
    "/{group_id}/templates", response_model=TemplateResponse, status_code=201
)
def create_template(
    body: TemplateCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Save a plan shape as one of the group's own templates."""
    template = template_service.create_template(
        db,
        actor_label=principal.label,
        group=context.group,
        name=body.name,
        description=body.description,
        payload=body.payload,
    )
    return template_view(template, template_service.parse_payload(template))


@router.post(
    "/{group_id}/templates/from-event/{event_id}",
    response_model=TemplateResponse,
    status_code=201,
)
def template_from_event(
    event_id: str,
    body: TemplateFromEventRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Snapshot an event and its plan as a reusable template —
    due moments become day-offsets; claims become open slots."""
    template = template_service.template_from_event(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        event_id=event_id,
        name=body.name,
    )
    return template_view(template, template_service.parse_payload(template))


@router.post(
    "/{group_id}/templates/{template_id}/instantiate",
    response_model=InstantiationResponse,
    status_code=201,
)
def instantiate_template(
    template_id: str,
    body: TemplateInstantiateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Build the real plan at a new start moment: a published
    event plus its tasks, checklist, and open role slots."""
    built = template_service.instantiate_template(
        db,
        actor_label=principal.label,
        group=context.group,
        template_id=template_id,
        starts_at=to_utc_naive(body.starts_at),
        title=body.title,
    )
    return InstantiationResponse(
        template_id=built["template"].id,
        event_id=built["event"].id,
        task_ids=[task.id for task in built["tasks"]],
        checklist_item_ids=[item.id for item in built["checklist"]],
        role_slot_ids=[slot.id for slot in built["roles"]],
    )
