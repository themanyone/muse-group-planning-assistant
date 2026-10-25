"""The flyer surfaces: the wall, the alt-text edit, the print
month (roadmap B-070, B-065, B-072).

Three reads/writes over the Flyer rows Phase 2 planted and
Phase 5 reads:

- **The wall** (B-070): a period's posters — each with its
  picture record, its transcript, its description state, and the
  event approval made of it. A poster with no event yet is
  ``pending_review``: present and labelled, never invisible.
- **The alt-text edit** (B-065): a person's own description.
  This is the human road to ``complete`` — and the law's edge is
  enforced here: placeholder words or near-empty text cannot
  complete a description, because they are not one.
- **The print month** (B-072): the month's events as a clean,
  ordered, TEXT-COMPLETE listing for a real noticeboard. The
  frontend renders; the guarantee that every event's facts are
  present as text is the backend's, and a test asserts it fact
  by fact.
"""

import calendar as calendar_module
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.sources import Flyer, Source
from gpa.schemas.events import iso_utc
from gpa.schemas.flyers import FlyerWallEntry, PrintEventRow, WallEventSummary
from gpa.services import audit
from gpa.services import calendar_views
from gpa.services.vision import _is_genuine_alt


def _period_bounds(period: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """A "YYYY-MM" period's [start, end) as UTC-naive instants,
    the month being the GROUP's month (a poster pinned on the
    1st in Tokyo is not in London's previous month)."""
    try:
        year, month = (int(part) for part in period.split("-"))
        first = datetime(year, month, 1)
    except (ValueError, TypeError):
        raise AppError(
            "validation_error",
            detail=f"Period '{period}' is not a month — use YYYY-MM.",
        ) from None
    last_day = calendar_module.monthrange(year, month)[1]
    start_local = datetime.combine(first.date(), time.min)
    end_local = datetime.combine(
        first.date().replace(day=last_day), time.max
    ) + timedelta(microseconds=1)
    start_utc = start_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _current_period(tz: ZoneInfo) -> str:
    now = datetime.now(timezone.utc).astimezone(tz)
    return f"{now.year:04d}-{now.month:02d}"


def get_flyer(db: Session, group: Group, flyer_id: str) -> Flyer:
    """Fetch a flyer inside its group, or 404 — flyers never leak
    across groups, like every other row here."""
    flyer = db.get(Flyer, flyer_id)
    if flyer is None or flyer.group_id != group.id:
        raise AppError("not_found", detail="That flyer could not be found.")
    return flyer


def update_alt_text(
    db: Session, *, actor_label: str, group: Group, flyer_id: str, alt_text: str
) -> Flyer:
    """A person's own description of a flyer (B-065).

    The words are stored tidied but otherwise as given, and the
    status flips to ``complete`` — a human description IS the
    genuine article the law wants. What the service refuses:
    words that are not a description at all (the placeholder, a
    near-empty shrug) — accepting them would paint the defect
    green, which is the one lie this law exists to prevent.
    """
    flyer = get_flyer(db, group, flyer_id)
    cleaned = " ".join((alt_text or "").split())
    asset_name = ""
    if not _is_genuine_alt(cleaned, filename=asset_name):
        raise AppError(
            "validation_error",
            detail=(
                "That is not a description yet — say what the picture "
                "shows: its layout, its imagery, and the facts on it."
            ),
        )
    flyer.alt_text = cleaned[:500]
    flyer.alt_text_status = "complete"
    audit.record(
        db, actor=actor_label, action="flyer.alt_text_updated",
        group_id=group.id, entity_type="flyer", entity_id=flyer.id,
        detail={"source_id": flyer.source_id},
    )
    db.commit()
    return flyer


def flyer_wall(
    db: Session, *, group: Group, period: str | None
) -> tuple[str, list[FlyerWallEntry]]:
    """The period's wall (B-070).

    A flyer belongs to the period when its event falls in it, or
    — with no event yet — when the poster itself arrived in it.
    Ordering is the noticeboard's own: dated posters by their
    event's start, then the undated by arrival.
    """
    tz = ZoneInfo(group.home_timezone or "UTC")
    period = period or _current_period(tz)
    start_utc, end_utc = _period_bounds(period, tz)
    flyers = (
        db.query(Flyer)
        .filter(Flyer.group_id == group.id)
        .order_by(Flyer.created_at, Flyer.id)
        .all()
    )
    events_by_flyer: dict[str, Event] = {}
    for event in db.query(Event).filter(Event.group_id == group.id).all():
        # B-080: a deleted event's poster leaves the wall with it.
        if event.flyer_id and event.deleted_at is None:
            events_by_flyer[event.flyer_id] = event
    sources = {
        source.id: source
        for source in db.query(Source).filter(Source.group_id == group.id).all()
    }

    dated: list[tuple[datetime, FlyerWallEntry]] = []
    undated: list[tuple[datetime, FlyerWallEntry]] = []
    for flyer in flyers:
        event = events_by_flyer.get(flyer.id)
        if event is not None:
            if not (start_utc <= event.starts_at < end_utc):
                continue
            review_status = event.status
        else:
            if not (start_utc <= flyer.created_at < end_utc):
                continue
            source = sources.get(flyer.source_id)
            review_status = (
                "read_failed"
                if source is not None and source.stage == "read_failed"
                else "pending_review"
            )
        entry = FlyerWallEntry(
            flyer_id=flyer.id,
            source_id=flyer.source_id,
            source_kind=sources[flyer.source_id].kind if flyer.source_id in sources else "",
            parent_flyer_id=flyer.parent_flyer_id,
            thumbnail_key=flyer.thumbnail_key,
            width=flyer.width,
            height=flyer.height,
            alt_text=flyer.alt_text,
            alt_text_status=flyer.alt_text_status,
            transcript=flyer.transcript,
            generated=flyer.generated,
            review_status=review_status,
            event=(
                WallEventSummary(
                    id=event.id, title=event.title,
                    starts_at=iso_utc(event.starts_at),
                    status=event.status,
                )
                if event is not None
                else None
            ),
        )
        if event is not None:
            dated.append((event.starts_at, entry))
        else:
            undated.append((flyer.created_at, entry))
    dated.sort(key=lambda pair: pair[0])
    undated.sort(key=lambda pair: pair[0])
    return period, [entry for _when, entry in dated + undated]


def _when_words(event: Event, occ) -> str:
    """An occurrence's moment as noticeboard words, in the
    event's own timezone: "Saturday 24 October 2026, 7:00 PM –
    9:00 PM". All-day events say the day, plainly."""
    start = occ.start_local
    end = occ.end_local
    day = start.strftime("%A %d %B %Y")
    if event.all_day:
        return f"{day} (all day)"
    start_t = start.strftime("%I:%M %p").lstrip("0")
    end_t = end.strftime("%I:%M %p").lstrip("0")
    return f"{day}, {start_t} – {end_t}"


def print_month(
    db: Session, *, group: Group, month: str | None
) -> tuple[str, list[PrintEventRow], str]:
    """The month's print listing (B-072): rows + the rendered
    plain-text document. Every fact the event carries — title,
    moment, place, host, cost, bring, description — is present
    in BOTH, so a completeness assertion can hold the backend to
    it word by word."""
    tz = ZoneInfo(group.home_timezone or "UTC")
    period = month or _current_period(tz)
    start_utc, end_utc = _period_bounds(period, tz)
    pairs = calendar_views.expand_group(
        db, group, window_start=start_utc, window_end=end_utc
    )
    rows: list[PrintEventRow] = []
    for event, occ in pairs:
        place = event.place_name
        if event.place_address:
            place = f"{place} ({event.place_address})" if place else event.place_address
        rows.append(
            PrintEventRow(
                event_id=event.id,
                title=event.title,
                when=_when_words(event, occ),
                place=place,
                host=event.host_label,
                cost=event.cost_text,
                bring=event.bring_list,
                description=event.description,
                status="cancelled" if occ.cancelled else event.status,
            )
        )
    lines = [f"{group.name} — {period}", ""]
    if not rows:
        lines.append("No events this month.")
    for row in rows:
        lines.append(row.title + (" (CANCELLED)" if row.status == "cancelled" else ""))
        lines.append(f"  When: {row.when}")
        if row.place:
            lines.append(f"  Where: {row.place}")
        if row.host:
            lines.append(f"  Host: {row.host}")
        if row.cost:
            lines.append(f"  Cost: {row.cost}")
        if row.bring:
            lines.append(f"  Bring: {row.bring}")
        if row.description:
            lines.append(f"  {row.description}")
        lines.append("")
    return period, rows, "\n".join(lines).strip() + "\n"
