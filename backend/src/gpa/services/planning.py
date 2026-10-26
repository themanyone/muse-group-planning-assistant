"""Planning: tasks, decisions, open questions (roadmap B-083,
B-085, B-086), plus the cross-cutting reads — the event gaps
view (B-086), planning search (B-092), and the digest data
(B-093).

The mint pattern is Phase 4's, extended: approval of a task /
decision / question candidate mints its entity in the
approval's own transaction (services/groups.py calls the
``mint_*_from_candidate`` functions here). Minting NEVER
commits — the approval transaction owns the commit, so a mint
that refuses takes the whole approval back with it and the
candidate stays pending, exactly like an unmintable event.

The task-owner law (B-083) lives in the mint and in create:
the extraction draft's owner field is the model's reading of
the words ("Ben" volunteered in the thread) and lands ONLY in
``suggested_owner_label``. ``owner_id`` — the assignment — is
set when a PERSON sets it: a human creating the task, a human
assigning it later, or a reviewer editing the draft to name a
real member (``owner_id`` in the edited draft). A task with no
owner is ``unclaimed`` in every response — computed here, so no
client has to guess and no surface can hide it.
"""

import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event, EventSourceLink
from gpa.models.groups import Candidate, Group
from gpa.models.identity import User, now_utc
from gpa.models.planning import Decision, OpenQuestion, Poll, Task
from gpa.services import audit
from gpa.services import events as event_service
from gpa.services.groups import active_membership

# The task status flow (models/planning.py TASK_STATUSES). A
# task may move forward, be cancelled, or be reopened from done
# (people change their minds; the flow records it, it does not
# forbid it). Same-state "transitions" are no-ops, not errors.
TASK_TRANSITIONS: dict[str, set[str]] = {
    "open": {"open", "in_progress", "done", "cancelled"},
    "in_progress": {"in_progress", "open", "done", "cancelled"},
    "done": {"done", "open"},
    "cancelled": {"cancelled", "open"},
}


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def parse_draft(candidate: Candidate) -> dict:
    """A candidate's draft JSON as a dict ({} when unreadable)."""
    try:
        draft = json.loads(candidate.draft_json or "{}")
    except json.JSONDecodeError:
        return {}
    return draft if isinstance(draft, dict) else {}


def draft_field(draft: dict, name: str):
    """A draft field's value: the top-level mirror first, then
    the fields map's value (the extraction draft shape)."""
    if draft.get(name) is not None:
        return draft[name]
    fields = draft.get("fields", {})
    entry = fields.get(name, {}) if isinstance(fields, dict) else {}
    return entry.get("value") if isinstance(entry, dict) else None


def member_label(db: Session, group: Group, user_id: str) -> tuple[User, str] | None:
    """(user, display words) when ``user_id`` is an ACTIVE member
    of the group, else None. The display words are the person's
    name when they have one, else their email — the same words
    every surface shows for them."""
    membership = active_membership(db, user_id, group.id)
    if membership is None:
        return None
    user = db.get(User, user_id)
    if user is None:
        return None
    return user, (user.name or user.email)


def owner_display(db: Session, group: Group, principal_id: str) -> str | None:
    """Display words for a task's owner (Phase 19, B-202): an
    ACTIVE member person (their name, else their email) or an
    agent SEAT of this group (its name) — the two kinds of
    citizen a task can belong to, under D-35. ``owner_id`` on
    the task holds the principal's id of either kind; this is
    the one door that resolves it. Anyone else — a non-member,
    another group's seat — resolves to None, and the callers
    refuse the assignment in plain words."""
    found = member_label(db, group, principal_id)
    if found is not None:
        return found[1]
    from gpa.models.groups import AgentSeat

    seat = db.get(AgentSeat, principal_id)
    if seat is not None and seat.group_id == group.id:
        return seat.name
    return None


def get_task(db: Session, group: Group, task_id: str) -> Task:
    """One task of this group, or 404 (another group's task is
    indistinguishable from a missing one)."""
    task = db.get(Task, task_id)
    if task is None or task.group_id != group.id:
        raise AppError("not_found", detail="That task could not be found.")
    return task


def get_decision(db: Session, group: Group, decision_id: str) -> Decision:
    """One decision of this group, or 404."""
    decision = db.get(Decision, decision_id)
    if decision is None or decision.group_id != group.id:
        raise AppError("not_found", detail="That decision could not be found.")
    return decision


def get_question(db: Session, group: Group, question_id: str) -> OpenQuestion:
    """One open question of this group, or 404."""
    question = db.get(OpenQuestion, question_id)
    if question is None or question.group_id != group.id:
        raise AppError("not_found", detail="That question could not be found.")
    return question


def visible_event(db: Session, group: Group, event_id: str, role: str | None) -> Event:
    """The event a planning write attaches to — it must exist and
    be visible to the caller (a draft's planning is as private
    as the draft)."""
    return event_service.get_visible_event(db, group, event_id, role)


# ------------------------------------------------------------------
# Tasks (B-083)
# ------------------------------------------------------------------


def _due_from_draft(draft: dict, group: Group) -> datetime | None:
    """A task draft's resolved due moment → naive UTC.

    The extraction mirrors carry the resolver's answer:
    ``due_at`` ("YYYY-MM-DDTHH:MM", group-local) when a single
    time resolved, else ``due_date`` — read as END of that local
    day (a thing "due Friday" is due by Friday's end; recorded
    decision). No resolved date → no due moment: the mint never
    invents one from an ambiguous phrase."""
    tz = ZoneInfo(group.home_timezone or "UTC")
    mirror = draft.get("due_at")
    if isinstance(mirror, str) and "T" in mirror:
        try:
            local = datetime.fromisoformat(mirror)
        except ValueError:
            local = None
        if local is not None:
            return local.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    date_text = draft.get("due_date")
    if isinstance(date_text, str) and date_text:
        try:
            day = datetime.fromisoformat(date_text).date()
        except ValueError:
            return None
        local_end = datetime.combine(day, time(23, 59, 59))
        return local_end.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return None


def mint_task_from_candidate(
    db: Session, *, group: Group, candidate: Candidate, actor_label: str
) -> Task | None:
    """Mint the Task an approved task candidate becomes (B-083).

    Called inside the approval transaction; never commits.
    Other kinds return None. The draft's owner WORDS land in
    ``suggested_owner_label`` only — an assignment happens here
    solely when a person put ``owner_id`` into the (possibly
    edited) draft, naming a real member of the group; a draft
    naming a non-member refuses the mint in plain words, like
    an event draft with no date refuses."""
    if candidate.kind != "task":
        return None
    draft = parse_draft(candidate)
    title = str(draft_field(draft, "title") or candidate.title or "").strip()
    if not title:
        raise AppError(
            "validation_error",
            detail="That task draft has no title, so it cannot become a task yet — edit the draft first.",
        )
    owner_id: str | None = None
    owner_label = ""
    named_owner = draft.get("owner_id")
    if isinstance(named_owner, str) and named_owner:
        # A reviewer naming a member OR a seat of this group
        # (B-202: seats own tasks too) — the owner law itself is
        # unchanged: the draft's words alone never assign.
        owner_label = owner_display(db, group, named_owner) or ""
        if not owner_label:
            raise AppError(
                "validation_error",
                detail=(
                    "That draft names an owner who is not a member of "
                    "this group — edit the draft to fix the owner first."
                ),
            )
        owner_id = named_owner
    task = Task(
        group_id=group.id,
        title=title[:240],
        notes=str(draft_field(draft, "notes") or ""),
        owner_id=owner_id,
        owner_label=owner_label,
        suggested_owner_label=str(draft_field(draft, "owner") or "")[:160],
        due_at=_due_from_draft(draft, group),
        status="open",
        candidate_id=candidate.id,
        created_by=actor_label,
    )
    db.add(task)
    db.flush()
    candidate.resulting_entity_type = "task"
    candidate.resulting_entity_id = task.id
    audit.record(
        db, actor=actor_label, action="task.minted",
        group_id=group.id, entity_type="task", entity_id=task.id,
        detail={"candidate_id": candidate.id},
    )
    return task


def create_task(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    title: str,
    notes: str = "",
    event_id: str | None = None,
    owner_id: str | None = None,
    due_at: datetime | None = None,
    subgroup_id: str | None = None,
    actor_user_id: str | None = None,
) -> Task:
    """Direct human entry (B-083). A person creating a task MAY
    name its owner in the same act — that is the assignment, and
    it is a person's act by construction here. The owner must be
    a member of the group. Scoping the task to a sub-group
    (B-105) takes membership of that sub-group, or an
    organiser's hand — you cannot file work into a room you
    cannot see."""
    if not title or not title.strip():
        raise AppError("validation_error", detail="A task needs a title.")
    if event_id is not None:
        visible_event(db, group, event_id, viewer_role)
    if subgroup_id is not None:
        from gpa.services import subgroups as subgroup_service

        subgroup_service.get_subgroup(db, group, subgroup_id)
        visible_ids = subgroup_service.visible_subgroup_ids(
            db, group, user_id=actor_user_id, role=viewer_role
        )
        if not subgroup_service.can_see_scoped(subgroup_id, visible_ids):
            raise AppError(
                "forbidden",
                detail="Scoping a task to a sub-group takes membership of it, or an organiser.",
            )
    owner_label = ""
    if owner_id is not None:
        # A member person or a seat of this group (B-202).
        owner_label = owner_display(db, group, owner_id) or ""
        if not owner_label:
            raise AppError(
                "validation_error",
                detail="The owner must be a member of this group.",
            )
    task = Task(
        group_id=group.id,
        event_id=event_id,
        subgroup_id=subgroup_id,
        title=title.strip()[:240],
        notes=notes,
        owner_id=owner_id,
        owner_label=owner_label,
        due_at=due_at,
        status="open",
        created_by=actor_label,
    )
    db.add(task)
    db.flush()
    audit.record(
        db, actor=actor_label, action="task.created",
        group_id=group.id, entity_type="task", entity_id=task.id,
        detail={"event_id": event_id, "owner_id": owner_id},
    )
    db.commit()
    return task


def list_tasks(
    db: Session,
    group: Group,
    *,
    event_id: str | None = None,
    status: str | None = None,
    unclaimed: bool = False,
    owner_id: str | None = None,
    subgroup_id: str | None = None,
    subgroup_scope: set[str] | None = None,
    scope_restricted: bool = False,
) -> list[Task]:
    """The group's tasks, soonest-due first (undated last).

    ``subgroup_id`` narrows to one sub-group's work;
    ``subgroup_scope`` (B-105, applied when ``scope_restricted``)
    is the viewer's visible set from services/subgroups — a
    scoped task outside it never reaches the list."""
    query = db.query(Task).filter(Task.group_id == group.id)
    if event_id is not None:
        query = query.filter(Task.event_id == event_id)
    if status is not None:
        query = query.filter(Task.status == status)
    if unclaimed:
        query = query.filter(Task.owner_id.is_(None))
    if owner_id is not None:
        query = query.filter(Task.owner_id == owner_id)
    if subgroup_id is not None:
        query = query.filter(Task.subgroup_id == subgroup_id)
    rows = query.all()
    if scope_restricted:
        from gpa.services import subgroups as subgroup_service

        rows = [
            row for row in rows
            if subgroup_service.can_see_scoped(row.subgroup_id, subgroup_scope)
        ]
    rows.sort(key=lambda t: (t.due_at is None, t.due_at or t.created_at, t.created_at, t.id))
    return rows


def update_task(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    task_id: str,
    changes: dict,
) -> Task:
    """Edit a task (B-083): title, notes, due moment, status,
    owner. ``changes`` carries only the fields the caller sent
    (the router's exclude_unset); ``owner_id: None`` UNASSIGNS —
    an explicit human act, recorded like any other."""
    task = get_task(db, group, task_id)
    if "title" in changes:
        if not str(changes["title"]).strip():
            raise AppError("validation_error", detail="A task needs a title.")
        task.title = str(changes["title"]).strip()[:240]
    if "notes" in changes:
        task.notes = str(changes["notes"])
    if "subgroup_id" in changes:
        # Re-scoping names a sub-group of this group, or clears
        # the scope with null (B-105).
        if changes["subgroup_id"] is not None:
            from gpa.services import subgroups as subgroup_service

            subgroup_service.get_subgroup(db, group, changes["subgroup_id"])
        task.subgroup_id = changes["subgroup_id"]
    if "due_at" in changes:
        task.due_at = changes["due_at"]
    if "owner_id" in changes:
        new_owner = changes["owner_id"]
        if new_owner is None:
            task.owner_id = None
            task.owner_label = ""
        else:
            # A member person or a seat of this group (B-202).
            resolved = owner_display(db, group, new_owner)
            if resolved is None:
                raise AppError(
                    "validation_error",
                    detail="The owner must be a member of this group.",
                )
            task.owner_label = resolved
            task.owner_id = new_owner
    if "status" in changes:
        new_status = changes["status"]
        if new_status not in TASK_TRANSITIONS.get(task.status, set()):
            raise AppError(
                "conflict",
                detail=f"A {task.status} task cannot become {new_status}.",
            )
        task.status = new_status
        task.completed_at = now_utc() if new_status == "done" else None
    audit.record(
        db, actor=actor_label, action="task.updated",
        group_id=group.id, entity_type="task", entity_id=task.id,
        detail={"fields": sorted(changes)},
    )
    db.commit()
    return task


# ------------------------------------------------------------------
# Decisions (B-085)
# ------------------------------------------------------------------


def mint_decision_from_candidate(
    db: Session, *, group: Group, candidate: Candidate, actor_label: str
) -> Decision | None:
    """Mint the Decision an approved decision candidate becomes
    (B-085), inside the approval transaction; never commits."""
    if candidate.kind != "decision":
        return None
    draft = parse_draft(candidate)
    text = str(draft_field(draft, "text") or candidate.title or "").strip()
    if not text:
        raise AppError(
            "validation_error",
            detail="That decision draft has no words, so it cannot be recorded yet — edit the draft first.",
        )
    decision = Decision(
        group_id=group.id,
        text=text,
        status="current",
        candidate_id=candidate.id,
        created_by=actor_label,
    )
    db.add(decision)
    db.flush()
    candidate.resulting_entity_type = "decision"
    candidate.resulting_entity_id = decision.id
    audit.record(
        db, actor=actor_label, action="decision.minted",
        group_id=group.id, entity_type="decision", entity_id=decision.id,
        detail={"candidate_id": candidate.id},
    )
    return decision


def create_decision(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    text: str,
    event_id: str | None = None,
    supersedes_id: str | None = None,
) -> Decision:
    """Record a decision directly (B-085). ``supersedes_id``
    chains it over an earlier CURRENT decision: the earlier row
    is kept, marked superseded, and points forward; the new row
    points back. Both ends are written in the one transaction,
    so the chain can never be half-linked."""
    if not text or not text.strip():
        raise AppError("validation_error", detail="A decision needs its words.")
    if event_id is not None:
        visible_event(db, group, event_id, viewer_role)
    previous: Decision | None = None
    if supersedes_id is not None:
        previous = get_decision(db, group, supersedes_id)
        if previous.status != "current":
            raise AppError(
                "conflict",
                detail="That decision has already been superseded — supersede the current one.",
            )
    decision = Decision(
        group_id=group.id,
        event_id=event_id,
        text=text.strip(),
        status="current",
        supersedes_id=previous.id if previous else None,
        created_by=actor_label,
    )
    db.add(decision)
    db.flush()
    if previous is not None:
        previous.status = "superseded"
        previous.superseded_by_id = decision.id
    audit.record(
        db, actor=actor_label, action="decision.created",
        group_id=group.id, entity_type="decision", entity_id=decision.id,
        detail={"supersedes_id": supersedes_id},
    )
    db.commit()
    return decision


def list_decisions(
    db: Session, group: Group, *, current_only: bool = False, event_id: str | None = None
) -> list[Decision]:
    """The decisions log, newest first. ``current_only`` is the
    settled-truth view (B-085)."""
    query = db.query(Decision).filter(Decision.group_id == group.id)
    if current_only:
        query = query.filter(Decision.status == "current")
    if event_id is not None:
        query = query.filter(Decision.event_id == event_id)
    return query.order_by(Decision.created_at.desc(), Decision.id.desc()).all()


def decision_chain(db: Session, group: Group, decision_id: str) -> list[Decision]:
    """The full supersede chain containing one decision, oldest
    first (B-085): walk back to the root, then forward to the
    current end."""
    row = get_decision(db, group, decision_id)
    root = row
    seen: set[str] = set()
    while root.supersedes_id and root.supersedes_id not in seen:
        seen.add(root.id)
        parent = db.get(Decision, root.supersedes_id)
        if parent is None or parent.group_id != group.id:
            break
        root = parent
    chain = [root]
    seen = {root.id}
    while chain[-1].superseded_by_id and chain[-1].superseded_by_id not in seen:
        nxt = db.get(Decision, chain[-1].superseded_by_id)
        if nxt is None or nxt.group_id != group.id:
            break
        chain.append(nxt)
        seen.add(nxt.id)
    return chain


# ------------------------------------------------------------------
# Open questions (B-086)
# ------------------------------------------------------------------


def mint_question_from_candidate(
    db: Session, *, group: Group, candidate: Candidate, actor_label: str
) -> OpenQuestion | None:
    """Mint the OpenQuestion an approved question candidate
    becomes (B-086), inside the approval transaction; never
    commits. A minted question is asked of the whole group —
    naming who must answer is a person's act (PATCH)."""
    if candidate.kind != "question":
        return None
    draft = parse_draft(candidate)
    text = str(draft_field(draft, "text") or candidate.title or "").strip()
    if not text:
        raise AppError(
            "validation_error",
            detail="That question draft has no words, so it cannot be recorded yet — edit the draft first.",
        )
    question = OpenQuestion(
        group_id=group.id,
        text=text,
        status="open",
        candidate_id=candidate.id,
        created_by=actor_label,
    )
    db.add(question)
    db.flush()
    candidate.resulting_entity_type = "question"
    candidate.resulting_entity_id = question.id
    audit.record(
        db, actor=actor_label, action="question.minted",
        group_id=group.id, entity_type="question", entity_id=question.id,
        detail={"candidate_id": candidate.id},
    )
    return question


def create_question(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    text: str,
    event_id: str | None = None,
    asked_of_id: str | None = None,
) -> OpenQuestion:
    """Ask a question directly (B-086), optionally of a named
    member."""
    if not text or not text.strip():
        raise AppError("validation_error", detail="A question needs its words.")
    if event_id is not None:
        visible_event(db, group, event_id, viewer_role)
    asked_of_label = ""
    if asked_of_id is not None:
        found = member_label(db, group, asked_of_id)
        if found is None:
            raise AppError(
                "validation_error",
                detail="A question can only be asked of a member of this group.",
            )
        _user, asked_of_label = found
    question = OpenQuestion(
        group_id=group.id,
        event_id=event_id,
        text=text.strip(),
        asked_of_id=asked_of_id,
        asked_of_label=asked_of_label,
        status="open",
        created_by=actor_label,
    )
    db.add(question)
    db.flush()
    audit.record(
        db, actor=actor_label, action="question.created",
        group_id=group.id, entity_type="question", entity_id=question.id,
        detail={"event_id": event_id, "asked_of_id": asked_of_id},
    )
    db.commit()
    return question


def list_questions(
    db: Session,
    group: Group,
    *,
    status: str | None = None,
    event_id: str | None = None,
) -> list[OpenQuestion]:
    """The group's questions, oldest open first."""
    query = db.query(OpenQuestion).filter(OpenQuestion.group_id == group.id)
    if status is not None:
        query = query.filter(OpenQuestion.status == status)
    if event_id is not None:
        query = query.filter(OpenQuestion.event_id == event_id)
    return query.order_by(OpenQuestion.created_at, OpenQuestion.id).all()


def update_question(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    question_id: str,
    changes: dict,
) -> OpenQuestion:
    """Edit a question: its words, its event, who it is asked of
    (``asked_of_id: None`` un-names them), or drop it."""
    question = get_question(db, group, question_id)
    if question.status != "open":
        raise AppError("conflict", detail="That question is already settled.")
    if "text" in changes:
        if not str(changes["text"]).strip():
            raise AppError("validation_error", detail="A question needs its words.")
        question.text = str(changes["text"]).strip()
    if "asked_of_id" in changes:
        asked_of_id = changes["asked_of_id"]
        if asked_of_id is None:
            question.asked_of_id = None
            question.asked_of_label = ""
        else:
            found = member_label(db, group, asked_of_id)
            if found is None:
                raise AppError(
                    "validation_error",
                    detail="A question can only be asked of a member of this group.",
                )
            _user, question.asked_of_label = found
            question.asked_of_id = asked_of_id
    if changes.get("drop") is True:
        question.status = "dropped"
        question.resolved_by = actor_label
        question.resolved_at = now_utc()
    audit.record(
        db, actor=actor_label, action="question.updated",
        group_id=group.id, entity_type="question", entity_id=question.id,
        detail={"fields": sorted(k for k in changes if k != "drop")},
    )
    db.commit()
    return question


def resolve_question(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    question_id: str,
    decision_text: str | None = None,
) -> OpenQuestion:
    """Resolve a question (B-086). With ``decision_text``, the
    resolution mints a Decision in the SAME transaction and the
    question links to it — the answer becomes the group's
    settled truth in the same act that closes the question."""
    question = get_question(db, group, question_id)
    if question.status != "open":
        raise AppError("conflict", detail="That question is already settled.")
    decision: Decision | None = None
    if decision_text is not None and decision_text.strip():
        decision = Decision(
            group_id=group.id,
            event_id=question.event_id,
            text=decision_text.strip(),
            status="current",
            created_by=actor_label,
        )
        db.add(decision)
        db.flush()
    question.status = "resolved"
    question.resolved_by = actor_label
    question.resolved_at = now_utc()
    question.resolution_decision_id = decision.id if decision else None
    audit.record(
        db, actor=actor_label, action="question.resolved",
        group_id=group.id, entity_type="question", entity_id=question.id,
        detail={"decision_id": decision.id if decision else None},
    )
    db.commit()
    return question


# ------------------------------------------------------------------
# The event gaps view (B-086)
# ------------------------------------------------------------------

# Event fields whose absence makes an event not-yet-planable.
# (Times are structural — an event cannot exist without them —
# so the gaps are the human facts around the moment.)
GAP_FIELDS: tuple[tuple[str, str], ...] = (
    ("place_name", "place"),
    ("host_label", "host"),
)


def event_gaps(db: Session, group: Group, event, *, viewer_role: str | None) -> dict:
    """What is still missing for one event (B-086): its absent
    fields, its open questions, its unclaimed roles, and its
    unclaimed tasks — one flat ``gaps`` list for badges, and the
    sections behind it for the screen that explains them."""
    from gpa.models.planning import RoleSlot

    gaps: list[dict] = []
    missing_fields: list[str] = []
    for attribute, label in GAP_FIELDS:
        value = getattr(event, attribute)
        if attribute == "host_label":
            absent = not value and event.host_id is None
        else:
            absent = not value
        if absent:
            missing_fields.append(label)
            gaps.append({"kind": "missing_field", "field": label, "label": f"No {label} yet"})
    questions = list_questions(db, group, status="open", event_id=event.id)
    for question in questions:
        gaps.append(
            {"kind": "open_question", "ref_id": question.id, "label": question.text}
        )
    roles = (
        db.query(RoleSlot)
        .filter(
            RoleSlot.event_id == event.id,
            RoleSlot.claimed_by_id.is_(None),
            RoleSlot.claimed_by_label == "",
        )
        .order_by(RoleSlot.created_at, RoleSlot.id)
        .all()
    )
    for slot in roles:
        gaps.append(
            {"kind": "unclaimed_role", "ref_id": slot.id, "label": f"No {slot.role_name} yet"}
        )
    unclaimed = [
        task
        for task in list_tasks(db, group, event_id=event.id)
        if task.owner_id is None and task.status in ("open", "in_progress")
    ]
    for task in unclaimed:
        gaps.append({"kind": "unclaimed_task", "ref_id": task.id, "label": task.title})
    return {
        "event": event,
        "gaps": gaps,
        "missing_fields": missing_fields,
        "open_questions": questions,
        "unclaimed_roles": roles,
        "unclaimed_tasks": unclaimed,
    }


# ------------------------------------------------------------------
# Search across planning (B-092)
# ------------------------------------------------------------------


def _score(haystacks: list[tuple[str, int]], terms: list[str]) -> float:
    """Deterministic ranking: each query term found in a
    haystack earns that haystack's weight; a term starting the
    primary text earns double. No learning, no magic — the same
    query over the same data always ranks the same (B-092)."""
    total = 0.0
    for index, (text, weight) in enumerate(haystacks):
        lowered = (text or "").lower()
        for term in terms:
            if term in lowered:
                total += weight
                if index == 0 and lowered.startswith(term):
                    total += weight
    return total


def _source_link_for(db: Session, *, candidate_id: str | None) -> tuple[str | None, str | None]:
    """(candidate_id, source_id) for a minted entity: the thread
    back to the material it came from (B-092's source link)."""
    if not candidate_id:
        return None, None
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        return candidate_id, None
    return candidate_id, candidate.source_id


def search_planning(
    db: Session,
    group: Group,
    query_text: str,
    *,
    subgroup_scope: set[str] | None = None,
    scope_restricted: bool = False,
) -> list[dict]:
    """One search over events, tasks, decisions, questions
    (B-092) — ranked, and every hit linked to its source where
    it has one. Tombstoned events never appear; drafts appear
    only as their organiser surfaces would show them is NOT this
    endpoint's call — search is a member tool over the group's
    planning, and events are searched at published/cancelled,
    the same bright line every member surface holds. With a
    restricted viewer (B-105), hits scoped to a sub-group the
    viewer is not in — directly, or through their event — never
    appear: search must not become the leak around scoping."""
    terms = [term for term in query_text.lower().split() if term]
    if not terms:
        return []
    hits: list[dict] = []

    if scope_restricted:
        from gpa.services import subgroups as subgroup_service

        event_scopes = {
            row.id: row.subgroup_id
            for row in db.query(Event).filter(Event.group_id == group.id).all()
        }

        def _visible(subgroup_id: str | None) -> bool:
            return subgroup_service.can_see_scoped(subgroup_id, subgroup_scope)
    else:
        event_scopes = {}

        def _visible(subgroup_id: str | None) -> bool:
            return True

    events = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.deleted_at.is_(None),
            Event.status.in_(("published", "cancelled")),
        )
        .all()
    )
    for event in events:
        if not _visible(event.subgroup_id):
            continue
        score = _score(
            [(event.title, 3), (event.place_name, 2), (event.host_label, 2),
             (event.description, 1)],
            terms,
        )
        if score > 0:
            candidate_id, source_id = _source_link_for(db, candidate_id=event.candidate_id)
            if source_id is None:
                link = (
                    db.query(EventSourceLink)
                    .filter(EventSourceLink.event_id == event.id)
                    .order_by(EventSourceLink.created_at, EventSourceLink.id)
                    .first()
                )
                source_id = link.source_id if link else None
            hits.append({
                "type": "event", "id": event.id, "title": event.title,
                "snippet": event.description or event.place_name,
                "score": score, "event_id": event.id,
                "candidate_id": candidate_id, "source_id": source_id,
            })

    for task in db.query(Task).filter(Task.group_id == group.id).all():
        if not _visible(task.subgroup_id):
            continue
        score = _score([(task.title, 3), (task.notes, 1)], terms)
        if score > 0:
            candidate_id, source_id = _source_link_for(db, candidate_id=task.candidate_id)
            hits.append({
                "type": "task", "id": task.id, "title": task.title,
                "snippet": task.notes, "score": score,
                "event_id": task.event_id,
                "candidate_id": candidate_id, "source_id": source_id,
            })

    for decision in db.query(Decision).filter(Decision.group_id == group.id).all():
        if not _visible(event_scopes.get(decision.event_id)):
            continue
        score = _score([(decision.text, 3)], terms)
        if score > 0:
            candidate_id, source_id = _source_link_for(db, candidate_id=decision.candidate_id)
            hits.append({
                "type": "decision", "id": decision.id, "title": decision.text,
                "snippet": decision.text, "score": score,
                "event_id": decision.event_id,
                "candidate_id": candidate_id, "source_id": source_id,
            })

    for question in db.query(OpenQuestion).filter(OpenQuestion.group_id == group.id).all():
        if not _visible(event_scopes.get(question.event_id)):
            continue
        score = _score([(question.text, 3)], terms)
        if score > 0:
            candidate_id, source_id = _source_link_for(db, candidate_id=question.candidate_id)
            hits.append({
                "type": "question", "id": question.id, "title": question.text,
                "snippet": question.text, "score": score,
                "event_id": question.event_id,
                "candidate_id": candidate_id, "source_id": source_id,
            })

    hits.sort(key=lambda h: (-h["score"], h["type"], h["title"], h["id"]))
    return hits


# ------------------------------------------------------------------
# Planning digest data (B-093)
# ------------------------------------------------------------------


def week_window(group: Group, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """This week as (start, end) naive-UTC instants: Monday
    00:00 group-local through the next Monday 00:00."""
    tz = ZoneInfo(group.home_timezone or "UTC")
    moment = (now or now_utc()).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    monday = (moment - timedelta(days=moment.weekday())).date()
    start_local = datetime.combine(monday, time(0, 0), tzinfo=tz)
    end_local = start_local + timedelta(days=7)
    return (
        start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
        end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
    )


def planning_digest(db: Session, group: Group, *, now: datetime | None = None) -> dict:
    """"State of the plan" (B-093): this week's published
    events, the group's unclaimed open tasks, its open
    questions, and its undecided (still-open) polls — the raw
    material of Phase 10's digest and the frontend's group
    home, computed in one place so both read the same truth."""
    start, end = week_window(group, now=now)
    events = (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.deleted_at.is_(None),
            Event.status == "published",
            Event.starts_at >= start,
            Event.starts_at < end,
        )
        .order_by(Event.starts_at, Event.id)
        .all()
    )
    unclaimed = [
        task
        for task in list_tasks(db, group)
        if task.owner_id is None and task.status in ("open", "in_progress")
    ]
    questions = list_questions(db, group, status="open")
    polls = (
        db.query(Poll)
        .filter(Poll.group_id == group.id, Poll.status == "open")
        .order_by(Poll.created_at, Poll.id)
        .all()
    )
    return {
        "week_start": start,
        "week_end": end,
        "events": events,
        "unclaimed_tasks": unclaimed,
        "open_questions": questions,
        "undecided_polls": polls,
    }
