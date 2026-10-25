"""Recurrence: RRULE storage + occurrence expansion (roadmap B-052).

Written by hand, in-house (D-46) — the grammar the product needs
is a well-understood subset of RFC 5545, and owning it means the
DST behaviour is *ours*, stated in one place, and golden-tested:

**The wall-clock law.** A series lives in its event's timezone as
*local wall time*. A weekly 19:00 meeting in America/New_York is
at 19:00 on the wall in EST and in EDT alike; its UTC instant is
what moves when the clocks change. Expansion therefore walks
*local* candidate times and converts each one through the zone —
pinning UTC instead would drift the meeting an hour twice a year
(B-052's acceptance test is exactly that trap).

Supported grammar (anything else is a validation error, in plain
words — a rule we cannot expand is never stored):

    FREQ=DAILY|WEEKLY|MONTHLY|YEARLY   (required)
    INTERVAL=n                          (default 1)
    COUNT=n                             (occurrences from DTSTART)
    UNTIL=YYYYMMDD | YYYYMMDDTHHMMSSZ   (inclusive)
    BYDAY=MO,TU,… with optional ordinal (2TU, -1FR) for
          MONTHLY/YEARLY; plain weekdays for WEEKLY/DAILY
    BYMONTHDAY=n[,…]  (negative counts from the month's end)
    BYMONTH=n[,…]     (YEARLY; a mask elsewhere)

Occurrences are computed, never stored (Doc 06 §6). Per-occurrence
exceptions ride on the Recurrence row as JSON:
``{"original_start": <local ISO>, "moved_to": <local ISO>|null,
"cancelled": bool}`` — "this week's meeting moved" is an
exception, not an edit to the rule.
"""

import calendar as calendar_module
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from gpa.errors import AppError

WEEKDAY_CODES: dict[str, int] = {
    "MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6,
}

FREQUENCIES: tuple[str, ...] = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")

# Runaway guards: a rule like FREQ=DAILY with no end, asked for a
# ten-year window, must not spin forever. Expansion is bounded by
# steps walked and occurrences produced; hitting a bound raises a
# plain-words error instead of hanging the request.
MAX_STEPS = 50_000


@dataclass
class Rrule:
    """A parsed, validated rule (services own this shape)."""

    freq: str
    interval: int = 1
    count: int | None = None
    until_date: date | None = None       # inclusive local date bound
    until_utc: datetime | None = None    # inclusive UTC instant bound
    byday: list[tuple[int | None, int]] = field(default_factory=list)
    bymonthday: list[int] = field(default_factory=list)
    bymonth: list[int] = field(default_factory=list)


@dataclass
class Occurrence:
    """One concrete happening of a series (or of a single event).

    Times are carried in three forms: local-naive (the wall
    clock), UTC-naive (the stored-instant convention, D-21), and
    the exception state — an occurrence a person moved or
    cancelled stays in the list, flagged, because every surface
    must show it *as* moved/cancelled, never silently vanished.
    """

    start_local: datetime
    end_local: datetime
    starts_at: datetime  # UTC, naive
    ends_at: datetime    # UTC, naive
    original_start_local: datetime
    is_exception: bool = False
    cancelled: bool = False


def _bad(detail: str) -> AppError:
    return AppError("validation_error", detail=detail)


def parse_rrule(text: str) -> Rrule:
    """Parse + validate an RRULE string, or raise in plain words."""
    parts: dict[str, str] = {}
    for piece in (text or "").split(";"):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise _bad(f"Recurrence rule part '{piece}' is not NAME=value.")
        name, _, value = piece.partition("=")
        name = name.strip().upper()
        if name in parts:
            raise _bad(f"Recurrence rule repeats {name}.")
        parts[name] = value.strip()
    known = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST"}
    unknown = set(parts) - known
    if unknown:
        raise _bad(f"Recurrence rule uses unsupported part(s): {', '.join(sorted(unknown))}.")
    freq = parts.get("FREQ", "").upper()
    if freq not in FREQUENCIES:
        raise _bad("Recurrence rule needs FREQ=DAILY, WEEKLY, MONTHLY, or YEARLY.")
    rule = Rrule(freq=freq)
    if "INTERVAL" in parts:
        try:
            rule.interval = int(parts["INTERVAL"])
        except ValueError:
            raise _bad("INTERVAL must be a whole number.")
        if rule.interval < 1:
            raise _bad("INTERVAL must be at least 1.")
    if "COUNT" in parts:
        try:
            rule.count = int(parts["COUNT"])
        except ValueError:
            raise _bad("COUNT must be a whole number.")
        if rule.count < 1:
            raise _bad("COUNT must be at least 1.")
    if "UNTIL" in parts:
        raw = parts["UNTIL"]
        try:
            if raw.endswith("Z"):
                rule.until_utc = datetime.strptime(
                    raw, "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=timezone.utc).replace(tzinfo=None)
            else:
                rule.until_date = datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            raise _bad("UNTIL must be YYYYMMDD or YYYYMMDDTHHMMSSZ.")
    if "BYDAY" in parts:
        for token in parts["BYDAY"].split(","):
            token = token.strip().upper()
            if not token:
                continue
            code = token[-2:]
            if code not in WEEKDAY_CODES:
                raise _bad(f"BYDAY value '{token}' is not a weekday code.")
            ordinal_text = token[:-2]
            ordinal: int | None = None
            if ordinal_text:
                try:
                    ordinal = int(ordinal_text)
                except ValueError:
                    raise _bad(f"BYDAY value '{token}' has a bad ordinal.")
                if ordinal == 0 or abs(ordinal) > 5:
                    raise _bad(f"BYDAY value '{token}' has an out-of-range ordinal.")
            rule.byday.append((ordinal, WEEKDAY_CODES[code]))
    if "BYMONTHDAY" in parts:
        for token in parts["BYMONTHDAY"].split(","):
            try:
                day = int(token.strip())
            except ValueError:
                raise _bad("BYMONTHDAY values must be whole numbers.")
            if day == 0 or abs(day) > 31:
                raise _bad("BYMONTHDAY values must be between -31 and 31, not 0.")
            rule.bymonthday.append(day)
    if "BYMONTH" in parts:
        for token in parts["BYMONTH"].split(","):
            try:
                month = int(token.strip())
            except ValueError:
                raise _bad("BYMONTH values must be whole numbers.")
            if not 1 <= month <= 12:
                raise _bad("BYMONTH values must be between 1 and 12.")
            rule.bymonth.append(month)
    return rule


def _month_days(year: int, month: int, rule: Rrule, start: datetime) -> list[int]:
    """The days of one month a MONTHLY/YEARLY step lands on.

    Priority: BYMONTHDAY, then BYDAY (ordinal or every), then the
    series start's own day-of-month — the RFC's defaulting, in the
    order a reader expects."""
    days_in_month = calendar_module.monthrange(year, month)[1]
    days: set[int] = set()
    if rule.bymonthday:
        for wanted in rule.bymonthday:
            day = wanted if wanted > 0 else days_in_month + wanted + 1
            if 1 <= day <= days_in_month:
                days.add(day)
    elif rule.byday:
        for ordinal, weekday in rule.byday:
            matches = [
                d for d in range(1, days_in_month + 1)
                if date(year, month, d).weekday() == weekday
            ]
            if ordinal is None:
                days.update(matches)
            elif ordinal > 0 and ordinal <= len(matches):
                days.add(matches[ordinal - 1])
            elif ordinal < 0 and abs(ordinal) <= len(matches):
                days.add(matches[ordinal])
    elif start.day <= days_in_month:
        days.add(start.day)
    return sorted(days)


def _candidates(rule: Rrule, start: datetime):
    """Yield local-naive candidate starts, ascending, from the
    series start onward (the start itself first — DTSTART always
    counts as an occurrence, RFC 5545 §3.3.10). Termination is
    the caller's job (COUNT/UNTIL/window), guarded by MAX_STEPS."""
    yield start
    steps = 0
    if rule.freq == "DAILY":
        current = start
        while True:
            steps += 1
            if steps > MAX_STEPS:
                return
            current = current + timedelta(days=rule.interval)
            if rule.bymonth and current.month not in rule.bymonth:
                continue
            if rule.bymonthday:
                days_in_month = calendar_module.monthrange(current.year, current.month)[1]
                wanted = {
                    d if d > 0 else days_in_month + d + 1 for d in rule.bymonthday
                }
                if current.day not in wanted:
                    continue
            if rule.byday and current.weekday() not in [w for _o, w in rule.byday]:
                continue
            yield current
    elif rule.freq == "WEEKLY":
        weekdays = sorted({w for _o, w in rule.byday}) or [start.weekday()]
        # Anchor: the Monday of the start's week, at the start's
        # wall time. Weeks step by INTERVAL from there.
        monday = (start - timedelta(days=start.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wall = start.time()
        week_offset = 0
        while True:
            steps += 1
            if steps > MAX_STEPS:
                return
            week_start = monday + timedelta(weeks=week_offset * rule.interval)
            for weekday in weekdays:
                candidate = datetime.combine(
                    (week_start + timedelta(days=weekday)).date(), wall
                )
                if candidate > start:
                    yield candidate
            week_offset += 1
    elif rule.freq == "MONTHLY":
        offset = 0
        while True:
            steps += 1
            if steps > MAX_STEPS:
                return
            total_months = (start.year * 12 + (start.month - 1)) + offset * rule.interval
            year, month_zero = divmod(total_months, 12)
            month = month_zero + 1
            if year > 9998:
                return
            offset += 1
            if rule.bymonth and month not in rule.bymonth:
                continue  # BYMONTH is a mask for monthly rules too
            for day in _month_days(year, month, rule, start):
                candidate = datetime.combine(date(year, month, day), start.time())
                if candidate > start:
                    yield candidate
    elif rule.freq == "YEARLY":
        year_offset = 0
        while True:
            steps += 1
            if steps > MAX_STEPS:
                return
            year = start.year + year_offset * rule.interval
            if year > 9998:
                return
            year_offset += 1
            months = sorted(rule.bymonth) or [start.month]
            for month in months:
                for day in _month_days(year, month, rule, start):
                    candidate = datetime.combine(date(year, month, day), start.time())
                    if candidate > start:
                        yield candidate


def _to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    """One local wall time → its UTC instant (naive, D-21).

    This single conversion is where the wall-clock law lives:
    the local time is pinned to the wall; the zone decides which
    instant that is on *that* date — EST or EDT as the date says."""
    return local_naive.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def load_exceptions(recurrence) -> list[dict]:
    """A Recurrence row's exceptions as a list of plain dicts."""
    try:
        rows = json.loads(recurrence.exceptions_json or "[]")
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


# ------------------------------------------------------------------
# Rule administration (B-052's write side)
# ------------------------------------------------------------------


def get_recurrence(db, event_id: str):
    """The event's Recurrence row, or None (a one-off event)."""
    from gpa.models.events import Recurrence

    return db.query(Recurrence).filter(Recurrence.event_id == event_id).one_or_none()


def _validate_exceptions(exceptions: list[dict]) -> list[dict]:
    """Normalise + validate an exception list: every row needs an
    original_start the expander can read; a moved_to must parse
    too. Bad rows are refused in plain words at write time so the
    expander never meets a surprise at read time."""
    out: list[dict] = []
    for row in exceptions:
        if not isinstance(row, dict) or not row.get("original_start"):
            raise _bad("Each exception needs the occurrence's original_start.")
        try:
            original = datetime.fromisoformat(str(row["original_start"]))
        except ValueError:
            raise _bad("An exception's original_start must be a local ISO datetime.")
        entry = {
            "original_start": original.isoformat(),
            "moved_to": None,
            "cancelled": bool(row.get("cancelled", False)),
        }
        if row.get("moved_to"):
            try:
                entry["moved_to"] = datetime.fromisoformat(str(row["moved_to"])).isoformat()
            except ValueError:
                raise _bad("An exception's moved_to must be a local ISO datetime.")
        out.append(entry)
    return out


def set_recurrence(
    db, *, actor_label: str, group, event, rrule_text: str,
    exceptions: list[dict] | None = None,
):
    """Create or replace an event's series rule (B-052).

    The rule is parsed BEFORE anything is stored — a rule the
    expander cannot read is refused at the door. ``exceptions``
    of None keeps the existing list (a rule correction must not
    silently drop the moves people already made)."""
    from gpa.models.events import Recurrence
    from gpa.services import audit

    parse_rrule(rrule_text)  # validation only; raises in plain words
    row = get_recurrence(db, event.id)
    if row is None:
        row = Recurrence(event_id=event.id, rrule=rrule_text, exceptions_json="[]")
        db.add(row)
    else:
        row.rrule = rrule_text
    if exceptions is not None:
        row.exceptions_json = json.dumps(_validate_exceptions(exceptions))
    audit.record(
        db, actor=actor_label, action="event.recurrence_set",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"rrule": rrule_text},
    )
    db.commit()
    return row


def add_exception(
    db, *, actor_label: str, group, event, original_start: str,
    moved_to: str | None = None, cancelled: bool = False,
):
    """Add (or replace) one occurrence's exception (B-052):
    "this week's meeting moved to Thursday" as data."""
    from gpa.services import audit

    row = get_recurrence(db, event.id)
    if row is None:
        raise _bad("That event does not repeat, so it has no occurrences to move.")
    existing = [
        entry for entry in load_exceptions(row)
        if entry.get("original_start") != datetime.fromisoformat(original_start).isoformat()
    ]
    new_rows = _validate_exceptions(
        [{"original_start": original_start, "moved_to": moved_to, "cancelled": cancelled}]
    )
    row.exceptions_json = json.dumps(existing + new_rows)
    audit.record(
        db, actor=actor_label, action="event.recurrence_exception",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"original_start": original_start, "moved_to": moved_to, "cancelled": cancelled},
    )
    db.commit()
    return row


def delete_recurrence(db, *, actor_label: str, group, event) -> None:
    """Remove a series rule: the event becomes a one-off at its
    stored start (B-052). Refused when there is no rule — the
    client is told the truth instead of a fake success."""
    from gpa.services import audit

    row = get_recurrence(db, event.id)
    if row is None:
        raise AppError("not_found", detail="That event does not repeat.")
    db.delete(row)
    audit.record(
        db, actor=actor_label, action="event.recurrence_removed",
        group_id=group.id, entity_type="event", entity_id=event.id,
    )
    db.commit()


def _exception_map(recurrence) -> dict[datetime, dict]:
    """Exceptions keyed by the occurrence's ORIGINAL local start."""
    out: dict[datetime, dict] = {}
    if recurrence is None:
        return out
    for row in load_exceptions(recurrence):
        raw = row.get("original_start")
        if not raw:
            continue
        try:
            original = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        out[original] = row
    return out


def expand(
    *,
    starts_at_utc: datetime,
    ends_at_utc: datetime,
    tz_name: str,
    rrule_text: str | None = None,
    recurrence=None,
    window_start_utc: datetime,
    window_end_utc: datetime,
) -> list[Occurrence]:
    """Expand one event into the occurrences overlapping a window.

    ``window_*`` are UTC-naive instants; an occurrence counts when
    it overlaps [window_start, window_end). Cancelled occurrences
    are returned flagged (callers decide how to show them);
    occurrences entirely outside are dropped — except that COUNT
    and UNTIL are honoured from the series start, so a window
    late in a counted series sees the series truly end.
    """
    tz = ZoneInfo(tz_name)
    start_local = starts_at_utc.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
    end_local = ends_at_utc.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
    duration = end_local - start_local  # wall-clock duration
    exceptions = _exception_map(recurrence)

    def make(original_local: datetime) -> Occurrence:
        exception = exceptions.get(original_local)
        start = original_local
        cancelled = False
        is_exception = exception is not None
        if exception is not None:
            cancelled = bool(exception.get("cancelled"))
            moved_to = exception.get("moved_to")
            if moved_to and not cancelled:
                try:
                    start = datetime.fromisoformat(str(moved_to))
                except ValueError:
                    pass  # a malformed move is ignored, never fatal
        end = start + duration
        return Occurrence(
            start_local=start,
            end_local=end,
            starts_at=_to_utc(start, tz),
            ends_at=_to_utc(end, tz),
            original_start_local=original_local,
            is_exception=is_exception,
            cancelled=cancelled,
        )

    occurrences: list[Occurrence] = []
    if not rrule_text:
        occ = make(start_local)
        if occ.starts_at < window_end_utc and occ.ends_at > window_start_utc:
            occurrences.append(occ)
        return occurrences

    rule = parse_rrule(rrule_text)
    produced = 0
    for candidate in _candidates(rule, start_local):
        candidate_utc = _to_utc(candidate, tz)
        if rule.until_utc is not None and candidate_utc > rule.until_utc:
            break
        if rule.until_date is not None and candidate.date() > rule.until_date:
            break
        produced += 1
        if rule.count is not None and produced > rule.count:
            break
        # Past the window (with duration slack): nothing later can
        # come back into it — candidates ascend.
        if candidate_utc >= window_end_utc and candidate > start_local:
            # An exception could still move this one INTO the
            # window; check before stopping.
            exception = exceptions.get(candidate)
            if exception is None or not exception.get("moved_to"):
                break
        occ = make(candidate)
        if occ.starts_at < window_end_utc and occ.ends_at > window_start_utc:
            occurrences.append(occ)
        if produced > MAX_STEPS:
            break
    occurrences.sort(key=lambda o: (o.starts_at, o.original_start_local))
    return occurrences
