"""Calendar reads (roadmap B-055, B-056, B-057).

Every way the group reads its time, computed server-side so the
frontend does ZERO date maths: occurrences arrive expanded,
sorted, paginated, and rendered in the group's timezone.

- **Agenda** — a window of occurrences with search and filters,
  cursor-paginated where the window is open-ended.
- **Month** — a ready-made grid: whole weeks covering the
  month, each day carrying its occurrences. The client renders
  cells; it never works out which week a day belongs to.
- **Tonight** — Geoffrey's original pain as an endpoint: what's
  on this evening, the findable (geo-pinned) first, flyer-born
  events carrying their picture and its alt text.
- **Conflicts** — flags, never blocks: double-booked hosts and
  rooms, and travel-impossible sequences where the data allows
  the judgement (both places pinned). Resource double-booking
  waits for Phase 9's resource model — the honest boundary is
  stated on the response, not silently absent.
"""

import base64
import calendar as calendar_module
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.sources import Flyer
from gpa.schemas.events import FlyerView, occurrence_view
from gpa.services import recurrence as recurrence_service

# The evening, in the group's own clock: from mid-afternoon
# (doors-open planning time) through the small hours.
EVENING_START = time(15, 0)
EVENING_END = time(3, 0)  # of the following day

# The travel judgement's assumed road speed. Deliberately
# ordinary — flags exist to make a person look, not to prove a
# journey impossible to three decimal places.
TRAVEL_SPEED_KMH = 40.0

MAX_AGENDA_LIMIT = 200


def flyer_view(db: Session, event: Event) -> FlyerView | None:
    """An event's flyer face (image + alt text state), or None
    when the event has no flyer. The alt text rides along ALWAYS
    — an undescribed flyer is a visible defect (B-065's law),
    and the tonight view is where flyers are looked at most."""
    if not event.flyer_id:
        return None
    flyer = db.get(Flyer, event.flyer_id)
    if flyer is None:
        return None
    return FlyerView(
        flyer_id=flyer.id,
        thumbnail_key=flyer.thumbnail_key,
        alt_text=flyer.alt_text,
        alt_text_status=flyer.alt_text_status,
        generated=flyer.generated,
    )


def _live_events(db: Session, group: Group) -> list[Event]:
    """Published + cancelled — the only events any calendar
    surface may show (drafts are excluded at this one door)."""
    return (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.status.in_(("published", "cancelled")),
            # B-080: a deleted event is gone from every surface
            # until restored — cancelled shows AS cancelled, but
            # deleted does not show at all.
            Event.deleted_at.is_(None),
        )
        .order_by(Event.starts_at, Event.id)
        .all()
    )


def _matches(event: Event, *, q=None, event_type=None, place=None, sub_group=None) -> bool:
    """The B-055 filters, applied to the master event before
    expansion: a text search over title/place/host, and exact
    matches for the group's own type and sub-group vocabulary."""
    if q:
        needle = q.lower()
        haystacks = (event.title, event.place_name, event.host_label)
        if not any(needle in (text or "").lower() for text in haystacks):
            return False
    if event_type and event.event_type != event_type:
        return False
    if place and place.lower() not in event.place_name.lower():
        return False
    if sub_group and event.sub_group != sub_group:
        return False
    return True


def expand_group(
    db: Session,
    group: Group,
    *,
    window_start: datetime,
    window_end: datetime,
    q=None,
    event_type=None,
    place=None,
    sub_group=None,
    subgroup_scope: set[str] | None = None,
    scope_restricted: bool = False,
) -> list[tuple[Event, "recurrence_service.Occurrence"]]:
    """Every (event, occurrence) pair in a window, expanded and
    sorted — the one expansion every calendar read shares. With
    a restricted viewer (B-105: ``subgroup_scope`` is the
    viewer's visible set from services/subgroups), events scoped
    to a sub-group outside the set never expand at all — the
    calendar must not leak what the event list hides. Callers
    without a viewer (the organiser-issued iCal feed) pass no
    restriction: the whole group's calendar, by design."""
    pairs: list[tuple[Event, recurrence_service.Occurrence]] = []
    for event in _live_events(db, group):
        if scope_restricted:
            from gpa.services import subgroups as subgroup_service

            if not subgroup_service.can_see_scoped(event.subgroup_id, subgroup_scope):
                continue
        if not _matches(
            event, q=q, event_type=event_type, place=place, sub_group=sub_group
        ):
            continue
        recurrence = recurrence_service.get_recurrence(db, event.id)
        for occ in recurrence_service.expand(
            starts_at_utc=event.starts_at, ends_at_utc=event.ends_at,
            tz_name=event.timezone,
            rrule_text=recurrence.rrule if recurrence else None,
            recurrence=recurrence,
            window_start_utc=window_start, window_end_utc=window_end,
        ):
            pairs.append((event, occ))
    pairs.sort(key=lambda pair: (pair[1].starts_at, pair[0].id, pair[1].original_start_local))
    return pairs


# ------------------------------------------------------------------
# Agenda (B-055)
# ------------------------------------------------------------------


def _encode_cursor(event: Event, occ) -> str:
    payload = json.dumps(
        [occ.starts_at.isoformat(), event.id, occ.original_start_local.isoformat()]
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str, str]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        starts_at, event_id, original = decoded
        return str(starts_at), str(event_id), str(original)
    except Exception:
        raise AppError("validation_error", detail="That page cursor is not readable.")


def agenda(
    db: Session,
    group: Group,
    *,
    window_start: datetime,
    window_end: datetime,
    q=None,
    event_type=None,
    place=None,
    sub_group=None,
    cursor: str | None = None,
    limit: int = 50,
    subgroup_scope: set[str] | None = None,
    scope_restricted: bool = False,
) -> tuple[list[dict], str | None]:
    """One page of the agenda + the next page's cursor (None at
    the end). The cursor names the last occurrence served —
    (start, event, original start) — so pages are stable while
    the group keeps planning around them."""
    limit = max(1, min(limit, MAX_AGENDA_LIMIT))
    pairs = expand_group(
        db, group, window_start=window_start, window_end=window_end,
        q=q, event_type=event_type, place=place, sub_group=sub_group,
        subgroup_scope=subgroup_scope, scope_restricted=scope_restricted,
    )
    if cursor:
        mark = _decode_cursor(cursor)
        pairs = [
            pair for pair in pairs
            if (pair[1].starts_at.isoformat(), pair[0].id,
                pair[1].original_start_local.isoformat()) > mark
        ]
    page = pairs[:limit]
    next_cursor = None
    if len(pairs) > limit and page:
        next_cursor = _encode_cursor(page[-1][0], page[-1][1])
    items = [
        occurrence_view(event, occ, flyer=flyer_view(db, event)).model_dump()
        for event, occ in page
    ]
    return items, next_cursor


# ------------------------------------------------------------------
# Month grid (B-055)
# ------------------------------------------------------------------


def month_grid(
    db: Session,
    group: Group,
    *,
    year: int,
    month: int,
    subgroup_scope: set[str] | None = None,
    scope_restricted: bool = False,
) -> dict:
    """The month as renderable weeks: Monday-first whole weeks
    covering the month, each day with its occurrences (assigned
    by the day the occurrence starts ON THE WALL in the group's
    timezone). Days outside the month are present and marked —
    a calendar grid has no holes."""
    tz = ZoneInfo(group.home_timezone or "UTC")
    first = date(year, month, 1)
    last = date(year, month, calendar_module.monthrange(year, month)[1])
    grid_start = first - timedelta(days=first.weekday())       # Monday
    grid_end = last + timedelta(days=6 - last.weekday())       # Sunday
    window_start = datetime.combine(grid_start, time(0, 0), tzinfo=tz).astimezone(
        timezone.utc
    ).replace(tzinfo=None)
    window_end = datetime.combine(
        grid_end + timedelta(days=1), time(0, 0), tzinfo=tz
    ).astimezone(timezone.utc).replace(tzinfo=None)
    pairs = expand_group(
        db, group, window_start=window_start, window_end=window_end,
        subgroup_scope=subgroup_scope, scope_restricted=scope_restricted,
    )
    by_day: dict[date, list[dict]] = {}
    for event, occ in pairs:
        by_day.setdefault(occ.start_local.date(), []).append(
            occurrence_view(event, occ, flyer=flyer_view(db, event)).model_dump()
        )
    weeks: list[list[dict]] = []
    cursor_day = grid_start
    while cursor_day <= grid_end:
        week = []
        for _ in range(7):
            week.append(
                {
                    "date": cursor_day.isoformat(),
                    "in_month": cursor_day.month == month,
                    "occurrences": by_day.get(cursor_day, []),
                }
            )
            cursor_day += timedelta(days=1)
        weeks.append(week)
    return {
        "year": year, "month": month,
        "timezone": group.home_timezone or "UTC",
        "weeks": weeks,
    }


# ------------------------------------------------------------------
# Tonight (B-056)
# ------------------------------------------------------------------


def tonight(
    db: Session,
    group: Group,
    *,
    now_utc: datetime,
    on_date: date | None = None,
    subgroup_scope: set[str] | None = None,
    scope_restricted: bool = False,
) -> list[dict]:
    """What's on this evening, in the group's own clock.

    The evening runs 15:00 → 03:00 local. Ordering is
    near-first WHERE PLACE DATA EXISTS: events with pinned
    coordinates lead (they're the ones a person can navigate
    to), then everything by start time. Flyer-born events carry
    their flyer (image + alt text) — the poster's whole point."""
    tz = ZoneInfo(group.home_timezone or "UTC")
    local_today = on_date or now_utc.replace(tzinfo=timezone.utc).astimezone(tz).date()
    start_local = datetime.combine(local_today, EVENING_START)
    end_local = datetime.combine(local_today + timedelta(days=1), EVENING_END)
    window_start = start_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    window_end = end_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    pairs = expand_group(
        db, group, window_start=window_start, window_end=window_end,
        subgroup_scope=subgroup_scope, scope_restricted=scope_restricted,
    )
    pairs.sort(
        key=lambda pair: (
            0 if (pair[0].lat is not None and pair[0].lng is not None) else 1,
            pair[1].starts_at,
            pair[0].id,
        )
    )
    return [
        occurrence_view(event, occ, flyer=flyer_view(db, event)).model_dump()
        for event, occ in pairs
    ]


# ------------------------------------------------------------------
# Conflicts (B-057) — flags, never blocks
# ------------------------------------------------------------------


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance — the travel judgement's ruler."""
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _host_key(event: Event) -> str | None:
    if event.host_id:
        return event.host_id
    label = event.host_label.strip().lower()
    return f"label:{label}" if label else None


def _place_key(event: Event) -> str | None:
    name = " ".join(event.place_name.split()).lower()
    return name or None


def conflicts(
    db: Session,
    group: Group,
    *,
    window_start: datetime,
    window_end: datetime,
    subgroup_scope: set[str] | None = None,
    scope_restricted: bool = False,
) -> list[dict]:
    """The window's conflict flags. Cancelled events and
    cancelled occurrences take no part — a thing that will not
    happen cannot clash. Overlap is STRICT: back-to-back in the
    same room (one ends as the next begins) is a plan, not a
    conflict, and must never flag. A restricted viewer (B-105)
    only ever hears about clashes between events they may see."""
    pairs = [
        (event, occ)
        for event, occ in expand_group(
            db, group, window_start=window_start, window_end=window_end,
            subgroup_scope=subgroup_scope, scope_restricted=scope_restricted,
        )
        if event.status == "published" and not occ.cancelled
    ]
    flags: list[dict] = []

    def flag(kind: str, a, b, message: str, **extra) -> None:
        (event_a, occ_a), (event_b, occ_b) = a, b
        flags.append(
            {
                "kind": kind,
                "event_id": event_a.id,
                "occurrence_start": occ_a.starts_at.isoformat() + "Z",
                "other_event_id": event_b.id,
                "other_occurrence_start": occ_b.starts_at.isoformat() + "Z",
                "message": message,
                **extra,
            }
        )

    for index, pair_a in enumerate(pairs):
        event_a, occ_a = pair_a
        for pair_b in pairs[index + 1:]:
            event_b, occ_b = pair_b
            overlaps = occ_a.starts_at < occ_b.ends_at and occ_b.starts_at < occ_a.ends_at
            same_event = event_a.id == event_b.id
            if overlaps and not same_event:
                host_a, host_b = _host_key(event_a), _host_key(event_b)
                if host_a is not None and host_a == host_b:
                    flag(
                        "host_overlap", pair_a, pair_b,
                        f"{event_a.host_label or 'The host'} is double-booked: "
                        f"'{event_a.title}' overlaps '{event_b.title}'.",
                    )
                place_a, place_b = _place_key(event_a), _place_key(event_b)
                if place_a is not None and place_a == place_b:
                    flag(
                        "place_overlap", pair_a, pair_b,
                        f"{event_a.place_name} is double-booked: "
                        f"'{event_a.title}' overlaps '{event_b.title}'.",
                    )
            # Travel: the same host, two pinned places, a gap too
            # small for the distance between them.
            if not overlaps and not same_event:
                host_a, host_b = _host_key(event_a), _host_key(event_b)
                if host_a is None or host_a != host_b:
                    continue
                if None in (event_a.lat, event_a.lng, event_b.lat, event_b.lng):
                    continue
                place_a, place_b = _place_key(event_a), _place_key(event_b)
                if place_a is None or place_a == place_b:
                    continue
                if occ_a.ends_at <= occ_b.starts_at:
                    earlier, later = pair_a, pair_b
                elif occ_b.ends_at <= occ_a.starts_at:
                    earlier, later = pair_b, pair_a
                else:
                    continue
                (from_event, from_occ), (to_event, to_occ) = earlier, later
                gap_minutes = (to_occ.starts_at - from_occ.ends_at).total_seconds() / 60
                distance = _haversine_km(
                    from_event.lat, from_event.lng, to_event.lat, to_event.lng
                )
                needed = distance / TRAVEL_SPEED_KMH * 60
                if gap_minutes < needed:
                    flag(
                        "travel_impossible", earlier, later,
                        f"Only {int(gap_minutes)} minutes between "
                        f"'{from_event.title}' ({from_event.place_name}) and "
                        f"'{to_event.title}' ({to_event.place_name}), "
                        f"about {distance:.0f} km apart.",
                        distance_km=round(distance, 1),
                        needed_minutes=int(needed),
                        available_minutes=int(gap_minutes),
                    )
    flags.sort(key=lambda f: (f["occurrence_start"], f["kind"], f["event_id"]))
    return flags
