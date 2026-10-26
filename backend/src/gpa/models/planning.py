"""Planning tables (roadmap Phase 7, B-083 → B-093).

An event says *when*; this module is everything around it — who
does what, what was decided, what is still open — as data:

- ``Task`` — work with an owner, a due moment, and a status flow
  (B-083). Minted from approved task candidates or created
  directly. The founding honesty rule rides on two columns: the
  model's *suggested* owner words are kept as words
  (``suggested_owner_label``) and NEVER become an assignment —
  ``owner_id`` is set only by a person's act (a human create, a
  human assignment, or a reviewer's edit of the draft naming a
  real member). A task with no ``owner_id`` is ``unclaimed``,
  visibly, in every response.
- ``Decision`` — the group's settled truth (B-085). A later
  decision may *supersede* an earlier one; both rows are kept and
  the chain is queryable. ``status`` says which end of the chain
  a row sits at: ``current`` or ``superseded``.
- ``OpenQuestion`` — a question somebody still owes an answer
  (B-086). It may name who it is asked of, and resolving it can
  mint a ``Decision`` in the same act (``resolution_decision_id``).
- ``Poll`` / ``PollOption`` / ``PollVote`` — questions the group
  answers by voting (B-087). A poll is ``choice`` (words) or
  ``date`` (its options carry proposed instants, and the winner
  can mint or move an event). One member holds ONE current vote:
  ``PollVote`` is unique per (poll, voter) and re-voting updates
  the row — the tally is always read off committed rows.
- ``ChecklistItem`` / ``RoleSlot`` — an event's bring/prepare
  list and its named jobs (host, driver, cook…), each claimable
  by one person (B-088).
- ``CostLine`` — budget-lite (B-089): what one event cost, line
  by line, in **integer cents** — money is never a float here.
  Deliberately stops at the group's truth: no ledger, no
  payments, just who paid what.
- ``AvailabilitySlot`` / ``AvailabilityMark`` — proposed moments
  and members' can/can't marks against them (B-090). A mark is a
  boolean about a PROPOSED slot and nothing else: no member's
  private calendar is ever read, inferred, or stored.
- ``Template`` — a reusable event + task-set shape (B-091).
  ``group_id`` NULL marks a global starter template shipped as
  seed; otherwise the template belongs to one group. The shape
  itself is JSON (``payload_json``) — services/templates.py owns
  its grammar.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc

# A task's status flow (B-083): work is open, then being done,
# then done — or cancelled, which is a decision, not a delete.
TASK_STATUSES: tuple[str, ...] = ("open", "in_progress", "done", "cancelled")

# A question's life (B-086): open until a person resolves it
# (with or without minting a decision) or drops it.
QUESTION_STATUSES: tuple[str, ...] = ("open", "resolved", "dropped")

# Poll kinds (B-087): "choice" options are words; "date" options
# carry a proposed instant and the winner can become an event.
POLL_KINDS: tuple[str, ...] = ("choice", "date")

# Checklist sections (B-088): what to bring, what to prepare.
CHECKLIST_SECTIONS: tuple[str, ...] = ("bring", "prepare")


class Task(Base):
    """One piece of work the plan needs done (B-083)."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    # The event this work belongs to, when it belongs to one.
    # Plain reads treat a missing event as a group-level task.
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The named sub-group this task is scoped to (Phase 9,
    # B-105); NULL = group-wide. The same visibility rule as
    # scoped events, enforced in services/subgroups.py.
    subgroup_id: Mapped[str | None] = mapped_column(
        ForeignKey("subgroups.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(240), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # The owner, when a PERSON has made them the owner: a user id
    # plus the display words captured at assignment. NULL owner_id
    # is the unclaimed state — computed into every response, never
    # papered over by the model's suggestion below.
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    owner_label: Mapped[str] = mapped_column(String(160), default="")
    # The draft's owner words, verbatim ("Ben"), kept as evidence
    # of what the source said. A suggestion, never an assignment.
    suggested_owner_label: Mapped[str] = mapped_column(String(160), default="")
    # When the work is due, naive UTC like every stored instant
    # (D-21); NULL = no due moment was ever resolved or set.
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The candidate whose approval minted this task (B-083);
    # NULL for direct human entry.
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class Decision(Base):
    """One settled thing, and where it sits in its chain (B-085)."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    text: Mapped[str] = mapped_column(Text, default="")
    # current = the group's settled truth on its subject;
    # superseded = kept for the record, replaced by the row in
    # superseded_by_id. The chain walks both directions.
    status: Mapped[str] = mapped_column(String(16), default="current", index=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("decisions.id"), nullable=True
    )
    superseded_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("decisions.id"), nullable=True
    )
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class OpenQuestion(Base):
    """A question the group has not answered yet (B-086)."""

    __tablename__ = "open_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    text: Mapped[str] = mapped_column(Text, default="")
    # Who the question is asked of, when a person has named them
    # (a user id + the words); NULL = asked of the whole group.
    asked_of_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asked_of_label: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    # When resolution minted a decision, the link to it (B-086) —
    # the question's answer is then the group's settled truth.
    resolution_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("decisions.id"), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Poll(Base):
    """A question the group answers by voting (B-087)."""

    __tablename__ = "polls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(String(240), default="")
    kind: Mapped[str] = mapped_column(String(16), default="choice")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    # When voting closes by the clock; a vote at/after it is
    # refused the same as a vote on a closed poll. NULL = closes
    # only when an organiser closes it.
    closes_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The event a date-poll winner was applied to (B-087), once an
    # organiser has pressed that button; NULL until then.
    applied_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PollOption(Base):
    """One votable answer on a poll (B-087)."""

    __tablename__ = "poll_options"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    poll_id: Mapped[str] = mapped_column(
        ForeignKey("polls.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(240), default="")
    # Date polls only: the proposed instant (naive UTC), with a
    # proposed length so a winner can mint a whole event.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class PollVote(Base):
    """One member's CURRENT vote on one poll (B-087).

    Unique per (poll, voter): voting again updates this row, so
    the tally is a count of rows grouped by option — one member,
    one current vote, by construction, not by tally-time hope.
    ``voter_label`` is the principal's audit label ("user:<id>" /
    "seat:<id>"), the same identity every other module uses.
    """

    __tablename__ = "poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "voter_label"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    poll_id: Mapped[str] = mapped_column(
        ForeignKey("polls.id", ondelete="CASCADE"), index=True
    )
    option_id: Mapped[str] = mapped_column(
        ForeignKey("poll_options.id", ondelete="CASCADE"), index=True
    )
    voter_label: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seat_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class ChecklistItem(Base):
    """One line of an event's bring/prepare list (B-088)."""

    __tablename__ = "checklist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[str] = mapped_column(String(16), default="bring")
    label: Mapped[str] = mapped_column(String(240), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(Text, default="")
    # The claim: who is bringing/preparing this, once a person
    # has said so. NULL claimer = still needed — the merged
    # supplies view counts it either way (B-088).
    claimed_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_by_label: Mapped[str] = mapped_column(String(160), default="")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class RoleSlot(Base):
    """One named job an event needs a person for (B-088).

    Slots are individual rows — an event needing two drivers has
    two ``driver`` rows — so claimed/unclaimed is per-person by
    construction and the gaps endpoint (B-086) just counts the
    unclaimed ones.
    """

    __tablename__ = "role_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    role_name: Mapped[str] = mapped_column(String(80), default="")
    # The shift window (Phase 9, B-107): a plain B-088 role slot
    # carries no times; a SHIFT's slots all carry the same
    # start/end, so "who is on, when" is data and the gap report
    # can speak in days. Naive UTC like every stored instant
    # (D-21); both NULL for a timeless slot.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_by_label: Mapped[str] = mapped_column(String(160), default="")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class CostLine(Base):
    """One line of what an event cost (B-089).

    ``amount_cents`` is an INTEGER, always: money never travels
    as a float anywhere in this codebase, so the arithmetic the
    balance summary reports is exact by construction.
    """

    __tablename__ = "cost_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(240), default="")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Who paid: a user id + display words when known; a line a
    # person enters about someone else's spend carries their
    # words as the label and no id.
    paid_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    paid_by_label: Mapped[str] = mapped_column(String(160), default="")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AvailabilitySlot(Base):
    """One proposed moment members mark can/can't against (B-090)."""

    __tablename__ = "availability_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    label: Mapped[str] = mapped_column(String(240), default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AvailabilityMark(Base):
    """One member's can/can't against one proposed slot (B-090).

    The whole record is a boolean about a proposed moment: this
    is the privacy boundary of the availability finder — nothing
    about WHY, and nothing from anyone's own calendar, is ever
    stored here. Unique per (slot, member); re-marking updates.
    """

    __tablename__ = "availability_marks"
    __table_args__ = (UniqueConstraint("slot_id", "voter_label"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slot_id: Mapped[str] = mapped_column(
        ForeignKey("availability_slots.id", ondelete="CASCADE"), index=True
    )
    voter_label: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seat_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    can_attend: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class Template(Base):
    """A reusable plan shape (B-091).

    ``payload_json`` grammar (services/templates.py owns it):
    ``{"event": {title, description, duration_minutes, all_day,
    place_name, event_type, bring_list, cost_text},
      "tasks": [{title, notes, due_offset_days}],
      "checklist": [{label, quantity, section}],
      "roles": [{role_name, slots}]}``.
    Relative dates (offsets) rather than instants: a template is
    a shape, and the instant arrives at instantiation.
    """

    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("group_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # NULL = a global starter template, readable by every group.
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("groups.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(80), default="")  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


def task_overdue(task: Task, *, now: datetime | None = None) -> bool:
    """Overdue is computed, never stored (B-083): a task with a
    due moment in the past that is not done or cancelled.

    A pure function of the row, kept in the data layer (B-161)
    so services and schemas compute it identically and no schema
    ever has to import a service to ask the question.
    """
    if task.due_at is None or task.status in ("done", "cancelled"):
        return False
    return task.due_at < (now or now_utc())
