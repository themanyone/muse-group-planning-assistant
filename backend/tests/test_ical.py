"""The iCal engine + feeds + links (roadmap B-053, B-054).

The promises, as tests: a cancelled event is emitted AS
cancelled (never vanished) while a draft never appears at all;
a feed parsed back and expanded yields the same occurrences at
the same times as the API's own expansion (the import check,
verified by parse); feed tokens are hashed at rest and die
totally on revocation; and the Google link is a golden string —
its parameters match the event exactly.
"""

from datetime import timedelta

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.events import CalendarFeed
from gpa.services import ical as ical_service
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def create(client, world, **overrides):
    body = {
        "title": "Harvest Supper",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:00:00-05:00",
        "place_name": "Village Hall",
        **overrides,
    }
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json=body, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def issue_feed(client, world, filters=None):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/calendar/feeds",
        json={"filters": filters or {}}, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def fetch(client, token):
    return client.get(f"/api/v1/calendar/feed/{token}.ics")


# ------------------------------------------------------------------
# B-053 — files + feed
# ------------------------------------------------------------------


def test_event_ics_download_and_draft_refusal(client, world):
    event = create(client, world, title="Quiz, Night; Finals")
    gid = world["group"]["id"]
    response = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/ics",
        headers=bearer(world["member"]),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    (parsed,) = ical_service.parse_calendar(response.text)
    assert parsed["summary"] == "Quiz, Night; Finals"  # unescaped on parse
    assert parsed["status"] == "CONFIRMED"
    assert parsed["dtstart"]["kind"] == "local"
    assert parsed["dtstart"]["tzid"] == NY
    assert parsed["dtstart"]["local"].isoformat() == "2026-11-06T18:30:00"

    draft = create(client, world, status="draft", title="Secret draft")
    refused = client.get(
        f"/api/v1/groups/{gid}/events/{draft['id']}/ics",
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 404


def test_feed_carries_published_and_cancelled_never_drafts(client, world):
    create(client, world, title="Live One")
    doomed = create(client, world, title="Doomed One",
                    starts_at="2026-11-07T18:30:00-05:00",
                    ends_at="2026-11-07T21:00:00-05:00")
    create(client, world, status="draft", title="Draft One")
    gid = world["group"]["id"]
    client.post(
        f"/api/v1/groups/{gid}/events/{doomed['id']}/cancel",
        json={"reason": "Hall flooded"}, headers=bearer(world["owner"]),
    )
    issued = issue_feed(client, world)
    response = fetch(client, issued["token"])
    assert response.status_code == 200
    assert "Live One" in response.text
    assert "Doomed One" in response.text       # cancelled: present…
    assert "STATUS:CANCELLED" in response.text  # …and marked as such
    assert "Draft One" not in response.text     # drafts: never
    parsed = ical_service.parse_calendar(response.text)
    by_title = {row["summary"]: row for row in parsed}
    assert by_title["Doomed One"]["status"] == "CANCELLED"
    assert by_title["Live One"]["status"] == "CONFIRMED"


def test_feed_round_trip_expands_to_the_same_times(client, world):
    """The golden import check, verified by parse: a weekly
    series crossing the DST boundary, fed out and parsed back,
    expands to exactly the occurrences the API itself returns —
    identical times in the group's timezone."""
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    event = create(
        client, world, title="Club night",
        starts_at="2026-10-28T19:00:00-04:00",
        ends_at="2026-10-28T21:00:00-04:00",
    )
    client.put(
        f"/api/v1/groups/{gid}/events/{event['id']}/recurrence",
        json={"rrule": "FREQ=WEEKLY;COUNT=3"}, headers=headers,
    )
    issued = issue_feed(client, world)
    parsed = ical_service.parse_calendar(fetch(client, issued["token"]).text)
    (master,) = [row for row in parsed if row["summary"] == "Club night"]
    assert master["rrule"] == "FREQ=WEEKLY;COUNT=3"

    from datetime import datetime

    occurrences, _ = ical_service.expand_parsed(
        master, datetime(2026, 10, 26), datetime(2026, 11, 16)
    )
    api_rows = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/occurrences"
        "?from=2026-10-26T00:00:00Z&to=2026-11-16T00:00:00Z",
        headers=headers,
    ).json()
    assert [row["starts_at"] for row in api_rows] == [
        occ.starts_at.isoformat() + "Z" for occ in occurrences
    ]
    # And the wall times in the group's zone are the pinned 19:00.
    assert [row["occurrence_start"] for row in api_rows] == [
        "2026-10-28T19:00:00-04:00",
        "2026-11-04T19:00:00-05:00",
        "2026-11-11T19:00:00-05:00",
    ]


def test_feed_exceptions_ride_along(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    event = create(
        client, world, title="Club night",
        starts_at="2026-10-28T19:00:00-04:00",
        ends_at="2026-10-28T21:00:00-04:00",
    )
    client.put(
        f"/api/v1/groups/{gid}/events/{event['id']}/recurrence",
        json={"rrule": "FREQ=WEEKLY;COUNT=3"}, headers=headers,
    )
    client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/recurrence/exceptions",
        json={"original_start": "2026-11-04T19:00:00",
              "moved_to": "2026-11-05T20:00:00"},
        headers=headers,
    )
    client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/recurrence/exceptions",
        json={"original_start": "2026-11-11T19:00:00", "cancelled": True},
        headers=headers,
    )
    issued = issue_feed(client, world)
    text = fetch(client, issued["token"]).text
    assert "EXDATE;TZID=America/New_York:20261111T190000" in text
    parsed = ical_service.parse_calendar(text)
    moved = [row for row in parsed if "recurrence_id" in row]
    assert len(moved) == 1
    assert moved[0]["recurrence_id"]["local"].isoformat() == "2026-11-04T19:00:00"
    assert moved[0]["dtstart"]["local"].isoformat() == "2026-11-05T20:00:00"


def test_feed_token_is_hashed_and_revocation_is_total(client, world, db_session):
    issued = issue_feed(client, world)
    row = db_session.query(CalendarFeed).one()
    assert row.token_hash != issued["token"]
    assert issued["token"] not in row.token_hash
    assert row.token_prefix == issued["token"][:8]
    assert fetch(client, issued["token"]).status_code == 200
    assert fetch(client, "not-a-real-token").status_code == 404
    gid = world["group"]["id"]
    revoked = client.post(
        f"/api/v1/groups/{gid}/calendar/feeds/{issued['id']}/revoke",
        headers=bearer(world["owner"]),
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"]
    assert fetch(client, issued["token"]).status_code == 404


def test_feed_filters_narrow_what_a_leaked_url_leaks(client, world):
    create(client, world, title="Hall Night", place_name="Village Hall")
    create(client, world, title="Park Morning", place_name="The Park",
           starts_at="2026-11-07T10:00:00-05:00",
           ends_at="2026-11-07T11:00:00-05:00")
    issued = issue_feed(client, world, filters={"place_contains": "hall"})
    text = fetch(client, issued["token"]).text
    assert "Hall Night" in text
    assert "Park Morning" not in text


def test_feed_carries_a_real_vtimezone(client, world):
    create(client, world)
    issued = issue_feed(client, world)
    text = fetch(client, issued["token"]).text
    assert "BEGIN:VTIMEZONE" in text
    assert "TZID:America/New_York" in text
    assert "-0400" in text and "-0500" in text  # both offsets, honestly


# ------------------------------------------------------------------
# B-054 — golden links
# ------------------------------------------------------------------


def test_google_link_matches_the_event_exactly(client, world, db_session):
    event = create(
        client, world,
        description="A night of food.", cost_text="£6",
        bring_list="a dish", host_label="Ana",
    )
    from gpa.models.events import Event

    row = db_session.get(Event, event["id"])
    links = ical_service.event_links(row)
    assert links["google_calendar"] == (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        "&text=Harvest%20Supper"
        "&dates=20261106T233000Z/20261107T020000Z"
        "&details=A%20night%20of%20food.%0ACost%3A%20%C2%A36"
        "%0ABring%3A%20a%20dish%0AHost%3A%20Ana"
        "&location=Village%20Hall"
    )
    # The detail response carries the same link for organisers.
    detail = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        headers=bearer(world["owner"]),
    ).json()
    assert detail["links"]["google_calendar"] == links["google_calendar"]
    assert detail["links"]["ics_download"].endswith(f"/events/{event['id']}/ics")
