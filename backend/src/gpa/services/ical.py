"""The iCal engine + calendar feeds (roadmap B-053, B-054).

Hand-written, in-house (D-46): serialiser, VTIMEZONE builder,
parser, and the per-group secret feed. The laws it keeps:

- **Cancelled is emitted as cancelled** (``STATUS:CANCELLED``),
  never silently vanished — in per-event files and in the feed
  alike. Drafts, by contrast, never appear at all (B-049's
  bright line reaches this far).
- **Times are the same times everywhere.** Events serialise
  with their own timezone (``DTSTART;TZID=…``) beside a real
  VTIMEZONE built from the zone's actual transitions, so a
  Google/Apple/Outlook import shows the event at the wall time
  the group means. The round-trip parser in this module proves
  it in tests: parse the feed back, expand it, and the
  occurrences must equal the agenda's.
- **Feed tokens are secrets** (B-014 discipline): shown once at
  issue, stored hashed, revocable instantly and totally.
"""

import hashlib
import secrets
from datetime import datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.events import CalendarFeed, Event, Recurrence
from gpa.models.groups import Group
from gpa.security import hash_secret
from gpa.services import audit, recurrence as recurrence_service

PRODID = "-//Group Planning Assistant//Calendar//EN"
UID_DOMAIN = "gpa"


# ------------------------------------------------------------------
# Text plumbing (RFC 5545 §3.3.11, §3.1)
# ------------------------------------------------------------------


def escape_text(value: str) -> str:
    """Escape a TEXT value: backslash first, then ; , and
    newlines — the order matters, and getting it wrong corrupts
    exactly the events with interesting descriptions."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def unescape_text(value: str) -> str:
    """The reader's inverse of :func:`escape_text`."""
    out: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in ("n", "N") else nxt)
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def fold_line(line: str) -> str:
    """Fold a content line at 75 octets (CRLF + one space), the
    RFC's rule, counting UTF-8 octets so multi-byte characters
    are never split mid-sequence."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    pieces: list[bytes] = []
    first = True
    while encoded:
        budget = 75 if first else 74  # continuation spends 1 on its space
        cut = min(budget, len(encoded))
        while cut < len(encoded) and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1  # never split a UTF-8 sequence
        pieces.append(encoded[:cut])
        encoded = encoded[cut:]
        first = False
    return b"\r\n ".join(pieces).decode("utf-8")


def _join(lines: list[str]) -> str:
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def _format_utc(value: datetime) -> str:
    """A stored UTC-naive datetime → the iCal UTC form."""
    return value.strftime("%Y%m%dT%H%M%SZ")


def _format_local(value: datetime) -> str:
    """A local-naive datetime → the iCal floating form (the TZID
    parameter beside it names the zone)."""
    return value.strftime("%Y%m%dT%H%M%S")


def _format_date(value: datetime) -> str:
    return value.strftime("%Y%m%d")


# ------------------------------------------------------------------
# VTIMEZONE — the zone's real transitions, enumerated
# ------------------------------------------------------------------


def _offset_at(tz: ZoneInfo, instant_utc: datetime) -> timedelta:
    return instant_utc.replace(tzinfo=timezone.utc).astimezone(tz).utcoffset()


def _transitions(tz: ZoneInfo, start_utc: datetime, end_utc: datetime):
    """Yield (transition instant UTC, offset before, offset after)
    for every offset change in a range — found by daily scanning
    then bisection to the second. Zones change rarely; the
    enumeration is short."""
    found = []
    cursor = start_utc
    previous = _offset_at(tz, cursor)
    while cursor < end_utc:
        nxt = min(cursor + timedelta(days=1), end_utc)
        current = _offset_at(tz, nxt)
        if current != previous:
            lo, hi = cursor, nxt
            while (hi - lo) > timedelta(seconds=1):
                mid = lo + (hi - lo) / 2
                if _offset_at(tz, mid) == previous:
                    lo = mid
                else:
                    hi = mid
            found.append((hi, previous, current))
            previous = current
        cursor = nxt
    return found


def _format_offset(offset: timedelta) -> str:
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    base = f"{sign}{hours:02d}{minutes:02d}"
    return base + (f"{seconds:02d}" if seconds else "")


def vtimezone_block(tz_name: str, first_year: int, last_year: int) -> list[str]:
    """A VTIMEZONE for one zone: one observance per actual
    transition across the years asked for (plus the zone's
    standing state at the range's start). Enumerated observances
    — not guessed RRULEs — so importers land on the same offsets
    our own expander computes."""
    tz = ZoneInfo(tz_name)
    start = datetime(first_year, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    end = datetime(last_year + 1, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    lines = ["BEGIN:VTIMEZONE", f"TZID:{tz_name}"]
    transitions = _transitions(tz, start, end)
    if not transitions:
        offset = _offset_at(tz, start)
        name = start.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%Z")
        lines += [
            "BEGIN:STANDARD",
            f"DTSTART:{_format_local(datetime(first_year, 1, 1))}",
            f"TZOFFSETFROM:{_format_offset(offset)}",
            f"TZOFFSETTO:{_format_offset(offset)}",
            f"TZNAME:{name}",
            "END:STANDARD",
        ]
    for instant, offset_from, offset_after in transitions:
        # DTSTART is the transition instant in the wall time that
        # was in force BEFORE the change: UTC instant + old offset.
        wall = instant + offset_from
        name = instant.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%Z")
        kind = "DAYLIGHT" if offset_after > offset_from else "STANDARD"
        lines += [
            f"BEGIN:{kind}",
            f"DTSTART:{_format_local(wall)}",
            f"TZOFFSETFROM:{_format_offset(offset_from)}",
            f"TZOFFSETTO:{_format_offset(offset_after)}",
            f"TZNAME:{name}",
            f"END:{kind}",
        ]
    lines.append("END:VTIMEZONE")
    return lines


# ------------------------------------------------------------------
# Serialising events
# ------------------------------------------------------------------


def _uid(event: Event) -> str:
    return f"{event.id}@{UID_DOMAIN}"


def _location(event: Event) -> str:
    parts = [p for p in (event.place_name, event.place_address) if p]
    return ", ".join(parts)


def _description(event: Event) -> str:
    """The description an outside calendar shows: the event's
    own words, plus the practical lines (cost, bring) folded in
    plainly — an import should read like the event page, not
    like a database row."""
    lines = []
    if event.description:
        lines.append(event.description)
    if event.cost_text:
        lines.append(f"Cost: {event.cost_text}")
    if event.bring_list:
        lines.append(f"Bring: {event.bring_list}")
    if event.host_label:
        lines.append(f"Host: {event.host_label}")
    return "\n".join(lines)


def _event_lines(event: Event, recurrence: Recurrence | None, stamp: datetime) -> list[str]:
    """One event → its VEVENT block(s): the master, and for a
    series one extra VEVENT per moved occurrence (RECURRENCE-ID
    names the original; the block itself carries the new time),
    with cancelled occurrences as EXDATE on the master."""
    tz = ZoneInfo(event.timezone)
    start_local = event.starts_at.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
    end_local = event.ends_at.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
    status = "CANCELLED" if event.status == "cancelled" else "CONFIRMED"
    lines = ["BEGIN:VEVENT", f"UID:{_uid(event)}", f"DTSTAMP:{_format_utc(stamp)}"]
    if event.all_day:
        lines.append(f"DTSTART;VALUE=DATE:{_format_date(start_local)}")
        lines.append(f"DTEND;VALUE=DATE:{_format_date(end_local)}")
    else:
        lines.append(f"DTSTART;TZID={event.timezone}:{_format_local(start_local)}")
        lines.append(f"DTEND;TZID={event.timezone}:{_format_local(end_local)}")
    exceptions = recurrence_service.load_exceptions(recurrence) if recurrence else []
    if recurrence is not None and recurrence.rrule:
        lines.append(f"RRULE:{recurrence.rrule}")
        cancelled = [
            e for e in exceptions if e.get("cancelled") and e.get("original_start")
        ]
        if cancelled and not event.all_day:
            rendered = ",".join(
                _format_local(datetime.fromisoformat(str(e["original_start"])))
                for e in cancelled
            )
            lines.append(f"EXDATE;TZID={event.timezone}:{rendered}")
    lines.append(f"SUMMARY:{escape_text(event.title)}")
    description = _description(event)
    if description:
        lines.append(f"DESCRIPTION:{escape_text(description)}")
    location = _location(event)
    if location:
        lines.append(f"LOCATION:{escape_text(location)}")
    lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    # Moved occurrences: their own VEVENTs under the same UID.
    duration = end_local - start_local
    for entry in exceptions:
        if entry.get("cancelled") or not entry.get("moved_to"):
            continue
        original = datetime.fromisoformat(str(entry["original_start"]))
        moved = datetime.fromisoformat(str(entry["moved_to"]))
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_uid(event)}",
            f"DTSTAMP:{_format_utc(stamp)}",
            f"RECURRENCE-ID;TZID={event.timezone}:{_format_local(original)}",
            f"DTSTART;TZID={event.timezone}:{_format_local(moved)}",
            f"DTEND;TZID={event.timezone}:{_format_local(moved + duration)}",
            f"SUMMARY:{escape_text(event.title)}",
            f"STATUS:{status}",
            "END:VEVENT",
        ]
    return lines


def render_calendar(
    entries: list[tuple[Event, Recurrence | None]],
    *,
    calendar_name: str,
    stamp: datetime | None = None,
    year_span: tuple[int, int] | None = None,
) -> str:
    """A whole VCALENDAR from (event, recurrence) pairs.

    Only events the caller selected reach this function — the
    draft bright line is enforced by the selectors (feeds and
    downloads query published/cancelled only), and cancelled
    events serialise AS cancelled here."""
    stamp = stamp or datetime.now(timezone.utc).replace(tzinfo=None)
    zones = sorted({event.timezone for event, _ in entries})
    years = set()
    for event, _ in entries:
        years.add(event.starts_at.year)
    first_year = (year_span[0] if year_span else (min(years) - 1 if years else stamp.year - 1))
    last_year = (year_span[1] if year_span else (max(years) + 2 if years else stamp.year + 2))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(calendar_name)}",
    ]
    for zone_name in zones:
        lines += vtimezone_block(zone_name, first_year, last_year)
    for event, recurrence in entries:
        lines += _event_lines(event, recurrence, stamp)
    lines.append("END:VCALENDAR")
    return _join(lines)


def render_event_ics(event: Event, recurrence: Recurrence | None = None) -> str:
    """One event as a downloadable .ics (B-053)."""
    return render_calendar(
        [(event, recurrence)], calendar_name=event.title or "Event"
    )


# ------------------------------------------------------------------
# Parsing (the round-trip proof + B-053's import check)
# ------------------------------------------------------------------


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw.strip():
            lines.append(raw)
    return lines


def _parse_property(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    parts = head.split(";")
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        params[key.upper()] = val
    return parts[0].upper(), params, value


def _parse_datetime(value: str, params: dict[str, str]) -> dict:
    """One DTSTART-shaped value → {kind, ...}: utc instant, or
    local wall time + its TZID, or a plain date (all-day)."""
    if params.get("VALUE") == "DATE" or (len(value) == 8 and "T" not in value):
        return {"kind": "date", "date": datetime.strptime(value, "%Y%m%d").date()}
    if value.endswith("Z"):
        instant = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        return {"kind": "utc", "utc": instant}
    local = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return {"kind": "local", "local": local, "tzid": params.get("TZID", "UTC")}


def parse_calendar(text: str) -> list[dict]:
    """Parse a VCALENDAR into plain dicts — enough of RFC 5545
    to round-trip our own output and to verify an import-shaped
    feed: UID, SUMMARY, STATUS, DTSTART/DTEND (typed), RRULE,
    EXDATEs, RECURRENCE-ID."""
    events: list[dict] = []
    current: dict | None = None
    for line in _unfold(text):
        name, params, value = _parse_property(line)
        if name == "BEGIN" and value == "VEVENT":
            current = {"exdates": []}
        elif name == "END" and value == "VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None:
            if name == "UID":
                current["uid"] = value
            elif name == "SUMMARY":
                current["summary"] = unescape_text(value)
            elif name == "STATUS":
                current["status"] = value
            elif name == "DTSTART":
                current["dtstart"] = _parse_datetime(value, params)
            elif name == "DTEND":
                current["dtend"] = _parse_datetime(value, params)
            elif name == "RECURRENCE-ID":
                current["recurrence_id"] = _parse_datetime(value, params)
            elif name == "RRULE":
                current["rrule"] = value
            elif name == "EXDATE":
                for piece in value.split(","):
                    current["exdates"].append(_parse_datetime(piece, params))
    return events


def expand_parsed(parsed: dict, window_start_utc: datetime, window_end_utc: datetime):
    """Expand a parsed VEVENT with our own recurrence engine —
    the import check: a feed parsed back must expand to the same
    occurrences the agenda computes from the database."""
    start = parsed["dtstart"]
    end = parsed.get("dtend") or parsed["dtstart"]
    if start["kind"] == "local":
        tz = ZoneInfo(start["tzid"])
        starts_utc = start["local"].replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        ends_utc = end["local"].replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        tz_name = start["tzid"]
    elif start["kind"] == "utc":
        starts_utc, ends_utc, tz_name = start["utc"], end["utc"], "UTC"
    else:  # all-day: midnight in UTC is the stored convention here
        starts_utc = datetime.combine(start["date"], time(0, 0))
        ends_utc = datetime.combine(end["date"], time(0, 0))
        tz_name = "UTC"
    exceptions = []
    for exdate in parsed.get("exdates", []):
        if exdate["kind"] == "local":
            exceptions.append(
                {"original_start": exdate["local"].isoformat(),
                 "moved_to": None, "cancelled": True}
            )
    return recurrence_service.expand(
        starts_at_utc=starts_utc, ends_at_utc=ends_utc, tz_name=tz_name,
        rrule_text=parsed.get("rrule"),
        recurrence=None,
        window_start_utc=window_start_utc, window_end_utc=window_end_utc,
    ), exceptions


# ------------------------------------------------------------------
# The per-group secret feed (B-053) + links (B-054)
# ------------------------------------------------------------------


def issue_feed(
    db: Session, *, actor_label: str, group: Group, filters: dict | None = None
) -> tuple[CalendarFeed, str]:
    """Issue a feed: the raw token is returned ONCE and stored
    only as a hash — the same discipline as API keys (B-014)."""
    import json

    raw = secrets.token_urlsafe(32)
    feed = CalendarFeed(
        group_id=group.id,
        token_hash=hash_secret(raw),
        token_prefix=raw[:8],
        filters_json=json.dumps(filters or {}),
        created_by=actor_label,
    )
    db.add(feed)
    db.flush()
    audit.record(
        db, actor=actor_label, action="calendar_feed.issued",
        group_id=group.id, entity_type="calendar_feed", entity_id=feed.id,
    )
    db.commit()
    return feed, raw


def revoke_feed(db: Session, *, actor_label: str, group: Group, feed_id: str) -> CalendarFeed:
    """Revoke a feed: instant and total — the fetch route checks
    ``revoked_at`` on every request."""
    from gpa.models.identity import now_utc

    feed = db.get(CalendarFeed, feed_id)
    if feed is None or feed.group_id != group.id:
        raise AppError("not_found", detail="That calendar feed could not be found.")
    if feed.revoked_at is None:
        feed.revoked_at = now_utc()
        audit.record(
            db, actor=actor_label, action="calendar_feed.revoked",
            group_id=group.id, entity_type="calendar_feed", entity_id=feed.id,
        )
        db.commit()
    return feed


def list_feeds(db: Session, group: Group) -> list[CalendarFeed]:
    """A group's feeds (prefixes only — the raw token is gone
    by design the moment issuing returns)."""
    return (
        db.query(CalendarFeed)
        .filter(CalendarFeed.group_id == group.id)
        .order_by(CalendarFeed.created_at)
        .all()
    )


def resolve_feed(db: Session, raw_token: str) -> CalendarFeed | None:
    """A presented token → its live feed row, or None (unknown OR
    revoked: the fetch route answers both with the same 404, so
    a revoked token teaches an attacker nothing)."""
    feed = (
        db.query(CalendarFeed)
        .filter(CalendarFeed.token_hash == hash_secret(raw_token))
        .one_or_none()
    )
    if feed is None or feed.revoked_at is not None:
        return None
    return feed


def feed_events(db: Session, feed: CalendarFeed) -> list[tuple[Event, Recurrence | None]]:
    """The events a feed carries: the group's published +
    cancelled (drafts NEVER — the bright line), narrowed by the
    feed's own filters (statuses, a place word, a sub-group)."""
    import json

    try:
        filters = json.loads(feed.filters_json or "{}")
    except json.JSONDecodeError:
        filters = {}
    rows = (
        db.query(Event)
        .filter(
            Event.group_id == feed.group_id,
            Event.status.in_(("published", "cancelled")),
            # B-080: deleted events leave every feed until restored.
            Event.deleted_at.is_(None),
        )
        .order_by(Event.starts_at, Event.id)
        .all()
    )
    out: list[tuple[Event, Recurrence | None]] = []
    statuses = filters.get("statuses")
    place_word = str(filters.get("place_contains") or "").lower()
    sub_group = filters.get("sub_group")
    for event in rows:
        if statuses and event.status not in statuses:
            continue
        if place_word and place_word not in event.place_name.lower():
            continue
        if sub_group and event.sub_group != sub_group:
            continue
        recurrence = recurrence_service.get_recurrence(db, event.id)
        out.append((event, recurrence))
    return out


def render_feed(db: Session, feed: CalendarFeed, group: Group) -> str:
    """The feed's .ics body for one fetch."""
    return render_calendar(
        feed_events(db, feed), calendar_name=f"{group.name} — Group Planning Assistant"
    )


def event_links(event: Event, *, feed_url: str | None = None) -> dict:
    """The outbound calendar links for one event (B-054),
    rendered server-side so every client shows the same correct
    times: a Google "add to calendar" URL (UTC instants, which
    Google renders in the viewer's zone) and, when a feed URL is
    known, the webcal subscription form of it."""
    if event.all_day:
        dates = f"{event.starts_at.strftime('%Y%m%d')}/{event.ends_at.strftime('%Y%m%d')}"
    else:
        dates = f"{_format_utc(event.starts_at)}/{_format_utc(event.ends_at)}"
    google = (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={quote(event.title)}"
        f"&dates={dates}"
        f"&details={quote(_description(event))}"
        f"&location={quote(_location(event))}"
    )
    links = {"google_calendar": google}
    if feed_url:
        links["webcal"] = "webcal://" + feed_url.split("://", 1)[-1]
        links["ics_feed"] = feed_url
    return links


def feed_url_for(base_url: str, raw_token: str) -> str:
    """The fetch URL of a feed token under an API base."""
    return f"{base_url.rstrip('/')}/api/v1/calendar/feed/{raw_token}.ics"


def token_fingerprint(raw_token: str) -> str:
    """A short non-secret fingerprint for logs (never the token)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()[:8]
