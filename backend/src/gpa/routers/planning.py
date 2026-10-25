"""Planning routes (roadmap B-083, B-085, B-086, B-092, B-093).

Tasks, decisions, and open questions, plus the cross-cutting
reads: the event "what is still missing?" gaps view, search
across planning, and the state-of-the-plan digest data. Reads
are member+; writes are member+ riding the draft scope for key
callers (planning words are draft-class acts; publishing truth
— approvals, poll winners — stays behind the publish doors
elsewhere). All permission checks go through deps (B-017);
every rule lives in services/planning.py.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.events import to_utc_naive
from gpa.schemas.planning import (
    DecisionCreateRequest,
    DecisionResponse,
    GapsResponse,
    PlanningDigestResponse,
    DigestEventResponse,
    QuestionCreateRequest,
    QuestionPatchRequest,
    QuestionResolveRequest,
    QuestionResponse,
    SearchResponse,
    TaskCreateRequest,
    TaskPatchRequest,
    TaskResponse,
    decision_view,
    poll_view,
    question_view,
    role_slot_view,
    task_view,
)
from gpa.services import planning as planning_service
from gpa.services import polls as poll_service

router = APIRouter(prefix="/api/v1/groups", tags=["planning"])


# ------------------------------------------------------------------
# Tasks (B-083)
# ------------------------------------------------------------------


@router.post("/{group_id}/tasks", response_model=TaskResponse, status_code=201)
def create_task(
    body: TaskCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Create a task directly. Naming an owner here is a
    person's assignment — the only way a task is born owned."""
    task = planning_service.create_task(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        title=body.title,
        notes=body.notes,
        event_id=body.event_id,
        owner_id=body.owner_id,
        due_at=to_utc_naive(body.due_at) if body.due_at else None,
        subgroup_id=body.subgroup_id,
        actor_user_id=principal.user_id,
    )
    return task_view(task)


@router.get("/{group_id}/tasks", response_model=list[TaskResponse])
def list_tasks(
    event_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    unclaimed: bool = Query(default=False),
    owner_id: str | None = Query(default=None),
    subgroup_id: str | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The group's tasks, soonest-due first, filterable.
    Sub-group-scoped tasks outside the caller's enrolments
    never reach the list (B-105)."""
    from gpa.services import subgroups as subgroup_service

    rows = planning_service.list_tasks(
        db, context.group, event_id=event_id, status=status,
        unclaimed=unclaimed, owner_id=owner_id, subgroup_id=subgroup_id,
        subgroup_scope=subgroup_service.visible_subgroup_ids(
            db, context.group,
            user_id=context.principal.user_id, role=context.role,
        ),
        scope_restricted=True,
    )
    return [task_view(row) for row in rows]


@router.get("/{group_id}/events/{event_id}/tasks", response_model=list[TaskResponse])
def list_event_tasks(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """One event's tasks."""
    planning_service.visible_event(db, context.group, event_id, context.role)
    rows = planning_service.list_tasks(db, context.group, event_id=event_id)
    return [task_view(row) for row in rows]


def _visible_task(db: Session, context: GroupContext, task_id: str):
    """One task, with the B-105 scope applied: a scoped task
    outside the caller's sub-groups is a 404, like a draft."""
    from gpa.errors import AppError
    from gpa.services import subgroups as subgroup_service

    task = planning_service.get_task(db, context.group, task_id)
    visible_ids = subgroup_service.visible_subgroup_ids(
        db, context.group,
        user_id=context.principal.user_id, role=context.role,
    )
    if not subgroup_service.can_see_scoped(task.subgroup_id, visible_ids):
        raise AppError("not_found", detail="That task could not be found.")
    return task


@router.get("/{group_id}/tasks/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    return task_view(_visible_task(db, context, task_id))


@router.patch("/{group_id}/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    body: TaskPatchRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Edit a task — including assigning or unassigning its
    owner (a person's act, either way)."""
    _visible_task(db, context, task_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("due_at") is not None:
        changes["due_at"] = to_utc_naive(changes["due_at"])
    task = planning_service.update_task(
        db, actor_label=principal.label, group=context.group,
        task_id=task_id, changes=changes,
    )
    return task_view(task)


# ------------------------------------------------------------------
# Decisions (B-085)
# ------------------------------------------------------------------


@router.post("/{group_id}/decisions", response_model=DecisionResponse, status_code=201)
def create_decision(
    body: DecisionCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Record a decision, optionally superseding an earlier
    current one (both rows are kept; the chain is the story)."""
    decision = planning_service.create_decision(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        text=body.text,
        event_id=body.event_id,
        supersedes_id=body.supersedes_id,
    )
    return decision_view(decision)


@router.get("/{group_id}/decisions", response_model=list[DecisionResponse])
def list_decisions(
    current: bool = Query(default=False),
    event_id: str | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The decisions log, newest first. ``?current=true`` is
    the group's settled truth — superseded rows step aside."""
    rows = planning_service.list_decisions(
        db, context.group, current_only=current, event_id=event_id
    )
    return [decision_view(row) for row in rows]


@router.get("/{group_id}/decisions/{decision_id}", response_model=DecisionResponse)
def get_decision(
    decision_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    return decision_view(planning_service.get_decision(db, context.group, decision_id))


@router.get(
    "/{group_id}/decisions/{decision_id}/chain",
    response_model=list[DecisionResponse],
)
def get_decision_chain(
    decision_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The whole supersede chain containing one decision,
    oldest first — how the group's mind changed, in order."""
    rows = planning_service.decision_chain(db, context.group, decision_id)
    return [decision_view(row) for row in rows]


# ------------------------------------------------------------------
# Open questions (B-086)
# ------------------------------------------------------------------


@router.post("/{group_id}/questions", response_model=QuestionResponse, status_code=201)
def create_question(
    body: QuestionCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Ask a question of the group, or of one named member."""
    question = planning_service.create_question(
        db,
        actor_label=principal.label,
        group=context.group,
        viewer_role=context.role,
        text=body.text,
        event_id=body.event_id,
        asked_of_id=body.asked_of_id,
    )
    return question_view(question)


@router.get("/{group_id}/questions", response_model=list[QuestionResponse])
def list_questions(
    status: str | None = Query(default=None),
    event_id: str | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    rows = planning_service.list_questions(
        db, context.group, status=status, event_id=event_id
    )
    return [question_view(row) for row in rows]


@router.get("/{group_id}/questions/{question_id}", response_model=QuestionResponse)
def get_question(
    question_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    return question_view(planning_service.get_question(db, context.group, question_id))


@router.patch("/{group_id}/questions/{question_id}", response_model=QuestionResponse)
def update_question(
    question_id: str,
    body: QuestionPatchRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Edit a question: its words, who it is asked of, or drop
    it (a settled state, never a deletion)."""
    changes = body.model_dump(exclude_unset=True)
    question = planning_service.update_question(
        db, actor_label=principal.label, group=context.group,
        question_id=question_id, changes=changes,
    )
    return question_view(question)


@router.post(
    "/{group_id}/questions/{question_id}/resolve",
    response_model=QuestionResponse,
)
def resolve_question(
    question_id: str,
    body: QuestionResolveRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Resolve a question. With ``decision_text``, the answer
    is recorded as a decision in the same act and the question
    links to it — resolution and truth land together."""
    question = planning_service.resolve_question(
        db, actor_label=principal.label, group=context.group,
        question_id=question_id, decision_text=body.decision_text,
    )
    return question_view(question)


# ------------------------------------------------------------------
# Gaps (B-086), search (B-092), digest data (B-093)
# ------------------------------------------------------------------


@router.get("/{group_id}/events/{event_id}/gaps", response_model=GapsResponse)
def get_event_gaps(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """"What is still missing?" — missing fields, open
    questions, unclaimed roles, unclaimed tasks, in one list."""
    event = planning_service.visible_event(db, context.group, event_id, context.role)
    result = planning_service.event_gaps(
        db, context.group, event, viewer_role=context.role
    )
    return GapsResponse(
        event_id=event.id,
        gaps=result["gaps"],
        missing_fields=result["missing_fields"],
        open_questions=[question_view(q) for q in result["open_questions"]],
        unclaimed_roles=[role_slot_view(s) for s in result["unclaimed_roles"]],
        unclaimed_tasks=[task_view(t) for t in result["unclaimed_tasks"]],
    )


@router.get("/{group_id}/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=1),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """One search over events, tasks, decisions, questions —
    ranked, and every hit linked back to its source. Scoped
    hits outside the caller's sub-groups never appear (B-105)."""
    from gpa.services import subgroups as subgroup_service

    hits = planning_service.search_planning(
        db, context.group, q,
        subgroup_scope=subgroup_service.visible_subgroup_ids(
            db, context.group,
            user_id=context.principal.user_id, role=context.role,
        ),
        scope_restricted=True,
    )
    return SearchResponse(query=q, results=hits)


@router.get("/{group_id}/planning-digest", response_model=PlanningDigestResponse)
def planning_digest(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """"State of the plan": this week's events, unclaimed
    tasks, open questions, undecided polls — the frontend's
    group home and Phase 10's digest read this one truth."""
    digest = planning_service.planning_digest(db, context.group)
    return PlanningDigestResponse(
        week_start=digest["week_start"].isoformat() + "Z",
        week_end=digest["week_end"].isoformat() + "Z",
        events_this_week=[
            DigestEventResponse(
                id=event.id, title=event.title,
                starts_at=event.starts_at.isoformat() + "Z",
                ends_at=event.ends_at.isoformat() + "Z",
                timezone=event.timezone, place_name=event.place_name,
                status=event.status,
            )
            for event in digest["events"]
        ],
        unclaimed_tasks=[task_view(t) for t in digest["unclaimed_tasks"]],
        open_questions=[question_view(q) for q in digest["open_questions"]],
        undecided_polls=[
            poll_view(poll, poll_service.poll_results(db, poll))
            for poll in digest["undecided_polls"]
        ],
    )
