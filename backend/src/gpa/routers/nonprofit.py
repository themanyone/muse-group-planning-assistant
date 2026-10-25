"""Nonprofit-pack routers (roadmap Phase 9).

Sub-groups, the announcements board, volunteer shifts,
resources, attendance, member import/export, and the group
settings pack. Every route takes its caller from deps.py — the
one door (B-017) — and the permission each route needs is named
in its dependency, never hand-rolled in the handler.
"""

from fastapi import APIRouter, Body, Depends, Query, Response
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import (
    GroupContext,
    Principal,
    get_principal,
    require_group_role,
    require_scope,
)
from gpa.schemas.nonprofit import (
    AnnouncementCreateRequest,
    AnnouncementPatchRequest,
    AnnouncementResponse,
    AttendanceMarkRequest,
    AttendanceMarkResponse,
    AttendanceSummaryResponse,
    EventAttendanceResponse,
    GroupSettingsResponse,
    GroupSettingsUpdateRequest,
    MemberExportRow,
    MemberImportReportResponse,
    ResourceBookingCreateRequest,
    ResourceBookingResponse,
    ResourceCreateRequest,
    ResourcePatchRequest,
    ResourceResponse,
    ShiftCreateRequest,
    ShiftGapReportResponse,
    ShiftResponse,
    SubGroupCreateRequest,
    SubGroupEnrollRequest,
    SubGroupMemberResponse,
    SubGroupPatchRequest,
    SubGroupResponse,
    announcement_view,
    resource_booking_view,
    resource_view,
    shift_gap_view,
    shift_view,
    subgroup_view,
)
from gpa.services import announcements as announcement_service
from gpa.services import attendance as attendance_service
from gpa.services import member_io as member_io_service
from gpa.services import settings_pack as settings_pack_service
from gpa.services import resources as resource_service
from gpa.services import shifts as shift_service
from gpa.services import subgroups as subgroup_service

subgroups_router = APIRouter(prefix="/api/v1/groups", tags=["subgroups"])


@subgroups_router.post(
    "/{group_id}/subgroups", response_model=SubGroupResponse, status_code=201
)
def create_subgroup(
    body: SubGroupCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Open a named sub-group (organiser+)."""
    subgroup = subgroup_service.create_subgroup(
        db, actor_label=principal.label, group=context.group,
        name=body.name, description=body.description,
    )
    return subgroup_view(subgroup, subgroup_service.member_count(db, subgroup))


@subgroups_router.get("/{group_id}/subgroups", response_model=list[SubGroupResponse])
def list_subgroups(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The group's sub-groups, name order, with headcounts."""
    return [
        subgroup_view(row, subgroup_service.member_count(db, row))
        for row in subgroup_service.list_subgroups(db, context.group)
    ]


@subgroups_router.get(
    "/{group_id}/subgroups/{subgroup_id}", response_model=SubGroupResponse
)
def read_subgroup(
    subgroup_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """One sub-group."""
    subgroup = subgroup_service.get_subgroup(db, context.group, subgroup_id)
    return subgroup_view(subgroup, subgroup_service.member_count(db, subgroup))


@subgroups_router.patch(
    "/{group_id}/subgroups/{subgroup_id}", response_model=SubGroupResponse
)
def update_subgroup(
    subgroup_id: str,
    body: SubGroupPatchRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Rename / re-describe a sub-group (organiser+)."""
    subgroup = subgroup_service.update_subgroup(
        db, actor_label=principal.label, group=context.group,
        subgroup_id=subgroup_id, changes=body.model_dump(exclude_unset=True),
    )
    return subgroup_view(subgroup, subgroup_service.member_count(db, subgroup))


@subgroups_router.delete("/{group_id}/subgroups/{subgroup_id}", status_code=204)
def delete_subgroup(
    subgroup_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Remove a sub-group (organiser+). Refused while content is
    still scoped to it — a delete must never widen an audience."""
    subgroup_service.delete_subgroup(
        db, actor_label=principal.label, group=context.group,
        subgroup_id=subgroup_id,
    )


@subgroups_router.post(
    "/{group_id}/subgroups/{subgroup_id}/members",
    response_model=SubGroupMemberResponse,
    status_code=201,
)
def enroll_member(
    subgroup_id: str,
    body: SubGroupEnrollRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Enrol a group member in a sub-group. Organisers enrol
    anyone; a member may enrol THEMSELVES (the service holds
    that rule, so no route can forget it)."""
    subgroup_service.enroll_member(
        db, actor_label=principal.label, actor_user_id=principal.user_id,
        actor_role=context.role, group=context.group,
        subgroup_id=subgroup_id, user_id=body.user_id, role=body.role,
    )
    subgroup = subgroup_service.get_subgroup(db, context.group, subgroup_id)
    for member in subgroup_service.list_members(db, context.group, subgroup):
        if member["user_id"] == body.user_id:
            return SubGroupMemberResponse(**member)
    raise AssertionError("enrolled member missing from the member list")


@subgroups_router.get(
    "/{group_id}/subgroups/{subgroup_id}/members",
    response_model=list[SubGroupMemberResponse],
)
def list_subgroup_members(
    subgroup_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Who is enrolled, with group / per-sub-group / effective roles."""
    subgroup = subgroup_service.get_subgroup(db, context.group, subgroup_id)
    return [
        SubGroupMemberResponse(**member)
        for member in subgroup_service.list_members(db, context.group, subgroup)
    ]


# ------------------------------------------------------------------
# Announcements board (B-106)
# ------------------------------------------------------------------

announcements_router = APIRouter(prefix="/api/v1/groups", tags=["announcements"])


def _viewer_ids(db: Session, context: GroupContext):
    """The caller's visible sub-group set (B-105's rule)."""
    return subgroup_service.visible_subgroup_ids(
        db, context.group,
        user_id=context.principal.user_id, role=context.role,
    )


@announcements_router.post(
    "/{group_id}/announcements", response_model=AnnouncementResponse, status_code=201
)
def create_announcement(
    body: AnnouncementCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Post to the board (organiser+). Announcement ≠
    publication: it still takes an organiser ACCOUNT — a seat
    is refused in the service (the B-043 law for humans)."""
    from gpa.schemas.events import to_utc_naive

    row = announcement_service.create_announcement(
        db, actor_label=principal.label, actor_user_id=principal.user_id,
        actor_is_seat=principal.is_seat, group=context.group,
        title=body.title, body=body.body, pinned=body.pinned,
        expires_at=to_utc_naive(body.expires_at) if body.expires_at else None,
        subgroup_id=body.subgroup_id,
    )
    return announcement_view(row, expired=announcement_service.is_expired(row))


@announcements_router.get(
    "/{group_id}/announcements", response_model=list[AnnouncementResponse]
)
def list_announcements(
    include_expired: bool = Query(default=False),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The board: pinned first, then newest. Expired posts are
    gone from the board unless an organiser asks to see them."""
    from gpa.models.groups import role_at_least

    honoured = include_expired and (
        context.role is not None and role_at_least(context.role, "organiser")
    )
    rows = announcement_service.list_announcements(
        db, context.group,
        visible_ids=_viewer_ids(db, context),
        include_expired=honoured,
    )
    return [
        announcement_view(row, expired=announcement_service.is_expired(row))
        for row in rows
    ]


@announcements_router.get(
    "/{group_id}/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
)
def read_announcement(
    announcement_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """One post. A scoped post outside the caller's sub-groups
    is a 404, like every scoped thing (B-105)."""
    from gpa.errors import AppError

    row = announcement_service.get_announcement(db, context.group, announcement_id)
    if not subgroup_service.can_see_scoped(row.subgroup_id, _viewer_ids(db, context)):
        raise AppError("not_found", detail="That announcement could not be found.")
    return announcement_view(row, expired=announcement_service.is_expired(row))


@announcements_router.patch(
    "/{group_id}/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
)
def update_announcement(
    announcement_id: str,
    body: AnnouncementPatchRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Edit a post (organiser+ person — seats refused inside)."""
    from gpa.schemas.events import to_utc_naive

    changes = body.model_dump(exclude_unset=True)
    if changes.get("expires_at") is not None:
        changes["expires_at"] = to_utc_naive(changes["expires_at"])
    row = announcement_service.update_announcement(
        db, actor_label=principal.label, actor_is_seat=principal.is_seat,
        group=context.group, announcement_id=announcement_id, changes=changes,
    )
    return announcement_view(row, expired=announcement_service.is_expired(row))


@announcements_router.delete(
    "/{group_id}/announcements/{announcement_id}", status_code=204
)
def delete_announcement(
    announcement_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Take a post down (organiser+ person)."""
    announcement_service.delete_announcement(
        db, actor_label=principal.label, actor_is_seat=principal.is_seat,
        group=context.group, announcement_id=announcement_id,
    )


# ------------------------------------------------------------------
# Volunteer shifts (B-107)
# ------------------------------------------------------------------

shifts_router = APIRouter(prefix="/api/v1/groups", tags=["shifts"])


@shifts_router.post(
    "/{group_id}/events/{event_id}/shifts",
    response_model=ShiftResponse,
    status_code=201,
)
def open_shift(
    event_id: str,
    body: ShiftCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Open a shift on an event (member+): headcount slots of
    one role over one window. Claims ride the B-088 endpoints."""
    from gpa.schemas.events import to_utc_naive

    group = shift_service.open_shift(
        db, actor_label=principal.label, group=context.group,
        viewer_role=context.role, viewer_user_id=principal.user_id,
        event_id=event_id, role_name=body.role_name,
        headcount=body.headcount,
        starts_at=to_utc_naive(body.starts_at),
        ends_at=to_utc_naive(body.ends_at),
    )
    return shift_view(group)


@shifts_router.get(
    "/{group_id}/events/{event_id}/shifts", response_model=list[ShiftResponse]
)
def list_shifts(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """An event's shifts, grouped by role + window."""
    from gpa.services import events as event_service

    event = event_service.get_visible_event(
        db, context.group, event_id, context.role,
        viewer_user_id=principal.user_id,
    )
    return [shift_view(group) for group in shift_service.shift_groups(db, event)]


@shifts_router.get("/{group_id}/shift-gaps", response_model=ShiftGapReportResponse)
def shift_gaps(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The gap report: which events in the window still need
    which roles — "Saturday still needs a driver and a door
    person." Without a window, the group's coming week."""
    from datetime import datetime, timedelta, timezone

    def parse(value: str | None):
        if not value:
            return None
        try:
            from gpa.schemas.events import to_utc_naive

            return to_utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            from gpa.errors import AppError

            raise AppError("validation_error", detail="from/to must be ISO datetimes.")

    start = parse(from_) or datetime.now(timezone.utc).replace(tzinfo=None)
    end = parse(to) or start + timedelta(days=7)
    rows = shift_service.gap_report(db, context.group, start=start, end=end)
    return ShiftGapReportResponse(events=[shift_gap_view(row) for row in rows])


# ------------------------------------------------------------------
# Resources + bookings (B-108)
# ------------------------------------------------------------------

resources_router = APIRouter(prefix="/api/v1/groups", tags=["resources"])


@resources_router.post(
    "/{group_id}/resources", response_model=ResourceResponse, status_code=201
)
def create_resource(
    body: ResourceCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Register a bookable item (organiser+)."""
    row = resource_service.create_resource(
        db, actor_label=principal.label, group=context.group,
        name=body.name, notes=body.notes,
        keeper_user_id=body.keeper_user_id, keeper_label=body.keeper_label,
    )
    return resource_view(row)


@resources_router.get("/{group_id}/resources", response_model=list[ResourceResponse])
def list_resources(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The group's bookable items, name order."""
    return [
        resource_view(row)
        for row in resource_service.list_resources(db, context.group)
    ]


@resources_router.patch(
    "/{group_id}/resources/{resource_id}", response_model=ResourceResponse
)
def update_resource(
    resource_id: str,
    body: ResourcePatchRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Edit an item (organiser+)."""
    row = resource_service.update_resource(
        db, actor_label=principal.label, group=context.group,
        resource_id=resource_id, changes=body.model_dump(exclude_unset=True),
    )
    return resource_view(row)


@resources_router.delete("/{group_id}/resources/{resource_id}", status_code=204)
def delete_resource(
    resource_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Remove an item and its bookings (organiser+)."""
    resource_service.delete_resource(
        db, actor_label=principal.label, group=context.group,
        resource_id=resource_id,
    )


@resources_router.post(
    "/{group_id}/events/{event_id}/resource-bookings",
    response_model=ResourceBookingResponse,
    status_code=201,
)
def book_resource(
    event_id: str,
    body: ResourceBookingCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Book a resource for an event (organiser+). The response
    carries any double-booking this creates — flagged, never
    blocked (B-057's law, completed by B-108)."""
    booking, conflicts = resource_service.book_resource(
        db, actor_label=principal.label, group=context.group,
        viewer_role=context.role, viewer_user_id=principal.user_id,
        event_id=event_id, resource_id=body.resource_id,
    )
    resource = resource_service.get_resource(db, context.group, booking.resource_id)
    return resource_booking_view(booking, resource.name, conflicts)


@resources_router.get(
    "/{group_id}/events/{event_id}/resource-bookings",
    response_model=list[ResourceBookingResponse],
)
def list_event_bookings(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """What an event has booked."""
    from gpa.services import events as event_service

    event = event_service.get_visible_event(
        db, context.group, event_id, context.role,
        viewer_user_id=principal.user_id,
    )
    return [
        resource_booking_view(booking, resource.name)
        for booking, resource in resource_service.list_bookings_for_event(
            db, context.group, event
        )
    ]


@resources_router.delete(
    "/{group_id}/resource-bookings/{booking_id}", status_code=204
)
def unbook_resource(
    booking_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Release one booking (organiser+)."""
    resource_service.unbook(
        db, actor_label=principal.label, group=context.group,
        booking_id=booking_id,
    )


# ------------------------------------------------------------------
# Attendance (B-109) — aggregates only, by design
# ------------------------------------------------------------------

attendance_router = APIRouter(prefix="/api/v1/groups", tags=["attendance"])


@attendance_router.post(
    "/{group_id}/events/{event_id}/attendance",
    response_model=AttendanceMarkResponse,
    status_code=201,
)
def mark_attendance(
    event_id: str,
    body: AttendanceMarkRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Mark a member present (organiser+). Idempotent — the
    receipt is the act's own echo, not a read of anyone's
    record."""
    row = attendance_service.mark_attendance(
        db, actor_label=principal.label, group=context.group,
        viewer_role=context.role, viewer_user_id=principal.user_id,
        event_id=event_id, user_id=body.user_id,
    )
    return AttendanceMarkResponse(
        event_id=row.event_id, user_id=row.user_id,
        marked_by=row.marked_by, marked_at=row.created_at.isoformat() + "Z",
    )


@attendance_router.delete(
    "/{group_id}/events/{event_id}/attendance/{user_id}", status_code=204
)
def unmark_attendance(
    event_id: str,
    user_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Correct a mark (organiser+)."""
    attendance_service.unmark_attendance(
        db, actor_label=principal.label, group=context.group,
        event_id=event_id, user_id=user_id,
    )


@attendance_router.get(
    "/{group_id}/events/{event_id}/attendance",
    response_model=EventAttendanceResponse,
)
def read_event_attendance(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """One event's headcount. A count — there is no per-person
    read here, deliberately (B-109's refusal)."""
    from gpa.services import events as event_service

    event = event_service.get_visible_event(
        db, context.group, event_id, context.role,
        viewer_user_id=principal.user_id,
    )
    return EventAttendanceResponse(
        event_id=event.id,
        attendee_count=attendance_service.event_headcount(db, event),
    )


@attendance_router.get(
    "/{group_id}/attendance/summary", response_model=AttendanceSummaryResponse
)
def attendance_summary(
    recent: int = Query(default=3, ge=1, le=52),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Recent events + headcounts + the average — the planning
    numbers, with no person's record anywhere in them."""
    summary = attendance_service.attendance_summary(
        db, context.group, recent=recent
    )
    return AttendanceSummaryResponse(
        events_considered=summary["events_considered"],
        average_attendance=summary["average_attendance"],
        events=[
            {
                "event_id": row["event_id"],
                "title": row["title"],
                "starts_at": row["starts_at"].isoformat() + "Z",
                "attendee_count": row["attendee_count"],
            }
            for row in summary["events"]
        ],
    )


@subgroups_router.delete(
    "/{group_id}/subgroups/{subgroup_id}/members/{user_id}", status_code=204
)
def unenroll_member(
    subgroup_id: str,
    user_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Remove an enrolment (organiser, or the person themselves)."""
    subgroup_service.unenroll_member(
        db, actor_label=principal.label, actor_user_id=principal.user_id,
        actor_role=context.role, group=context.group,
        subgroup_id=subgroup_id, user_id=user_id,
    )


members_io_router = APIRouter(prefix="/api/v1/groups", tags=["members-io"])


@members_io_router.post(
    "/{group_id}/members/import", response_model=MemberImportReportResponse
)
def import_members(
    csv_text: str = Body(media_type="text/plain"),
    dry_run: bool = Query(default=False),
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Import a membership spreadsheet (admin+): CSV of name,
    email, sub-group, posted as the raw body.

    The answer is a report — what was created, counted,
    skipped, and refused, row by spreadsheet row number. With
    ``?dry_run=true`` the same report is computed and the work
    rolled back: what WOULD happen, truthfully.
    """
    report = member_io_service.import_members(
        db, actor_label=principal.label, group=context.group,
        csv_text=csv_text, dry_run=dry_run,
    )
    return MemberImportReportResponse(**report)


@members_io_router.get(
    "/{group_id}/members/export", response_model=list[MemberExportRow]
)
def export_members(
    format: str = Query(default="json"),
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """The roster out (admin+): JSON by default, or
    ``?format=csv`` for the spreadsheet the group keeps."""
    if format == "csv":
        return Response(
            content=member_io_service.export_csv(db, context.group),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="members.csv"'
            },
        )
    return [
        MemberExportRow(**row)
        for row in member_io_service.export_rows(db, context.group)
    ]


settings_router = APIRouter(prefix="/api/v1/groups", tags=["settings"])


@settings_router.get(
    "/{group_id}/settings", response_model=GroupSettingsResponse
)
def read_settings(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """The group's settings pack (organiser+): review rules,
    reminder defaults, digest moment, template set, AI budget."""
    return GroupSettingsResponse(
        **settings_pack_service.settings_view(context.group)
    )


@settings_router.put(
    "/{group_id}/settings", response_model=GroupSettingsResponse
)
def update_settings(
    body: GroupSettingsUpdateRequest,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Write the settings pack (admin+). Only the fields sent
    are changed; each is validated whole, the change is
    audited with its old and new values, and a digest change
    re-anchors the digest chain before the answer is sent."""
    view = settings_pack_service.update_settings(
        db, actor_label=principal.label, group=context.group,
        changes=body.model_dump(exclude_unset=True),
    )
    return GroupSettingsResponse(**view)
