"""Nonprofit-pack schemas (roadmap Phase 9).

The wire shapes for sub-groups, announcements, shifts,
resources, attendance, member import/export, and the group
settings pack. As everywhere here: response shapes are built
from the rows by small view functions beside them, and request
shapes validate the closed vocabularies before a service runs.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from gpa.schemas.groups import ReminderRule
from gpa.schemas.planning import RoleSlotResponse, role_slot_view

# ------------------------------------------------------------------
# Sub-groups (B-105)
# ------------------------------------------------------------------


class SubGroupCreateRequest(BaseModel):
    """Open a named sub-group (a chapter, a crew) in a group."""

    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class SubGroupPatchRequest(BaseModel):
    """Rename / re-describe a sub-group; only sent fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class SubGroupResponse(BaseModel):
    """One sub-group, with its enrolled headcount."""

    id: str
    group_id: str
    name: str
    description: str = ""
    member_count: int = 0
    created_by: str
    created_at: str


class SubGroupEnrollRequest(BaseModel):
    """Enrol one group member; ``role`` is the optional
    per-sub-group role override (an organiser's grant)."""

    user_id: str
    role: str | None = None


class SubGroupMemberResponse(BaseModel):
    """One enrolled member: their group role, their per-sub-group
    role when one was granted, and the effective role."""

    user_id: str
    name: str = ""
    email: str = ""
    group_role: str | None = None
    subgroup_role: str | None = None
    effective_role: str | None = None


# ------------------------------------------------------------------
# Announcements (B-106)
# ------------------------------------------------------------------


class AnnouncementCreateRequest(BaseModel):
    """Post to the board. Times arrive as ISO-8601; the router
    converts them to stored UTC before the service runs."""

    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1)
    pinned: bool = False
    expires_at: datetime | None = None
    subgroup_id: str | None = None


class AnnouncementPatchRequest(BaseModel):
    """Edit a post; only sent fields change. An explicit
    ``expires_at: null`` un-expires the post."""

    title: str | None = Field(default=None, min_length=1, max_length=240)
    body: str | None = Field(default=None, min_length=1)
    pinned: bool | None = None
    expires_at: datetime | None = None
    subgroup_id: str | None = None


class AnnouncementResponse(BaseModel):
    """One board post. ``expired`` is computed at read time —
    expiry is a state the row grows into, never a deletion."""

    id: str
    group_id: str
    subgroup_id: str | None = None
    title: str
    body: str
    pinned: bool = False
    expires_at: str | None = None
    expired: bool = False
    created_by: str
    created_at: str
    updated_at: str


def announcement_view(row, *, expired: bool = False) -> AnnouncementResponse:
    """Build one announcement's response from its row."""
    return AnnouncementResponse(
        id=row.id,
        group_id=row.group_id,
        subgroup_id=row.subgroup_id,
        title=row.title,
        body=row.body,
        pinned=row.pinned,
        expires_at=row.expires_at.isoformat() + "Z" if row.expires_at else None,
        expired=expired,
        created_by=row.created_by,
        created_at=row.created_at.isoformat() + "Z",
        updated_at=row.updated_at.isoformat() + "Z",
    )


# ------------------------------------------------------------------
# Volunteer shifts (B-107)
# ------------------------------------------------------------------


class ShiftCreateRequest(BaseModel):
    """Open a shift on an event: one role, one window, a
    headcount. Times arrive as ISO-8601; the router converts
    them to stored UTC before the service runs."""

    role_name: str = Field(min_length=1, max_length=80)
    headcount: int = Field(default=1, ge=1, le=100)
    starts_at: datetime
    ends_at: datetime


class ShiftResponse(BaseModel):
    """One shift: the role, the window, how many people it
    needs, how many have claimed — and the slot rows themselves
    (claims and releases ride the B-088 slot endpoints)."""

    event_id: str
    role_name: str
    starts_at: str | None = None
    ends_at: str | None = None
    headcount: int
    claimed: int
    slots: list[RoleSlotResponse] = Field(default_factory=list)


def shift_view(group: dict) -> ShiftResponse:
    """Build one shift's response from the service's group dict."""
    return ShiftResponse(
        event_id=group["event_id"],
        role_name=group["role_name"],
        starts_at=group["starts_at"].isoformat() + "Z" if group["starts_at"] else None,
        ends_at=group["ends_at"].isoformat() + "Z" if group["ends_at"] else None,
        headcount=group["headcount"],
        claimed=group["claimed"],
        slots=[role_slot_view(slot) for slot in group["slots"]],
    )


class MissingRole(BaseModel):
    """One role an event still needs people for."""

    role_name: str
    count: int


class ShiftGapResponse(BaseModel):
    """One event's gaps, with the summary in words a coordinator
    can read aloud: "Saturday still needs a driver and a door
    person.\""""

    event_id: str
    event_title: str
    starts_at: str
    missing: list[MissingRole] = Field(default_factory=list)
    summary: str


class ShiftGapReportResponse(BaseModel):
    """The window's gap report: only events WITH gaps appear."""

    events: list[ShiftGapResponse] = Field(default_factory=list)


def shift_gap_view(row: dict) -> ShiftGapResponse:
    """Build one gap-report line from the service's row dict."""
    return ShiftGapResponse(
        event_id=row["event_id"],
        event_title=row["event_title"],
        starts_at=row["starts_at"].isoformat() + "Z",
        missing=[MissingRole(**entry) for entry in row["missing"]],
        summary=row["summary"],
    )


# ------------------------------------------------------------------
# Resources + bookings (B-108)
# ------------------------------------------------------------------


class ResourceCreateRequest(BaseModel):
    """Register a bookable item, with its keeper: a member's id
    (their words become the label), or plain words."""

    name: str = Field(min_length=1, max_length=160)
    notes: str = ""
    keeper_user_id: str | None = None
    keeper_label: str = ""


class ResourcePatchRequest(BaseModel):
    """Edit an item; only sent fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str | None = None
    keeper_user_id: str | None = None
    keeper_label: str | None = None


class ResourceResponse(BaseModel):
    """One bookable item and its keeper."""

    id: str
    group_id: str
    name: str
    notes: str = ""
    keeper_user_id: str | None = None
    keeper_label: str = ""
    created_by: str
    created_at: str
    updated_at: str


def resource_view(row) -> ResourceResponse:
    """Build one resource's response from its row."""
    return ResourceResponse(
        id=row.id,
        group_id=row.group_id,
        name=row.name,
        notes=row.notes,
        keeper_user_id=row.keeper_user_id,
        keeper_label=row.keeper_label,
        created_by=row.created_by,
        created_at=row.created_at.isoformat() + "Z",
        updated_at=row.updated_at.isoformat() + "Z",
    )


class ResourceConflictFlag(BaseModel):
    """One double-booking a booking takes part in (B-057's
    shape, on the booking that created it)."""

    kind: str = "resource_overlap"
    resource_id: str
    resource_name: str
    other_event_id: str
    other_event_title: str
    message: str


class ResourceBookingCreateRequest(BaseModel):
    """Book one resource for the event in the path."""

    resource_id: str


class ResourceBookingResponse(BaseModel):
    """One booking. ``conflicts`` carries the double-bookings
    this booking takes part in — the booker sees the clash in
    the response that makes it (flags, never blocks)."""

    id: str
    group_id: str
    resource_id: str
    resource_name: str = ""
    event_id: str
    booked_by: str
    created_at: str
    conflicts: list[ResourceConflictFlag] = Field(default_factory=list)


def resource_booking_view(booking, resource_name: str = "", conflicts=()) -> ResourceBookingResponse:
    """Build one booking's response from its row."""
    return ResourceBookingResponse(
        id=booking.id,
        group_id=booking.group_id,
        resource_id=booking.resource_id,
        resource_name=resource_name,
        event_id=booking.event_id,
        booked_by=booking.booked_by,
        created_at=booking.created_at.isoformat() + "Z",
        conflicts=[ResourceConflictFlag(**flag) for flag in conflicts],
    )


# ------------------------------------------------------------------
# Member import/export (B-110)
# ------------------------------------------------------------------


class ImportRowIssue(BaseModel):
    """One row of the spreadsheet, named by its row number,
    with the plain reason it was skipped or failed (B-110)."""

    row: int
    reason: str


class MemberImportReportResponse(BaseModel):
    """What an import did — or, on a dry run, exactly what it
    WOULD do (the report is computed, then rolled back)."""

    dry_run: bool
    rows_total: int
    created_users: int
    memberships_created: int
    enrolments: int
    already_members: int
    blank_rows: int
    subgroups_created: list[str]
    skipped: list[ImportRowIssue]
    errors: list[ImportRowIssue]


class MemberExportRow(BaseModel):
    """One exported membership: the roster row, with the
    person's sub-group names (B-110)."""

    name: str
    email: str
    role: str
    status: str
    subgroups: list[str]


# ------------------------------------------------------------------
# Group settings pack (B-111)
# ------------------------------------------------------------------


class GroupSettingsResponse(BaseModel):
    """The settings pack as read (B-111): the review-queue
    rules, reminder defaults, the digest's moment, the
    template set, and the AI budget cap (null = no cap)."""

    review_min_role: str
    review_approvals_required: int
    reminder_rules: list[ReminderRule]
    digest_weekday: int | None
    digest_time: str
    digest_enabled: bool
    template_set: str
    ai_daily_budget_micros: int | None
    # Phase 10 memory dials (B-114/B-119).
    prune_max_passes: int
    prune_token_budget: int
    prune_driver: str
    # Phase 13 dials (B-141/B-144): storage quota + retention
    # window; null = no quota / keep sources forever.
    upload_quota_bytes: int | None
    source_retention_days: int | None


class GroupSettingsUpdateRequest(BaseModel):
    """A settings-pack write. Every field is optional: a field
    that is ABSENT is left as it was; a field that is present
    governs — including null where null means something
    (budget → no cap; digest weekday → the default)."""

    review_min_role: str | None = Field(
        default=None, pattern="^(member|organiser|admin|owner)$"
    )
    review_approvals_required: int | None = Field(default=None, ge=1, le=3)
    reminder_rules: list[ReminderRule] | None = None
    digest_weekday: int | None = Field(default=None, ge=0, le=6)
    digest_time: str | None = Field(default=None, pattern=r"^(\d{2}:\d{2})?$")
    digest_enabled: bool | None = None
    template_set: str | None = Field(default=None, max_length=32)
    ai_daily_budget_micros: int | None = Field(default=None, ge=0)
    # The pass cap's le=3 is the engine's hard ceiling restated
    # at the door (B-114); the service validates again.
    prune_max_passes: int | None = Field(default=None, ge=1, le=3)
    prune_token_budget: int | None = Field(default=None, ge=0, le=10_000_000)
    prune_driver: str | None = Field(
        default=None, pattern="^(hosted|local)$"
    )
    # Phase 13 dials. Present governs, including null (null
    # lifts the quota / keeps sources forever); the service
    # validates the ranges in plain words.
    upload_quota_bytes: int | None = Field(default=None, ge=0)
    source_retention_days: int | None = Field(default=None, ge=1, le=3650)


# ------------------------------------------------------------------
# Attendance (B-109) — aggregates only, by design
# ------------------------------------------------------------------


class AttendanceMarkRequest(BaseModel):
    """Mark one member present at the event in the path."""

    user_id: str


class AttendanceMarkResponse(BaseModel):
    """The mark's own receipt — the ONLY response in this area
    that names a person, because it echoes the organiser's act
    itself. There is no list shape beside it, deliberately."""

    event_id: str
    user_id: str
    marked_by: str
    marked_at: str


class EventAttendanceResponse(BaseModel):
    """One event's attendance, as a count and nothing else."""

    event_id: str
    attendee_count: int


class AttendanceEventSummary(BaseModel):
    """One recent event in the planning summary: the event and
    its headcount. No people."""

    event_id: str
    title: str
    starts_at: str
    attendee_count: int


class AttendanceSummaryResponse(BaseModel):
    """"Last three club nights drew ~20", as data: recent
    events, their headcounts, and the average."""

    events_considered: int
    average_attendance: float
    events: list[AttendanceEventSummary] = Field(default_factory=list)


def subgroup_view(subgroup, member_count: int = 0) -> SubGroupResponse:
    """Build one sub-group's response from its row (the router
    supplies the enrolled headcount — views never query)."""
    return SubGroupResponse(
        id=subgroup.id,
        group_id=subgroup.group_id,
        name=subgroup.name,
        description=subgroup.description,
        member_count=member_count,
        created_by=subgroup.created_by,
        created_at=subgroup.created_at.isoformat() + "Z",
    )
