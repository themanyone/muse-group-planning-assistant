"""Nonprofit-pack tables (roadmap Phase 9, B-105 → B-111).

Henry's anchor case: a local nonprofit whose members talk, plan,
and show up. The pack adds just enough structure — sub-groups and
chapters, an announcements board, volunteer shifts, bookable
resources, attendance aggregates — and none of the ERP weight.

- ``SubGroup`` / ``SubGroupMembership`` — named sub-groups inside
  a group (B-105). Group membership is INHERITED: every group
  member may belong to sub-groups; the membership row records the
  enrolment, plus an optional per-sub-group role that overrides
  the group role for scoped work. Events and tasks carry a
  ``subgroup_id``; a scoped item is visible only to the sub-group's
  enrolled members and to group organisers and above — that rule
  lives in services/subgroups.py and is enforced on the
  member-facing read surfaces.
- ``Announcement`` — the board (B-106): organiser-posted, pinned
  and expiring, separate from chat and from event discussion.
- ``Resource`` / ``ResourceBooking`` — the hall, the projector,
  the van (B-108): an item with a keeper, booked per event. A
  double-booking is a B-057 conflict flag, never a block.
- ``AttendanceMark`` — organiser-marked attendance (B-109). The
  rows exist so aggregates can be counted; NO read surface ever
  returns them per person — the API offers counts only, a
  deliberate product refusal recorded in the roadmap.
- ``CandidateApproval`` — one recorded approval of a candidate
  (B-111): the settings pack may require more than one approver,
  and each approver's act is its own row until the count is met.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc


class SubGroup(Base):
    """A named sub-group (a chapter, a crew, a team) in a group."""

    __tablename__ = "subgroups"
    __table_args__ = (UniqueConstraint("group_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class SubGroupMembership(Base):
    """One group member's enrolment in one sub-group (B-105).

    Membership is inherited — only a group member can hold a row
    here at all — and ``role`` is the OPTIONAL per-sub-group role:
    NULL means "my group role, unchanged"; a value overrides it
    for work scoped to this sub-group (a member of the whole group
    can be the kitchen crew's organiser).
    """

    __tablename__ = "subgroup_memberships"
    __table_args__ = (UniqueConstraint("subgroup_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subgroup_id: Mapped[str] = mapped_column(
        ForeignKey("subgroups.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Announcement(Base):
    """One post on the group's announcements board (B-106).

    An announcement is an organiser's act with an organiser
    account — never a seat's, never a member's (the B-043 law
    restated for humans). ``pinned`` posts lead the board;
    ``expires_at`` takes a post off the board when it passes
    (the row stays; expiry is a state, not an erasure). A post
    may be scoped to one sub-group, in which case the board
    shows it under the same visibility rule as scoped events.
    """

    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    subgroup_id: Mapped[str | None] = mapped_column(
        ForeignKey("subgroups.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(240), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    author_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class Resource(Base):
    """A bookable thing the group owns or borrows (B-108).

    The hall, the projector, the van. ``keeper`` is the person
    responsible for it: a member's id when the keeper is a
    member, plus the display words either way — a keeper can be
    "the hall committee" as words before they are a member row.
    """

    __tablename__ = "resources"
    __table_args__ = (UniqueConstraint("group_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    notes: Mapped[str] = mapped_column(Text, default="")
    keeper_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    keeper_label: Mapped[str] = mapped_column(String(160), default="")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class ResourceBooking(Base):
    """One resource booked for one event (B-108).

    Unique per (resource, event) — booking the van twice for the
    same event is one booking. Two DIFFERENT events holding the
    same resource over overlapping windows is a double-booking:
    allowed, and surfaced as a B-057 ``resource_overlap`` flag —
    flags, never blocks, is the codebase's conflict law.
    """

    __tablename__ = "resource_bookings"
    __table_args__ = (UniqueConstraint("resource_id", "event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    booked_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AttendanceMark(Base):
    """One person's recorded presence at one event (B-109).

    Organiser-marked, unique per (event, person). These rows are
    write-and-count ONLY: the API reads them back exclusively as
    aggregates (an event's headcount, an average over recent
    events) — there is deliberately no endpoint that lists who
    attended, and the schemas carry no per-person read shape.
    """

    __tablename__ = "attendance_marks"
    __table_args__ = (UniqueConstraint("event_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    marked_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class CandidateApproval(Base):
    """One person's recorded approval of one candidate (B-111).

    The settings pack may require more than one approver before a
    candidate is decided. Each approver's act lands here first —
    the candidate stays pending until the required count of
    DISTINCT approvers is met, when the final approver's call runs
    the ordinary approval (and mint) in its transaction. A seat
    can never hold a row here: approval is a person's act (B-018).
    """

    __tablename__ = "candidate_approvals"
    __table_args__ = (UniqueConstraint("candidate_id", "approver_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    approver_user_id: Mapped[str] = mapped_column(String(36), index=True)
    approver_label: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
