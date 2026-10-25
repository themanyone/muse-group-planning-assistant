"""Date resolver golden suite (roadmap B-039, decision D-22).

Two hundred-plus pinned cases. The bulk is a literal week table —
the calendar facts for Mon 2026-10-19 → Sun 2026-11-01 are written
out as dates, and every weekday phrase is checked from every
sending day of the first week — followed by hand-listed boundary
cases: month and year turns, DST weeks in two hemispheres'
calendars, timezone conversions that move the local date,
ordinals that skip short months, slash-date ambiguity, and the
two demo-critical lines from the roadmap itself:

    "this Saturday" sent Wed 2026-10-21 -> Saturday 2026-10-24
    "this Saturday" sent 2026-10-28     -> Saturday 2026-10-31

The suite's law is the module's: ambiguity is returned flagged,
never guessed away; unknown phrases match nothing.
"""

from datetime import date, datetime, time, timedelta

import pytest

from gpa.ai.dates import parse_time_phrase, resolve_date_phrase

WEEK_A = [date(2026, 10, 19) + timedelta(days=i) for i in range(7)]  # Mon..Sun
WEEK_B = [date(2026, 10, 26) + timedelta(days=i) for i in range(7)]  # Mon..Sun
NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _week_cases():
    """Every weekday phrase from every sending day of Week A."""
    cases = []
    for sent in WEEK_A:
        sent_dt = datetime(sent.year, sent.month, sent.day, 12, 0)
        for w, name in enumerate(NAMES):
            a, b = WEEK_A[w], WEEK_B[w]
            cases.append((f"this {name}", sent_dt, "UTC", None,
                          [a] if a >= sent else [a, b]))
            cases.append((f"next {name}", sent_dt, "UTC", None, [b]))
            cases.append((name, sent_dt, "UTC", None,
                          [a] if a >= sent else [b]))
            cases.append((f"on {name}", sent_dt, "UTC", None,
                          [a] if a >= sent else [b]))
    return cases


# (phrase, sent_at, group_tz, anchor, expected reading dates)
MISC_CASES = [
    # The roadmap's two demo-critical lines.
    ("this Saturday", datetime(2026, 10, 21, 19, 2), "America/New_York", None, [date(2026, 10, 24)]),
    ("this Saturday", datetime(2026, 10, 28, 9, 0), "UTC", None, [date(2026, 10, 31)]),
    # Fixed offsets, incl. month and year boundaries.
    ("today", datetime(2026, 10, 31, 12), "UTC", None, [date(2026, 10, 31)]),
    ("tomorrow", datetime(2026, 10, 31, 12), "UTC", None, [date(2026, 11, 1)]),
    ("tomorrow", datetime(2026, 12, 31, 12), "UTC", None, [date(2027, 1, 1)]),
    ("yesterday", datetime(2027, 1, 1, 12), "UTC", None, [date(2026, 12, 31)]),
    ("day after tomorrow", datetime(2026, 10, 30, 12), "UTC", None, [date(2026, 11, 1)]),
    ("tomorrow week", datetime(2026, 10, 21, 12), "UTC", None, [date(2026, 10, 29)]),
    ("tomorrow week", datetime(2026, 12, 28, 12), "UTC", None, [date(2027, 1, 5)]),
    ("next week", datetime(2026, 10, 21, 12), "UTC", None, [date(2026, 10, 26), date(2026, 10, 28)]),
    ("next week", datetime(2026, 10, 25, 12), "UTC", None, [date(2026, 10, 26), date(2026, 11, 1)]),
    # Ordinals roll forward, skipping months that lack the day.
    ("the 4th", datetime(2026, 10, 2, 12), "UTC", None, [date(2026, 10, 4)]),
    ("the 4th", datetime(2026, 10, 4, 12), "UTC", None, [date(2026, 10, 4)]),
    ("the 4th", datetime(2026, 10, 21, 12), "UTC", None, [date(2026, 11, 4)]),
    ("4th", datetime(2026, 10, 21, 12), "UTC", None, [date(2026, 11, 4)]),
    ("the 1st", datetime(2026, 12, 31, 12), "UTC", None, [date(2027, 1, 1)]),
    ("the 31st", datetime(2027, 1, 30, 12), "UTC", None, [date(2027, 1, 31)]),
    ("the 31st", datetime(2027, 2, 10, 12), "UTC", None, [date(2027, 3, 31)]),
    ("the 29th", datetime(2027, 2, 10, 12), "UTC", None, [date(2027, 3, 29)]),
    ("the 15th", datetime(2026, 10, 15, 12), "UTC", None, [date(2026, 10, 15)]),
    # Named-month dates, both orders, optional years.
    ("6 November", datetime(2026, 10, 22, 12), "UTC", None, [date(2026, 11, 6)]),
    ("November 6", datetime(2026, 10, 22, 12), "UTC", None, [date(2026, 11, 6)]),
    ("Nov 6th", datetime(2026, 10, 22, 12), "UTC", None, [date(2026, 11, 6)]),
    ("the 6th of November", datetime(2026, 10, 22, 12), "UTC", None, [date(2026, 11, 6)]),
    ("6 Nov 2027", datetime(2026, 10, 22, 12), "UTC", None, [date(2027, 11, 6)]),
    ("25 December", datetime(2026, 12, 26, 12), "UTC", None, [date(2027, 12, 25)]),
    ("1 January", datetime(2026, 12, 30, 12), "UTC", None, [date(2027, 1, 1)]),
    ("January 1", datetime(2027, 1, 1, 12), "UTC", None, [date(2027, 1, 1)]),
    ("31 February", datetime(2026, 10, 1, 12), "UTC", None, []),
    # ISO dates are exact; impossible ones match nothing.
    ("2026-11-06", datetime(2026, 10, 1, 12), "UTC", None, [date(2026, 11, 6)]),
    ("2026-02-31", datetime(2026, 10, 1, 12), "UTC", None, []),
    # Slash dates: both readings when both are real.
    ("06/11/2026", datetime(2026, 10, 1, 12), "UTC", None, [date(2026, 11, 6), date(2026, 6, 11)]),
    ("25/12/2026", datetime(2026, 10, 1, 12), "UTC", None, [date(2026, 12, 25)]),
    ("12/25/2026", datetime(2026, 10, 1, 12), "UTC", None, [date(2026, 12, 25)]),
    # Weekday + "week" suffix is next week's weekday.
    ("Friday week", datetime(2026, 10, 21, 12), "UTC", None, [date(2026, 10, 30)]),
    ("Saturday week", datetime(2026, 10, 24, 12), "UTC", None, [date(2026, 10, 31)]),
    # End of the month.
    ("end of the month", datetime(2026, 10, 21, 12), "UTC", None, [date(2026, 10, 31)]),
    ("end of the month", datetime(2027, 2, 10, 12), "UTC", None, [date(2027, 2, 28)]),
    ("end of the month", datetime(2026, 12, 15, 12), "UTC", None, [date(2026, 12, 31)]),
    # Timezone conversion moves the local "sent" date.
    ("today", datetime(2026, 10, 24, 2, 30), "America/New_York", None, [date(2026, 10, 23)]),
    ("this Saturday", datetime(2026, 10, 24, 2, 30), "America/New_York", None, [date(2026, 10, 24)]),
    ("today", datetime(2026, 10, 24, 4, 30), "America/New_York", None, [date(2026, 10, 24)]),
    ("today", datetime(2026, 10, 24, 23, 30), "Europe/London", None, [date(2026, 10, 25)]),
    ("tomorrow", datetime(2026, 10, 24, 23, 30), "Europe/London", None, [date(2026, 10, 26)]),
    # DST weeks: pure date arithmetic in local time, clocks aside.
    ("today", datetime(2026, 11, 1, 5, 30), "America/New_York", None, [date(2026, 11, 1)]),
    ("this Sunday", datetime(2026, 11, 1, 5, 30), "America/New_York", None, [date(2026, 11, 1)]),
    ("tomorrow", datetime(2026, 3, 7, 12), "America/New_York", None, [date(2026, 3, 8)]),
    ("tomorrow", datetime(2026, 10, 31, 12), "Australia/Sydney", None, [date(2026, 11, 1)]),
    # An explicit anchor replaces the sent date entirely.
    ("this Saturday", datetime(2026, 1, 1, 12), "UTC", date(2026, 12, 2), [date(2026, 12, 5)]),
    ("tomorrow", datetime(2026, 1, 1, 12), "UTC", date(2027, 1, 31), [date(2027, 2, 1)]),
    ("the 4th", datetime(2026, 1, 1, 12), "UTC", date(2026, 10, 21), [date(2026, 11, 4)]),
    # Unknown phrases match nothing — honestly.
    ("whenever lol", datetime(2026, 10, 21, 12), "UTC", None, []),
    ("gobbledygook", datetime(2026, 10, 21, 12), "UTC", None, []),
    ("next month", datetime(2026, 10, 21, 12), "UTC", None, []),
    ("", datetime(2026, 10, 21, 12), "UTC", None, []),
]

ALL_CASES = _week_cases() + MISC_CASES


def test_golden_suite_is_really_200_cases():
    """B-039's acceptance names 200 cases — count them, don't
    take the file's word for it."""
    assert len(ALL_CASES) >= 200


@pytest.mark.parametrize(
    "phrase,sent_at,tz,anchor,expected",
    ALL_CASES,
    ids=[f"{c[0]!r}@{c[1].date()}#{i}" for i, c in enumerate(ALL_CASES)],
)
def test_date_phrase_golden(phrase, sent_at, tz, anchor, expected):
    resolution = resolve_date_phrase(phrase, sent_at=sent_at, group_tz=tz, anchor=anchor)
    assert resolution.matched is bool(expected)
    assert [r.date for r in resolution.readings] == expected
    # Every multi-reading answer is flagged; no single reading is.
    assert resolution.ambiguous is (len(expected) > 1)


def test_passed_this_weekday_readings_carry_past_flags():
    """Sunday speaking of "this Saturday": the just-gone Saturday
    is marked past, the coming one is not — the queue shows both
    and a human picks."""
    resolution = resolve_date_phrase(
        "this Saturday", sent_at=datetime(2026, 10, 25, 12), group_tz="UTC"
    )
    assert [r.date for r in resolution.readings] == [date(2026, 10, 24), date(2026, 10, 31)]
    assert [r.past for r in resolution.readings] == [True, False]


def test_resolution_is_deterministic():
    """Same phrase + same context = same answer, every call."""
    kwargs = dict(sent_at=datetime(2026, 10, 21, 19, 2), group_tz="America/New_York")
    first = resolve_date_phrase("this Saturday", **kwargs)
    second = resolve_date_phrase("this Saturday", **kwargs)
    assert first == second


TIME_CASES = [
    ("7pm", [time(19, 0)]),
    ("7 pm", [time(19, 0)]),
    ("6.30pm", [time(18, 30)]),
    ("6:30 pm", [time(18, 30)]),
    ("19:00", [time(19, 0)]),
    ("12am", [time(0, 0)]),
    ("12pm", [time(12, 0)]),
    ("noon", [time(12, 0)]),
    ("midnight", [time(0, 0)]),
    ("doors 7", [time(7, 0), time(19, 0)]),
    ("at 7", [time(7, 0), time(19, 0)]),
    ("7", [time(7, 0), time(19, 0)]),
    ("18", [time(18, 0)]),
    ("half past seven", []),
]


@pytest.mark.parametrize("phrase,expected", TIME_CASES)
def test_time_phrase_golden(phrase, expected):
    resolution = parse_time_phrase(phrase)
    assert resolution.matched is bool(expected)
    assert [r.time for r in resolution.readings] == expected
    assert resolution.ambiguous is (len(expected) > 1)
