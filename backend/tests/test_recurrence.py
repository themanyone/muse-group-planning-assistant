"""Recurrence (roadmap B-052).

The trap this slice exists to disarm: a weekly series over a DST
change must keep its LOCAL wall time — 19:00 on the wall in EDT
and in EST alike — while its UTC instant moves. Plus the rule
grammar (COUNT / UNTIL / BYDAY ordinals / month-end), exceptions
as data (this week's meeting moved), and the API law: readers
get occurrences, never rules to expand themselves.
"""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import bearer, make_group, register
from gpa.services import recurrence as recurrence_service

NY = "America/New_York"


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute)


def expand_local(starts_utc, ends_utc, rrule, exceptions=None, window_days=120):
    """Service-level expansion over a generous window."""
    recurrence = None
    if exceptions is not None:
        import json

        class _Row:  # the expander reads only exceptions_json
            exceptions_json = json.dumps(exceptions)

        recurrence = _Row()
    return recurrence_service.expand(
        starts_at_utc=starts_utc, ends_at_utc=ends_utc, tz_name=NY,
        rrule_text=rrule, recurrence=recurrence,
        window_start_utc=starts_utc - timedelta(days=1),
        window_end_utc=starts_utc + timedelta(days=window_days),
    )


def test_weekly_series_keeps_wall_time_across_dst():
    # Wed 2026-10-28 19:00 EDT (UTC-4) = 23:00Z. DST ends Nov 1.
    occs = expand_local(utc(2026, 10, 28, 23), utc(2026, 10, 29, 1),
                         "FREQ=WEEKLY;COUNT=3")
    assert [o.start_local for o in occs] == [
        datetime(2026, 10, 28, 19, 0),
        datetime(2026, 11, 4, 19, 0),   # same wall time…
        datetime(2026, 11, 11, 19, 0),
    ]
    assert [o.starts_at for o in occs] == [
        utc(2026, 10, 28, 23),          # …different UTC instants
        utc(2026, 11, 5, 0),
        utc(2026, 11, 12, 0),
    ]


def test_daily_count_and_until():
    occs = expand_local(utc(2026, 11, 2, 14), utc(2026, 11, 2, 15),
                        "FREQ=DAILY;COUNT=4")
    assert len(occs) == 4
    occs = expand_local(utc(2026, 11, 2, 14), utc(2026, 11, 2, 15),
                        "FREQ=DAILY;UNTIL=20261104")
    assert [o.start_local.date().isoformat() for o in occs] == [
        "2026-11-02", "2026-11-03", "2026-11-04",
    ]


def test_monthly_ordinal_weekday_and_month_end():
    # Second Tuesday, from Tue 2026-10-13 18:00 local (22:00Z).
    occs = expand_local(utc(2026, 10, 13, 22), utc(2026, 10, 13, 23),
                        "FREQ=MONTHLY;BYDAY=2TU;COUNT=3")
    assert [o.start_local.date().isoformat() for o in occs] == [
        "2026-10-13", "2026-11-10", "2026-12-08",
    ]
    # Last day of the month, from Jan 31 2027 10:00 local (15:00Z).
    occs = expand_local(utc(2027, 1, 31, 15), utc(2027, 1, 31, 16),
                        "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3")
    assert [o.start_local.date().isoformat() for o in occs] == [
        "2027-01-31", "2027-02-28", "2027-03-31",
    ]


def test_weekly_byday_multiple_days():
    # Mon+Wed yoga from Mon 2026-11-02 07:00 EST (12:00Z).
    occs = expand_local(utc(2026, 11, 2, 12), utc(2026, 11, 2, 13),
                        "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4")
    assert [o.start_local.date().isoformat() for o in occs] == [
        "2026-11-02", "2026-11-04", "2026-11-09", "2026-11-11",
    ]


def test_exceptions_move_and_cancel():
    exceptions = [
        {"original_start": "2026-11-04T19:00:00",
         "moved_to": "2026-11-05T20:00:00", "cancelled": False},
        {"original_start": "2026-11-11T19:00:00",
         "moved_to": None, "cancelled": True},
    ]
    occs = expand_local(utc(2026, 10, 28, 23), utc(2026, 10, 29, 1),
                        "FREQ=WEEKLY;COUNT=3", exceptions=exceptions)
    by_original = {o.original_start_local: o for o in occs}
    moved = by_original[datetime(2026, 11, 4, 19, 0)]
    assert moved.is_exception and not moved.cancelled
    assert moved.start_local == datetime(2026, 11, 5, 20, 0)
    cancelled = by_original[datetime(2026, 11, 11, 19, 0)]
    assert cancelled.is_exception and cancelled.cancelled
    assert cancelled.start_local == datetime(2026, 11, 11, 19, 0)


def test_unexpandable_rules_are_refused_in_plain_words():
    for bad in ("FREQ=HOURLY", "COUNT=3", "FREQ=DAILY;BYDAY=XX", "FREQ=DAILY;COUNT=0"):
        with pytest.raises(Exception) as caught:
            recurrence_service.parse_rrule(bad)
        assert caught.value.code == "validation_error"


# ------------------------------------------------------------------
# API level
# ------------------------------------------------------------------


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Club night",
            "starts_at": "2026-10-28T19:00:00-04:00",
            "ends_at": "2026-10-28T21:00:00-04:00",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return {"owner": owner, "group": group, "event": created.json()}


def test_api_returns_occurrences_not_rules(client, world):
    gid = world["group"]["id"]
    event = world["event"]
    headers = bearer(world["owner"])
    put = client.put(
        f"/api/v1/groups/{gid}/events/{event['id']}/recurrence",
        json={"rrule": "FREQ=WEEKLY;COUNT=3"}, headers=headers,
    )
    assert put.status_code == 200, put.text
    occs = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/occurrences"
        "?from=2026-10-26T00:00:00Z&to=2026-11-16T00:00:00Z",
        headers=headers,
    )
    assert occs.status_code == 200, occs.text
    rows = occs.json()
    # Wall time pinned at 19:00; the OFFSET is what changes (EDT→EST).
    assert [row["occurrence_start"] for row in rows] == [
        "2026-10-28T19:00:00-04:00",
        "2026-11-04T19:00:00-05:00",
        "2026-11-11T19:00:00-05:00",
    ]
    assert [row["starts_at"] for row in rows] == [
        "2026-10-28T23:00:00Z", "2026-11-05T00:00:00Z", "2026-11-12T00:00:00Z",
    ]


def test_api_exception_moves_one_occurrence(client, world):
    gid = world["group"]["id"]
    event = world["event"]
    headers = bearer(world["owner"])
    client.put(
        f"/api/v1/groups/{gid}/events/{event['id']}/recurrence",
        json={"rrule": "FREQ=WEEKLY;COUNT=3"}, headers=headers,
    )
    moved = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/recurrence/exceptions",
        json={"original_start": "2026-11-04T19:00:00",
              "moved_to": "2026-11-05T20:00:00"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    rows = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/occurrences"
        "?from=2026-11-02T00:00:00Z&to=2026-11-09T00:00:00Z",
        headers=headers,
    ).json()
    assert len(rows) == 1
    assert rows[0]["occurrence_start"] == "2026-11-05T20:00:00-05:00"
    assert rows[0]["is_exception"] is True
