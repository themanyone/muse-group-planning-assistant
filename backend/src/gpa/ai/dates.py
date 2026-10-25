"""The date resolver (roadmap B-039, decision D-22).

Relative date phrases — "this Saturday", "next Friday", "the
4th", "tomorrow week" — are resolved HERE, by deterministic
rules, never by a model and never by a prompt side-effect. The
model proposes raw phrases; this module owns their meaning, so
"this Saturday" means the same thing in the demo, in the golden
suite, and in production (Doc 07 §4).

The rules, stated once (the golden suite pins every one):

- Resolution is against the message's *sent date in the group's
  timezone* (a stored UTC instant converted), or against an
  explicit ``anchor`` date when the caller supplies one.
- ``today`` / ``tomorrow`` / ``yesterday`` / ``day after
  tomorrow`` are the obvious offsets. ``tomorrow week`` is a week
  from tomorrow (+8). ``next week`` is genuinely two readings —
  the same weekday next week, or next week's Monday — and returns
  both, flagged.
- ``this <weekday>`` is that weekday of the current (Mon–Sun)
  week. If it is still upcoming (today counts), that is the one
  reading. If it has already passed this week, BOTH readings are
  returned flagged ambiguous — the speaker may mean the one just
  gone (a recap) or the one coming (a plan), and a silent guess
  is the one outcome this module is forbidden to produce.
- ``next <weekday>`` and ``<weekday> week`` are that weekday of
  next week. A bare ``<weekday>`` (or ``on <weekday>``) is the
  upcoming one, today counting.
- ``the 4th`` rolls forward: the soonest future date with that
  day-of-month, skipping months that lack it (the 31st in
  February lands on March 31).
- Named dates (``6 November``, ``November 6th``, ``6 Nov 2027``)
  take their stated year, or roll to next year when this year's
  has passed. ISO dates are exact. Slash dates (``06/11/2026``)
  return both the day-first and month-first readings when both
  are real dates — flagged — because the source's locale is not
  ours to presume.
- Anything unrecognised returns ``matched=False`` with no
  readings. An unresolved date is information, not an error.

``parse_time_phrase`` is the smaller sibling for times of day:
explicit forms (``7pm``, ``6.30pm``, ``19:00``, ``noon``) resolve
exactly; a bare hour ("doors 7") is honestly ambiguous between
morning and evening and returns both readings.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_WEEKDAY_ALT = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))


@dataclass
class DateReading:
    """One possible meaning of a phrase: a date + the rule that
    produced it + whether it already lies in the past."""

    date: date
    rule: str
    past: bool = False


@dataclass
class DateResolution:
    """A phrase's full resolution. ``readings`` empty +
    ``matched=False`` means the module does not know this phrase —
    stated plainly, never papered over with a guess."""

    phrase: str
    readings: list[DateReading] = field(default_factory=list)
    ambiguous: bool = False
    matched: bool = False


@dataclass
class TimeReading:
    """One possible time of day + the rule that produced it."""

    time: time
    rule: str


@dataclass
class TimeResolution:
    """A time phrase's resolution (bare hours are ambiguous)."""

    phrase: str
    readings: list[TimeReading] = field(default_factory=list)
    ambiguous: bool = False
    matched: bool = False


def _base_date(sent_at: datetime | None, group_tz: str, anchor: date | None) -> date:
    """The local date phrases are relative TO: the anchor when one
    is given, else the sent instant's date in the group's timezone
    (stored instants are naive UTC — D-21)."""
    if anchor is not None:
        return anchor
    if sent_at is None:
        return datetime.now(ZoneInfo(group_tz)).date()
    instant = sent_at if sent_at.tzinfo is not None else sent_at.replace(tzinfo=timezone.utc)
    return instant.astimezone(ZoneInfo(group_tz)).date()


def _reading(base: date, value: date, rule: str) -> DateReading:
    return DateReading(date=value, rule=rule, past=value < base)


def _monday_of(d: date) -> date:
    """The Monday of ``d``'s (Mon–Sun) week."""
    return d - timedelta(days=d.weekday())


def _resolve_weekday(prefix: str | None, weekday: int, week_suffix: bool, base: date, phrase: str) -> DateResolution:
    """The weekday rules (see the module docstring)."""
    monday = _monday_of(base)
    if week_suffix or prefix == "next":
        rule = "weekday-week" if week_suffix and prefix is None else "next-weekday"
        return DateResolution(
            phrase=phrase, matched=True,
            readings=[_reading(base, monday + timedelta(days=7 + weekday), rule)],
        )
    if prefix == "this":
        candidate = monday + timedelta(days=weekday)
        if candidate >= base:
            return DateResolution(
                phrase=phrase, matched=True,
                readings=[_reading(base, candidate, "this-weekday")],
            )
        # Already passed this week: both readings, flagged —
        # the module does not guess between recap and plan.
        return DateResolution(
            phrase=phrase, matched=True, ambiguous=True,
            readings=[
                _reading(base, candidate, "this-weekday-passed"),
                _reading(base, candidate + timedelta(days=7), "this-weekday-passed"),
            ],
        )
    delta = (weekday - base.weekday()) % 7
    return DateResolution(
        phrase=phrase, matched=True,
        readings=[_reading(base, base + timedelta(days=delta), "upcoming-weekday")],
    )


def _resolve_ordinal(day: int, base: date, phrase: str) -> DateResolution:
    """``the 4th``: roll forward to the soonest month that has the
    day, on or after the base date."""
    year, month = base.year, base.month
    for _ in range(24):  # two years of months is more than enough
        try:
            candidate = date(year, month, day)
        except ValueError:
            candidate = None  # e.g. the 31st in February — skip it
        if candidate is not None and candidate >= base:
            return DateResolution(
                phrase=phrase, matched=True,
                readings=[_reading(base, candidate, "ordinal-roll-forward")],
            )
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return DateResolution(phrase=phrase)  # pragma: no cover — unreachable for 1..31


def _resolve_named(day: int, month: int, year: int | None, base: date, phrase: str) -> DateResolution:
    """``6 November`` / ``November 6th`` / with an explicit year."""
    try:
        if year is not None:
            value = date(year, month, day)
            return DateResolution(
                phrase=phrase, matched=True,
                readings=[_reading(base, value, "named-date-explicit-year")],
            )
        value = date(base.year, month, day)
        if value < base:
            value = date(base.year + 1, month, day)
        return DateResolution(
            phrase=phrase, matched=True,
            readings=[_reading(base, value, "named-date")],
        )
    except ValueError:
        return DateResolution(phrase=phrase)  # e.g. "31 February" — no such date


def resolve_date_phrase(
    phrase: str,
    *,
    sent_at: datetime | None = None,
    group_tz: str = "UTC",
    anchor: date | None = None,
) -> DateResolution:
    """Resolve one raw date phrase to its reading(s).

    Deterministic: same phrase + same context = same answer,
    forever. Ambiguity is a result, not an error.
    """
    base = _base_date(sent_at, group_tz, anchor)
    text = " ".join(phrase.strip().lower().strip(".,!").split())
    text = re.sub(r"^on\s+", "", text)

    # Fixed phrases first — exact matches, no pattern ambiguity.
    fixed: dict[str, list[tuple[date, str]]] = {
        "today": [(base, "today")],
        "tonight": [(base, "today")],
        "tomorrow": [(base + timedelta(days=1), "tomorrow")],
        "yesterday": [(base - timedelta(days=1), "yesterday")],
        "day after tomorrow": [(base + timedelta(days=2), "day-after-tomorrow")],
        "tomorrow week": [(base + timedelta(days=8), "tomorrow-week")],
    }
    if text in fixed:
        return DateResolution(
            phrase=phrase, matched=True,
            readings=[_reading(base, d, rule) for d, rule in fixed[text]],
        )
    if text == "next week":
        monday_next = _monday_of(base) + timedelta(days=7)
        return DateResolution(
            phrase=phrase, matched=True, ambiguous=True,
            readings=[
                _reading(base, monday_next, "next-week-monday"),
                _reading(base, base + timedelta(days=7), "next-week-same-weekday"),
            ],
        )

    # ISO calendar date — exact, single reading.
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            value = date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return DateResolution(phrase=phrase)
        return DateResolution(
            phrase=phrase, matched=True, readings=[_reading(base, value, "iso-date")]
        )

    # Slash date — both readings when both are real (flagged).
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        first, second, year = int(match[1]), int(match[2]), int(match[3])
        readings: list[DateReading] = []
        for day, month, rule in ((first, second, "slash-day-first"), (second, first, "slash-month-first")):
            try:
                value = date(year, month, day)
            except ValueError:
                continue
            if all(r.date != value for r in readings):
                readings.append(_reading(base, value, rule))
        if readings:
            return DateResolution(
                phrase=phrase, matched=True,
                readings=readings, ambiguous=len(readings) > 1,
            )
        return DateResolution(phrase=phrase)

    # Named-month dates: "6 november", "the 4th of november",
    # "november 6th", each with an optional explicit year.
    match = re.fullmatch(
        rf"(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_ALT})(?:\s+(\d{{4}}))?",
        text,
    )
    if match:
        return _resolve_named(
            int(match[1]), _MONTHS[match[2]],
            int(match[3]) if match[3] else None, base, phrase,
        )
    match = re.fullmatch(
        rf"({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?",
        text,
    )
    if match:
        return _resolve_named(
            int(match[2]), _MONTHS[match[1]],
            int(match[3]) if match[3] else None, base, phrase,
        )

    # Bare ordinal: "the 4th".
    match = re.fullmatch(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)", text)
    if match and 1 <= int(match[1]) <= 31:
        return _resolve_ordinal(int(match[1]), base, phrase)

    # End of the month.
    if text in ("end of the month", "end of month", "month end"):
        if base.month == 12:
            value = date(base.year, 12, 31)
        else:
            value = date(base.year, base.month + 1, 1) - timedelta(days=1)
        return DateResolution(
            phrase=phrase, matched=True,
            readings=[_reading(base, value, "end-of-month")],
        )

    # Weekday phrases: "this saturday", "next friday", "saturday",
    # "friday week".
    match = re.fullmatch(
        rf"(?:(this|next)\s+)?({_WEEKDAY_ALT})(\s+week)?", text
    )
    if match:
        return _resolve_weekday(
            match[1], _WEEKDAYS[match[2]], bool(match[3]), base, phrase
        )

    return DateResolution(phrase=phrase)


def parse_time_phrase(phrase: str) -> TimeResolution:
    """Resolve one time-of-day phrase.

    Explicit forms are exact. A bare hour ("doors 7", "at 7") is
    ambiguous morning/evening and returns both readings — the
    resolver flags it; the queue lets a person choose (the same
    honesty rule as dates).
    """
    text = " ".join(phrase.strip().lower().strip(".,!").split())
    if text == "noon":
        return TimeResolution(phrase=phrase, matched=True,
                              readings=[TimeReading(time(12, 0), "noon")])
    if text == "midnight":
        return TimeResolution(phrase=phrase, matched=True,
                              readings=[TimeReading(time(0, 0), "midnight")])

    # HH:MM with optional am/pm — an exact written time.
    match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?\b", text)
    if match:
        hour, minute = int(match[1]), int(match[2])
        if match[3]:
            hour = _to_24h(hour, match[3])
        if hour <= 23 and minute <= 59:
            return TimeResolution(
                phrase=phrase, matched=True,
                readings=[TimeReading(time(hour, minute), "clock-time")],
            )
        return TimeResolution(phrase=phrase)

    # H.MM with am/pm ("6.30pm"), or H(am|pm) ("7pm").
    match = re.search(r"(\d{1,2})[.:](\d{2})\s*(am|pm)\b", text)
    if match:
        hour = _to_24h(int(match[1]), match[3])
        minute = int(match[2])
        return TimeResolution(
            phrase=phrase, matched=True,
            readings=[TimeReading(time(hour, minute), "am-pm")],
        )
    match = re.search(r"(\d{1,2})\s*(am|pm)\b", text)
    if match:
        hour = _to_24h(int(match[1]), match[2])
        minute = 0
        return TimeResolution(
            phrase=phrase, matched=True,
            readings=[TimeReading(time(hour, minute), "am-pm")],
        )

    # A bare hour, possibly inside words ("doors 7"): ambiguous.
    numbers = re.findall(r"\b(\d{1,2})\b", text)
    if numbers:
        hour = int(numbers[-1])
        if 1 <= hour <= 12:
            return TimeResolution(
                phrase=phrase, matched=True, ambiguous=True,
                readings=[
                    TimeReading(time(hour, 0), "bare-hour-morning"),
                    TimeReading(time(hour + 12, 0), "bare-hour-evening"),
                ],
            )
        if 13 <= hour <= 23:
            return TimeResolution(
                phrase=phrase, matched=True,
                readings=[TimeReading(time(hour, 0), "bare-hour-24h")],
            )
    return TimeResolution(phrase=phrase)


def _to_24h(hour: int, meridiem: str) -> int:
    """12-hour clock to 24-hour: 12am -> 0, 12pm -> 12."""
    if meridiem == "am":
        return 0 if hour == 12 else hour
    return 12 if hour == 12 else hour + 12
