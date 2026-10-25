"""Calendar views (roadmap B-055, B-056, B-057, B-059).

Agenda (expanded, searched, filtered, cursor-paginated),
the month grid, the tonight view (pinned places first, flyers
with their alt text), conflict flags (with the back-to-back
false-positive guard), and the three projections — public,
member, organiser — with the leak suite grepping a public
response for member-only strings.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.events import Event
from gpa.models.sources import Flyer, Source
from test_groups import invite_and_accept

NY = "America/New_York"
WINDOW = "from=2026-11-06T00:00:00Z&to=2026-11-07T05:00:00Z"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def create(client, world, **overrides):
    body = {
        "title": "Event",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:00:00-05:00",
        **overrides,
    }
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json=body, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def evening(client, world):
    """One Friday evening in November, arranged to exercise the
    views: overlaps, a back-to-back pair, a double-booked host,
    and an impossible journey."""
    a = create(client, world, title="Harvest Supper", place_name="Village Hall",
               host_label="Ana", event_type="social", sub_group="kitchen",
               lat=42.0, lng=-76.0)
    b = create(client, world, title="Quiz Night", place_name="Village Hall",
               host_label="Ben", starts_at="2026-11-06T20:00:00-05:00",
               ends_at="2026-11-06T22:00:00-05:00")
    c = create(client, world, title="Supper Afterparty", place_name="Village Hall",
               host_label="Ana", starts_at="2026-11-06T21:00:00-05:00",
               ends_at="2026-11-06T22:30:00-05:00")
    e = create(client, world, title="Ana's Other Thing", place_name="The Library",
               host_label="Ana", starts_at="2026-11-06T19:00:00-05:00",
               ends_at="2026-11-06T20:00:00-05:00")
    market = create(client, world, title="Market Run", place_name="Far Town",
                    host_label="Ana", starts_at="2026-11-06T17:30:00-05:00",
                    ends_at="2026-11-06T18:15:00-05:00", lat=43.0, lng=-75.0)
    return {"a": a, "b": b, "c": c, "e": e, "market": market}


# ------------------------------------------------------------------
# B-055 — agenda + month
# ------------------------------------------------------------------


def test_agenda_expands_searches_and_paginates(client, world, evening):
    gid = world["group"]["id"]
    headers = bearer(world["member"])
    page1 = client.get(f"/api/v1/groups/{gid}/agenda?{WINDOW}&limit=2", headers=headers)
    assert page1.status_code == 200, page1.text
    body = page1.json()
    assert [row["title"] for row in body["items"]] == ["Market Run", "Harvest Supper"]
    assert body["next_cursor"]
    # Times arrive rendered in the group's zone, offsets and all.
    assert body["items"][1]["occurrence_start"] == "2026-11-06T18:30:00-05:00"

    page2 = client.get(
        f"/api/v1/groups/{gid}/agenda?{WINDOW}&limit=2&cursor={body['next_cursor']}",
        headers=headers,
    ).json()
    assert [row["title"] for row in page2["items"]] == ["Ana's Other Thing", "Quiz Night"]
    page3 = client.get(
        f"/api/v1/groups/{gid}/agenda?{WINDOW}&limit=2&cursor={page2['next_cursor']}",
        headers=headers,
    ).json()
    assert [row["title"] for row in page3["items"]] == ["Supper Afterparty"]
    assert page3["next_cursor"] is None

    searched = client.get(
        f"/api/v1/groups/{gid}/agenda?{WINDOW}&q=supper", headers=headers
    ).json()
    assert {row["title"] for row in searched["items"]} == {
        "Harvest Supper", "Supper Afterparty",
    }
    typed = client.get(
        f"/api/v1/groups/{gid}/agenda?{WINDOW}&type=social", headers=headers
    ).json()
    assert [row["title"] for row in typed["items"]] == ["Harvest Supper"]
    subbed = client.get(
        f"/api/v1/groups/{gid}/agenda?{WINDOW}&sub_group=kitchen", headers=headers
    ).json()
    assert [row["title"] for row in subbed["items"]] == ["Harvest Supper"]


def test_month_grid_places_occurrences(client, world, evening):
    gid = world["group"]["id"]
    grid = client.get(
        f"/api/v1/groups/{gid}/month?year=2026&month=11",
        headers=bearer(world["member"]),
    )
    assert grid.status_code == 200, grid.text
    body = grid.json()
    assert body["timezone"] == NY
    weeks = body["weeks"]
    assert len(weeks) == 6  # Oct 26 → Dec 6 covers November 2026
    assert weeks[0][0]["date"] == "2026-10-26"
    assert weeks[0][0]["in_month"] is False
    flat = {day["date"]: day for week in weeks for day in week}
    assert flat["2026-11-01"]["in_month"] is True
    friday = flat["2026-11-06"]
    assert {row["title"] for row in friday["occurrences"]} == {
        "Harvest Supper", "Quiz Night", "Supper Afterparty",
        "Ana's Other Thing", "Market Run",
    }


# ------------------------------------------------------------------
# B-056 — tonight
# ------------------------------------------------------------------


def test_tonight_orders_pinned_first_and_carries_flyers(client, world, evening, db_session):
    gid = world["group"]["id"]
    # A flyer-born event (the flyer row is Phase 5's to mint;
    # here the view is exercised with the link set directly).
    flyer_event = create(
        client, world, title="Lantern Walk", place_name="The Green",
        starts_at="2026-11-06T19:30:00-05:00",
        ends_at="2026-11-06T20:30:00-05:00",
    )
    source = db_session.query(Source).first()
    flyer = Flyer(
        group_id=gid, source_id=source.id,
        thumbnail_key="thumb/lantern.jpg",
        alt_text="A hand-drawn poster of paper lanterns over a village green.",
        alt_text_status="described",
    )
    db_session.add(flyer)
    db_session.flush()
    row = db_session.get(Event, flyer_event["id"])
    row.flyer_id = flyer.id
    db_session.commit()

    rows = client.get(
        f"/api/v1/groups/{gid}/tonight?date=2026-11-06",
        headers=bearer(world["member"]),
    )
    assert rows.status_code == 200, rows.text
    items = rows.json()
    titles = [row["title"] for row in items]
    assert "Lantern Walk" in titles
    # Near-first where place data exists: pinned events lead.
    pinned = {"Market Run", "Harvest Supper"}
    first_unpinned = min(
        i for i, title in enumerate(titles) if title not in pinned
    )
    assert all(title in pinned for title in titles[:first_unpinned])
    lantern = next(row for row in items if row["title"] == "Lantern Walk")
    assert lantern["flyer"]["alt_text"].startswith("A hand-drawn poster")
    assert lantern["flyer"]["thumbnail_key"] == "thumb/lantern.jpg"


# ------------------------------------------------------------------
# B-057 — conflicts: flags, never blocks, no back-to-back false hit
# ------------------------------------------------------------------


def test_conflict_flags_are_exact(client, world, evening):
    gid = world["group"]["id"]
    body = client.get(
        f"/api/v1/groups/{gid}/conflicts?{WINDOW}", headers=bearer(world["member"])
    )
    assert body.status_code == 200, body.text
    flags = body.json()["flags"]
    pairs = {
        (flag["kind"], frozenset({flag["event_id"], flag["other_event_id"]}))
        for flag in flags
    }
    a, b, c, e, market = (evening[k]["id"] for k in ("a", "b", "c", "e", "market"))
    assert ("place_overlap", frozenset({a, b})) in pairs      # hall, overlapping
    assert ("place_overlap", frozenset({b, c})) in pairs      # hall again, later
    assert ("host_overlap", frozenset({a, e})) in pairs       # Ana, twice at once
    travel = next(f for f in flags if f["kind"] == "travel_impossible")
    assert frozenset({travel["event_id"], travel["other_event_id"]}) == frozenset(
        {market, a}
    )
    assert travel["available_minutes"] == 15
    assert travel["needed_minutes"] > 60
    # THE guard: A ends 21:00 as C begins in the same room with
    # the same host — back-to-back is a plan, never a flag.
    assert not any(
        frozenset({flag["event_id"], flag["other_event_id"]}) == frozenset({a, c})
        for flag in flags
    )
    # Superseded by B-108 (Phase 9): resources exist now, and
    # the note says where their double-bookings appear instead
    # of deferring them to a later phase.
    assert "resource_overlap" in body.json()["resource_note"]


# ------------------------------------------------------------------
# B-059 — projections + the leak suite
# ------------------------------------------------------------------


def test_public_projection_leaks_nothing_member_only(client, world):
    listed = make_group(
        client, world["owner"], "Public Circle", visibility="public-listed",
        home_timezone=NY,
    )
    secret = client.post(
        f"/api/v1/groups/{listed['id']}/events",
        json={
            "title": "Open Fete", "place_name": "The Green",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "host_label": "Ana", "cost_text": "£6",
            "bring_list": "a dish", "access_notes": "Door code 4413",
        },
        headers=bearer(world["owner"]),
    )
    assert secret.status_code == 201, secret.text

    response = client.get(f"/api/v1/public/groups/{listed['slug']}/events")
    assert response.status_code == 200, response.text
    text = response.text
    assert "Open Fete" in text
    for member_only in ("4413", "Door code", "Ana", "£6", "a dish"):
        assert member_only not in text, f"public projection leaked {member_only!r}"
    row = response.json()[0]
    assert row["host_label"] is None and row["access_notes"] is None

    # The same event at member level DOES carry those fields.
    member_view = client.get(
        f"/api/v1/groups/{listed['id']}/events/{secret.json()['id']}",
        headers=bearer(world["owner"]),
    )
    assert "4413" in member_view.text

    # A private group has no public face at all.
    private = client.get(f"/api/v1/public/groups/{world['group']['slug']}/events")
    assert private.status_code == 404
