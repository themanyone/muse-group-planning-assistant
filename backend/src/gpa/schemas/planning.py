"""Planning schemas (roadmap Phase 7, B-083 → B-093).

Wire shapes for tasks, decisions, questions, polls, checklists,
role slots, budget, availability, templates, search, and the
digest. Datetimes ride as ISO-8601 with ``Z`` (the codebase
convention via ``iso_utc``); money rides as INTEGER cents —
the field types here refuse floats before a service ever runs,
which is budget-lite's arithmetic law (B-089) enforced at the
door. Computed truths (``unclaimed``, ``overdue``, tallies,
week windows) are computed in the services and rendered here,
never trusted from the client.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from gpa.schemas.events import iso_utc
from gpa.models.planning import task_overdue

# ------------------------------------------------------------------
# Tasks (B-083)
# ------------------------------------------------------------------


class TaskCreateRequest(BaseModel):
    """Direct human entry. Naming ``owner_id`` here IS the
    assignment — a person's act, validated against membership
    in the service."""

    title: str = Field(min_length=1, max_length=240)
    notes: str = ""
    event_id: str | None = None
    # The named sub-group this task is scoped to (B-105).
    subgroup_id: str | None = None
    owner_id: str | None = None
    due_at: datetime | None = None


class TaskPatchRequest(BaseModel):
    """Any subset; ``owner_id: null`` explicitly unassigns."""

    title: str | None = Field(default=None, min_length=1, max_length=240)
    notes: str | None = None
    due_at: datetime | None = None
    subgroup_id: str | None = None
    owner_id: str | None = None
    status: str | None = None


class TaskResponse(BaseModel):
    id: str
    group_id: str
    event_id: str | None = None
    subgroup_id: str | None = None
    title: str
    notes: str = ""
    owner_id: str | None = None
    owner_label: str = ""
    suggested_owner_label: str = ""
    # The B-083 honesty fields, computed server-side: a task
    # nobody owns says so; a task past due says so.
    unclaimed: bool = True
    overdue: bool = False
    due_at: str | None = None
    status: str
    completed_at: str | None = None
    candidate_id: str | None = None
    created_by: str
    created_at: str
    updated_at: str


def task_view(task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        group_id=task.group_id,
        event_id=task.event_id,
        subgroup_id=task.subgroup_id,
        title=task.title,
        notes=task.notes,
        owner_id=task.owner_id,
        owner_label=task.owner_label,
        suggested_owner_label=task.suggested_owner_label,
        unclaimed=task.owner_id is None,
        overdue=task_overdue(task),
        due_at=iso_utc(task.due_at),
        status=task.status,
        completed_at=iso_utc(task.completed_at),
        candidate_id=task.candidate_id,
        created_by=task.created_by,
        created_at=iso_utc(task.created_at),
        updated_at=iso_utc(task.updated_at),
    )


# ------------------------------------------------------------------
# Decisions (B-085)
# ------------------------------------------------------------------


class DecisionCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    event_id: str | None = None
    supersedes_id: str | None = None


class DecisionResponse(BaseModel):
    id: str
    group_id: str
    event_id: str | None = None
    text: str
    status: str
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    candidate_id: str | None = None
    created_by: str
    created_at: str


def decision_view(decision) -> DecisionResponse:
    return DecisionResponse(
        id=decision.id,
        group_id=decision.group_id,
        event_id=decision.event_id,
        text=decision.text,
        status=decision.status,
        supersedes_id=decision.supersedes_id,
        superseded_by_id=decision.superseded_by_id,
        candidate_id=decision.candidate_id,
        created_by=decision.created_by,
        created_at=iso_utc(decision.created_at),
    )


# ------------------------------------------------------------------
# Open questions (B-086)
# ------------------------------------------------------------------


class QuestionCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    event_id: str | None = None
    asked_of_id: str | None = None


class QuestionPatchRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    asked_of_id: str | None = None
    drop: bool = False


class QuestionResolveRequest(BaseModel):
    """Resolve, optionally minting the answer as a decision in
    the same call (B-086)."""

    decision_text: str | None = Field(default=None, max_length=4000)


class QuestionResponse(BaseModel):
    id: str
    group_id: str
    event_id: str | None = None
    text: str
    asked_of_id: str | None = None
    asked_of_label: str = ""
    status: str
    resolution_decision_id: str | None = None
    candidate_id: str | None = None
    created_by: str
    created_at: str
    resolved_at: str | None = None


def question_view(question) -> QuestionResponse:
    return QuestionResponse(
        id=question.id,
        group_id=question.group_id,
        event_id=question.event_id,
        text=question.text,
        asked_of_id=question.asked_of_id,
        asked_of_label=question.asked_of_label,
        status=question.status,
        resolution_decision_id=question.resolution_decision_id,
        candidate_id=question.candidate_id,
        created_by=question.created_by,
        created_at=iso_utc(question.created_at),
        resolved_at=iso_utc(question.resolved_at),
    )


# ------------------------------------------------------------------
# Polls (B-087)
# ------------------------------------------------------------------


class PollOptionRequest(BaseModel):
    label: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class PollCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=240)
    kind: str = "choice"
    event_id: str | None = None
    closes_at: datetime | None = None
    options: list[PollOptionRequest] = Field(min_length=2, max_length=50)


class PollOptionResponse(BaseModel):
    id: str
    label: str
    starts_at: str | None = None
    ends_at: str | None = None
    votes: int = 0


class PollResponse(BaseModel):
    id: str
    group_id: str
    event_id: str | None = None
    question: str
    kind: str
    status: str
    closes_at: str | None = None
    applied_event_id: str | None = None
    options: list[PollOptionResponse] = []
    total_votes: int = 0
    my_option_id: str | None = None
    created_by: str
    created_at: str


class PollVoteRequest(BaseModel):
    option_id: str


class PollResultsResponse(BaseModel):
    """The tally as answered AFTER the vote commits (B-087):
    every option's count, the total, and the leader(s) — a tie
    returns two leaders and says so by shape."""

    poll_id: str
    status: str
    total_votes: int
    options: list[PollOptionResponse]
    leader_option_ids: list[str]
    my_option_id: str | None = None


class ApplyWinnerRequest(BaseModel):
    """Mint path only: the new event's title (defaults to the
    poll's question)."""

    title: str | None = Field(default=None, max_length=240)


def poll_option_view(option, votes: int = 0) -> PollOptionResponse:
    return PollOptionResponse(
        id=option.id,
        label=option.label,
        starts_at=iso_utc(option.starts_at),
        ends_at=iso_utc(option.ends_at),
        votes=votes,
    )


def poll_view(poll, results: dict | None = None, my_option_id: str | None = None) -> PollResponse:
    """A poll with its tally when ``results`` (services.polls'
    poll_results) is supplied; bare shape otherwise."""
    options: list[PollOptionResponse] = []
    total = 0
    if results is not None:
        total = results["total_votes"]
        options = [
            poll_option_view(entry["option"], entry["votes"])
            for entry in results["options"]
        ]
    return PollResponse(
        id=poll.id,
        group_id=poll.group_id,
        event_id=poll.event_id,
        question=poll.question,
        kind=poll.kind,
        status=poll.status,
        closes_at=iso_utc(poll.closes_at),
        applied_event_id=poll.applied_event_id,
        options=options,
        total_votes=total,
        my_option_id=my_option_id,
        created_by=poll.created_by,
        created_at=iso_utc(poll.created_at),
    )


def results_view(results: dict, my_option_id: str | None = None) -> PollResultsResponse:
    poll = results["poll"]
    return PollResultsResponse(
        poll_id=poll.id,
        status=poll.status,
        total_votes=results["total_votes"],
        options=[
            poll_option_view(entry["option"], entry["votes"])
            for entry in results["options"]
        ],
        leader_option_ids=results["leader_option_ids"],
        my_option_id=my_option_id,
    )


# ------------------------------------------------------------------
# Checklists + roles (B-088)
# ------------------------------------------------------------------


class ChecklistItemCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=240)
    quantity: int = Field(default=1, ge=1)
    section: str = "bring"
    note: str = ""


class ChecklistItemPatchRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=240)
    quantity: int | None = Field(default=None, ge=1)
    note: str | None = None
    done: bool | None = None


class ChecklistItemResponse(BaseModel):
    id: str
    group_id: str
    event_id: str
    section: str
    label: str
    quantity: int
    note: str = ""
    claimed_by_id: str | None = None
    claimed_by_label: str = ""
    claimed: bool = False
    done: bool = False
    created_at: str


def checklist_item_view(item) -> ChecklistItemResponse:
    return ChecklistItemResponse(
        id=item.id,
        group_id=item.group_id,
        event_id=item.event_id,
        section=item.section,
        label=item.label,
        quantity=item.quantity,
        note=item.note,
        claimed_by_id=item.claimed_by_id,
        claimed_by_label=item.claimed_by_label,
        claimed=item.claimed_by_id is not None or bool(item.claimed_by_label),
        done=item.done,
        created_at=iso_utc(item.created_at),
    )


class RoleSlotsCreateRequest(BaseModel):
    role_name: str = Field(min_length=1, max_length=80)
    slots: int = Field(default=1, ge=1, le=50)


class RoleSlotResponse(BaseModel):
    id: str
    group_id: str
    event_id: str
    role_name: str
    # The shift window (B-107): set on a shift's slots, null on
    # a plain timeless B-088 slot.
    starts_at: str | None = None
    ends_at: str | None = None
    claimed_by_id: str | None = None
    claimed_by_label: str = ""
    claimed: bool = False
    created_at: str


def role_slot_view(slot) -> RoleSlotResponse:
    return RoleSlotResponse(
        id=slot.id,
        group_id=slot.group_id,
        event_id=slot.event_id,
        role_name=slot.role_name,
        starts_at=iso_utc(slot.starts_at),
        ends_at=iso_utc(slot.ends_at),
        claimed_by_id=slot.claimed_by_id,
        claimed_by_label=slot.claimed_by_label,
        claimed=slot.claimed_by_id is not None or bool(slot.claimed_by_label),
        created_at=iso_utc(slot.created_at),
    )


class GapsResponse(BaseModel):
    """"What is still missing?" for one event (B-086): one flat
    gaps list (badge-shaped) plus the sections behind it."""

    event_id: str
    gaps: list[dict]
    missing_fields: list[str]
    open_questions: list[QuestionResponse]
    unclaimed_roles: list[RoleSlotResponse]
    unclaimed_tasks: list[TaskResponse]


class SupplyEventResponse(BaseModel):
    event_id: str
    event_title: str
    quantity: int
    claimed: bool = False


class SupplyLineResponse(BaseModel):
    """One folded shopping line of the merged view (B-088)."""

    label: str
    total_quantity: int
    events: list[SupplyEventResponse]


# ------------------------------------------------------------------
# Budget-lite (B-089)
# ------------------------------------------------------------------


class CostLineCreateRequest(BaseModel):
    """``amount_cents`` is an int — a float body is refused by
    this schema before any service runs (B-089's law)."""

    label: str = Field(min_length=1, max_length=240)
    amount_cents: int
    paid_by_id: str | None = None
    paid_by_label: str = ""


class CostLineResponse(BaseModel):
    id: str
    group_id: str
    event_id: str
    label: str
    amount_cents: int
    paid_by_id: str | None = None
    paid_by_label: str = ""
    created_by: str
    created_at: str


def cost_line_view(line) -> CostLineResponse:
    return CostLineResponse(
        id=line.id,
        group_id=line.group_id,
        event_id=line.event_id,
        label=line.label,
        amount_cents=line.amount_cents,
        paid_by_id=line.paid_by_id,
        paid_by_label=line.paid_by_label,
        created_by=line.created_by,
        created_at=iso_utc(line.created_at),
    )


class BalanceResponse(BaseModel):
    member_id: str | None = None
    member_label: str = ""
    paid_cents: int


class BudgetResponse(BaseModel):
    event_id: str
    total_cents: int
    lines: list[CostLineResponse]
    balances: list[BalanceResponse]


# ------------------------------------------------------------------
# Availability (B-090)
# ------------------------------------------------------------------


class AvailabilitySlotCreateRequest(BaseModel):
    starts_at: datetime
    ends_at: datetime
    label: str = ""
    event_id: str | None = None


class AvailabilityMarkRequest(BaseModel):
    can: bool


class AvailabilitySlotResponse(BaseModel):
    """One proposed slot with its marks and its rank inputs —
    can/can't counts and who said which (the group's own marks,
    nothing else; B-090's privacy boundary is this shape)."""

    id: str
    group_id: str
    event_id: str | None = None
    label: str = ""
    starts_at: str
    ends_at: str
    can_count: int = 0
    cant_count: int = 0
    can_members: list[str] = []
    cant_members: list[str] = []
    created_at: str


def availability_view(row: dict) -> AvailabilitySlotResponse:
    slot = row["slot"]
    return AvailabilitySlotResponse(
        id=slot.id,
        group_id=slot.group_id,
        event_id=slot.event_id,
        label=slot.label,
        starts_at=iso_utc(slot.starts_at),
        ends_at=iso_utc(slot.ends_at),
        can_count=row["can_count"],
        cant_count=row["cant_count"],
        can_members=row["can_members"],
        cant_members=row["cant_members"],
        created_at=iso_utc(slot.created_at),
    )


# ------------------------------------------------------------------
# Templates (B-091)
# ------------------------------------------------------------------


class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    payload: dict


class TemplateFromEventRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TemplateInstantiateRequest(BaseModel):
    starts_at: datetime
    title: str | None = Field(default=None, max_length=240)


class TemplateResponse(BaseModel):
    id: str
    group_id: str | None = None
    is_starter: bool = False
    name: str
    description: str = ""
    payload: dict = {}
    created_by: str = ""
    created_at: str


def template_view(template, payload: dict) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        group_id=template.group_id,
        is_starter=template.group_id is None,
        name=template.name,
        description=template.description,
        payload=payload,
        created_by=template.created_by,
        created_at=iso_utc(template.created_at),
    )


class InstantiationResponse(BaseModel):
    """What instantiating built: the event (full event view
    rides in ``event`` as a plain dict from the events schema)
    and the ids of the plan built around it."""

    template_id: str
    event_id: str
    task_ids: list[str]
    checklist_item_ids: list[str]
    role_slot_ids: list[str]


# ------------------------------------------------------------------
# Search (B-092) + digest (B-093)
# ------------------------------------------------------------------


class SearchHitResponse(BaseModel):
    type: str
    id: str
    title: str
    snippet: str = ""
    score: float
    event_id: str | None = None
    candidate_id: str | None = None
    source_id: str | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHitResponse]


class DigestEventResponse(BaseModel):
    id: str
    title: str
    starts_at: str
    ends_at: str
    timezone: str
    place_name: str = ""
    status: str


class PlanningDigestResponse(BaseModel):
    """"State of the plan" (B-093): the week's events, what is
    unclaimed, what is unanswered, what is undecided."""

    week_start: str
    week_end: str
    events_this_week: list[DigestEventResponse]
    unclaimed_tasks: list[TaskResponse]
    open_questions: list[QuestionResponse]
    undecided_polls: list[PollResponse]
