"""Nonprofit-pack routers (roadmap Phase 9).

Sub-groups, the announcements board, volunteer shifts,
resources, attendance, member import/export, and the group
settings pack. Every route takes its caller from deps.py — the
one door (B-017) — and the permission each route needs is named
in its dependency, never hand-rolled in the handler.
"""

from fastapi import APIRouter, Depends, Query
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
    ShiftCreateRequest,
    ShiftGapReportResponse,
    ShiftResponse,
    SubGroupCreateRequest,
    SubGroupEnrollRequest,
    SubGroupMemberResponse,
    SubGroupPatchRequest,
    SubGroupResponse,
    announcement_view,
    shift_gap_view,
    shift_view,
    subgroup_view,
)
from gpa.services import announcements as announcement_service
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
