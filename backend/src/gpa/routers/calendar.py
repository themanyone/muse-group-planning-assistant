"""Calendar routes (roadmap B-053 → B-059).

The group's time, read every way the frontend needs — with the
server doing all the date maths (B-055). This file holds:

- the per-event ``.ics`` download and the per-group secret feed
  (issue / list / revoke / the unauthenticated fetch the token
  itself authorises) — B-053;
- agenda, month grid, search + filters — B-055;
- the Saturday-night ``tonight`` view — B-056;
- conflict flags — B-057;
- the public projection routes — B-059.

Drafts reach NONE of these surfaces (B-049's bright line): the
selectors filter to published + cancelled, and cancelled events
are shown/emitted AS cancelled, never vanished.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.schemas.events import EventResponse, OccurrenceResponse, iso_utc, project_event
from gpa.services import events as event_service
from gpa.services import ical as ical_service
from gpa.services import recurrence as recurrence_service

router = APIRouter(prefix="/api/v1/groups", tags=["calendar"])
public_router = APIRouter(prefix="/api/v1", tags=["calendar"])
embed_router = APIRouter(prefix="/api/v1", tags=["calendar"])


class PublicPageResponse(BaseModel):
    """The public page payload (B-135): a public-listed
    group's face for its own website and for embeds — the
    group's public facts and its events at the PUBLIC
    projection (B-059). Nothing member-only exists here."""

    slug: str
    name: str
    description: str = ""
    events: list[EventResponse] = Field(default_factory=list)


class FeedIssueRequest(BaseModel):
    """Issue a feed, optionally narrowed: statuses (published /
    cancelled), a place word, a sub-group. The filter is part of
    the issued feed — a leaked URL leaks only its own scope."""

    filters: dict = Field(default_factory=dict)


class FeedResponse(BaseModel):
    """A feed as its group sees it: prefix only, never the token."""

    id: str
    token_prefix: str
    filters: dict = Field(default_factory=dict)
    revoked_at: str | None = None
    created_at: str


class FeedIssueResponse(FeedResponse):
    """The issuing response — the ONE time the raw token and its
    ready-to-paste URLs exist (B-053)."""

    token: str
    feed_url: str
    webcal_url: str


def _feed_row(feed) -> FeedResponse:
    import json

    try:
        filters = json.loads(feed.filters_json or "{}")
    except json.JSONDecodeError:
        filters = {}
    return FeedResponse(
        id=feed.id,
        token_prefix=feed.token_prefix,
        filters=filters,
        revoked_at=iso_utc(feed.revoked_at),
        created_at=iso_utc(feed.created_at),
    )


@router.get("/{group_id}/events/{event_id}/ics")
def download_event_ics(
    event_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """One event as a downloadable .ics (B-053). Drafts refuse —
    a file download is a publishing-out road, and the bright
    line holds for every role here."""
    event = event_service.get_event(db, context.group, event_id)
    if event.status == "draft":
        raise AppError("not_found", detail="That event could not be found.")
    recurrence = recurrence_service.get_recurrence(db, event.id)
    body = ical_service.render_event_ics(event, recurrence)
    return PlainTextResponse(
        body,
        media_type="text/calendar",
        headers={
            "Content-Disposition": f'attachment; filename="event-{event.id}.ics"'
        },
    )


@router.post(
    "/{group_id}/calendar/feeds", response_model=FeedIssueResponse, status_code=201
)
def issue_feed(
    group_id: str,
    body: FeedIssueRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Issue the group's secret calendar feed (B-053): the token
    is shown once, in this response, and stored only hashed."""
    feed, raw = ical_service.issue_feed(
        db, actor_label=principal.label, group=context.group, filters=body.filters
    )
    base = str(request.base_url).rstrip("/")
    feed_url = ical_service.feed_url_for(base, raw)
    row = _feed_row(feed)
    return FeedIssueResponse(
        **row.model_dump(), token=raw, feed_url=feed_url,
        webcal_url="webcal://" + feed_url.split("://", 1)[-1],
    )


@router.get("/{group_id}/calendar/feeds", response_model=list[FeedResponse])
def list_feeds(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """The group's feeds, prefixes only (B-053)."""
    return [_feed_row(feed) for feed in ical_service.list_feeds(db, context.group)]


@router.post("/{group_id}/calendar/feeds/{feed_id}/revoke", response_model=FeedResponse)
def revoke_feed(
    feed_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Revoke a feed — instant and total (B-053)."""
    feed = ical_service.revoke_feed(
        db, actor_label=principal.label, group=context.group, feed_id=feed_id
    )
    return _feed_row(feed)


@public_router.get("/calendar/feed/{token}.ics")
def fetch_feed(token: str, db: Session = Depends(get_db)):
    """The feed itself. No login: the token IS the credential,
    exactly like every calendar subscription URL a person pastes
    into Google/Apple/Outlook. Unknown and revoked tokens get
    the same 404."""
    feed = ical_service.resolve_feed(db, token)
    if feed is None:
        raise AppError("not_found", detail="That calendar feed could not be found.")
    group = db.get(Group, feed.group_id)
    body = ical_service.render_feed(db, feed, group)
    return PlainTextResponse(body, media_type="text/calendar")


# ------------------------------------------------------------------
# Agenda / month / tonight / conflicts (B-055, B-056, B-057)
# ------------------------------------------------------------------


class AgendaResponse(BaseModel):
    """One agenda page: occurrences + the next page's cursor."""

    items: list[OccurrenceResponse]
    next_cursor: str | None = None


class MonthDay(BaseModel):
    date: str
    in_month: bool
    occurrences: list[OccurrenceResponse] = Field(default_factory=list)


class MonthResponse(BaseModel):
    year: int
    month: int
    timezone: str
    weeks: list[list[MonthDay]]


class ConflictFlag(BaseModel):
    kind: str  # host_overlap | place_overlap | travel_impossible | resource_overlap
    event_id: str
    occurrence_start: str
    other_event_id: str
    other_occurrence_start: str
    message: str
    distance_km: float | None = None
    needed_minutes: int | None = None
    available_minutes: int | None = None
    # Resource flags (B-108) name the double-booked item.
    resource_id: str | None = None
    resource_name: str | None = None


class ConflictsResponse(BaseModel):
    flags: list[ConflictFlag]
    # Resource double-booking (B-108) rides the same flags:
    # kind "resource_overlap", with the item named on the flag.
    resource_note: str = (
        "Resource double-bookings appear above as resource_overlap "
        "flags when two published events hold the same resource "
        "over overlapping times."
    )


def _window(from_: str | None, to: str | None, *, default_days: int = 31):
    """Parse the from/to window (ISO datetimes) with a sane
    default: from now, for ``default_days`` days."""
    from datetime import datetime, timedelta, timezone

    from gpa.schemas.events import to_utc_naive

    def parse(value: str | None):
        if not value:
            return None
        try:
            return to_utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            raise AppError("validation_error", detail="from/to must be ISO datetimes.")

    start = parse(from_) or datetime.now(timezone.utc).replace(tzinfo=None)
    end = parse(to) or start + timedelta(days=default_days)
    if end <= start:
        raise AppError("validation_error", detail="The window must end after it starts.")
    return start, end


@router.get("/{group_id}/agenda", response_model=AgendaResponse)
def agenda(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    q: str | None = Query(default=None),
    type_: str | None = Query(default=None, alias="type"),
    place: str | None = Query(default=None),
    sub_group: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The agenda (B-055): occurrence-expanded, searchable,
    filterable, cursor-paginated — the frontend does zero date
    maths of its own, here and everywhere in this file."""
    from gpa.services import calendar_views

    start, end = _window(from_, to)
    items, next_cursor = calendar_views.agenda(
        db, context.group, window_start=start, window_end=end,
        q=q, event_type=type_, place=place, sub_group=sub_group,
        cursor=cursor, limit=limit,
        subgroup_scope=_viewer_scope(db, context), scope_restricted=True,
    )
    return AgendaResponse(items=items, next_cursor=next_cursor)


def _viewer_scope(db: Session, context: GroupContext) -> set[str] | None:
    """The caller's visible sub-group set (B-105): None for
    organisers and above, else exactly their enrolments."""
    from gpa.services import subgroups as subgroup_service

    return subgroup_service.visible_subgroup_ids(
        db, context.group,
        user_id=context.principal.user_id, role=context.role,
    )


@router.get("/{group_id}/month", response_model=MonthResponse)
def month(
    year: int = Query(ge=1970, le=2100),
    month: int = Query(ge=1, le=12),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """The month grid (B-055): whole weeks, days marked
    in/out of month, occurrences already placed."""
    from gpa.services import calendar_views

    return calendar_views.month_grid(
        db, context.group, year=year, month=month,
        subgroup_scope=_viewer_scope(db, context), scope_restricted=True,
    )


@router.get("/{group_id}/tonight", response_model=list[OccurrenceResponse])
def tonight(
    date: str | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Saturday night (B-056): this evening's events in the
    group's own clock — pinned places first, flyer events with
    their pictures and alt text. ``date`` (a local date) pins
    the evening for planning ahead and for tests."""
    from datetime import date as date_type, datetime, timezone

    from gpa.services import calendar_views

    on_date = None
    if date:
        try:
            on_date = date_type.fromisoformat(date)
        except ValueError:
            raise AppError("validation_error", detail="date must be YYYY-MM-DD.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return calendar_views.tonight(
        db, context.group, now_utc=now, on_date=on_date,
        subgroup_scope=_viewer_scope(db, context), scope_restricted=True,
    )


@router.get("/{group_id}/conflicts", response_model=ConflictsResponse)
def conflicts(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Conflict flags over a window (B-057): double-booked
    hosts and rooms, travel-impossible hops. Flags, never
    blocks — reading them changes nothing."""
    from gpa.services import calendar_views

    start, end = _window(from_, to)
    flags = calendar_views.conflicts(
        db, context.group, window_start=start, window_end=end,
        subgroup_scope=_viewer_scope(db, context), scope_restricted=True,
    )
    return ConflictsResponse(flags=flags)


# ------------------------------------------------------------------
# Public projections (B-059)
# ------------------------------------------------------------------


@public_router.get(
    "/public/groups/{slug}/events", response_model=list[EventResponse]
)
def public_events(slug: str, db: Session = Depends(get_db)):
    """A public-listed group's events, PUBLIC projection only:
    no host, no cost, no access notes, no discussion — those
    fields do not exist in this response (B-059). A group that
    is not public-listed is a 404 here, exactly as if it did
    not exist."""
    group = _public_group(db, slug)
    return _public_event_rows(db, group)


def _public_group(db: Session, slug: str) -> Group:
    """The public-listed group at a slug, or the public 404 —
    a group that is not public-listed does not exist here."""
    group = db.query(Group).filter(Group.slug == slug).one_or_none()
    if group is None or group.visibility != "public-listed":
        raise AppError("not_found", detail="That group could not be found.")
    return group


def _public_event_rows(db: Session, group: Group) -> list[EventResponse]:
    rows = event_service.list_events(db, group, role="member")
    # The flyer face rides the public projection too (Phase 5):
    # the picture and its alt-text state are public facts, and a
    # generated image's flag must survive even here (B-067).
    from gpa.services import calendar_views

    return [
        project_event(event, "public", flyer=calendar_views.flyer_view(db, event))
        for event in rows
    ]


def _public_page(db: Session, slug: str) -> PublicPageResponse:
    group = _public_group(db, slug)
    return PublicPageResponse(
        slug=group.slug,
        name=group.name,
        description=group.description or "",
        events=_public_event_rows(db, group),
    )


@public_router.get(
    "/public/groups/{slug}", response_model=PublicPageResponse
)
def public_page(slug: str, db: Session = Depends(get_db)):
    """The group's public page data (B-135): its public facts
    and public events in one payload, for its own website.
    CORS answers are made for registered embed origins only
    (the middleware's rule); the data itself is public."""
    return _public_page(db, slug)


@embed_router.get(
    "/embed/groups/{slug}", response_model=PublicPageResponse
)
def embed_page(
    slug: str, request: Request, db: Session = Depends(get_db)
):
    """The widget door (B-135): the same public page data,
    served to EMBEDS. A browser embed always carries an
    Origin header, and the origin must be on THIS group's
    register — **an unregistered origin gets nothing** (403,
    and no CORS answer from the middleware either). A
    request with no Origin header is not a browser embed
    (server-to-server, a feed reader) and is served like the
    public page."""
    group = _public_group(db, slug)
    origin = request.headers.get("origin")
    if origin:
        from gpa.services import embed as embed_service

        if not embed_service.group_allows_origin(group, origin):
            raise AppError(
                "forbidden",
                detail=(
                    "This origin is not registered to embed this "
                    "group's page. An organiser can add it under "
                    "the group's embed settings."
                ),
            )
    page = _public_page(db, slug)
    return page
