"""Flyer wall + print month tests (roadmap B-070, B-072).

The wall shows the month's posters — approved ones with their
events, waiting ones as pending_review, never invisible. The
print month is the noticeboard listing: every event's facts
present as text, asserted fact by fact (B-072's acceptance).
"""

from datetime import datetime, timezone

import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from vision_world import (
    ScriptedVisionDriver,
    flyer_answer,
    poster_bytes,
    request_read,
    run_one_job,
    upload_image,
)

RED = (208, 32, 32)


@pytest.fixture()
def world(client):
    driver = ScriptedVisionDriver()
    ai_client.set_client(driver)
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    return {"owner": owner, "group": group, "headers": bearer(owner), "driver": driver}


def _period_of(instant: datetime) -> str:
    return f"{instant.year:04d}-{instant.month:02d}"


def test_wall_shows_pending_then_the_event(world, client, app, db_session):
    gid = world["group"]["id"]
    detail = upload_image(client, world["headers"], gid, poster_bytes(RED))
    wall = client.get(
        f"/api/v1/groups/{gid}/flyers", headers=world["headers"]
    ).json()
    (entry,) = wall["entries"]
    assert entry["review_status"] == "pending_review"
    assert entry["event"] is None
    assert entry["alt_text_status"] == "awaiting_description"

    # Read + approve: the wall now carries the event instead.
    world["driver"].readings[RED] = flyer_answer(
        title="Harvest Supper", starts_phrase="Nov 6", starts_time="6.30pm",
        place="Village Hall", cost="£6", bring="a dish to share",
        transcript="HARVEST SUPPER — Nov 6, Village Hall",
        alt_text="A red poster for a harvest supper at the Village Hall "
                 "on Nov 6, £6, bring a dish to share.",
    )
    request_read(client, world["headers"], gid, detail["id"])
    assert run_one_job(app, db_session) is True
    candidate = client.get(
        f"/api/v1/groups/{gid}/candidates", headers=world["headers"]
    ).json()[0]
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=world["headers"],
    )
    assert approved.status_code == 200, approved.text
    event = client.get(
        f"/api/v1/groups/{gid}/events/{approved.json()['resulting_entity_id']}",
        headers=world["headers"],
    ).json()
    # The wall period follows the EVENT's month (the minted start
    # is the resolver's next Nov 6 from the upload day).
    from zoneinfo import ZoneInfo

    starts = datetime.fromisoformat(event["starts_at"].replace("Z", "+00:00"))
    period = _period_of(starts.astimezone(ZoneInfo("America/New_York")))
    wall = client.get(
        f"/api/v1/groups/{gid}/flyers?period={period}", headers=world["headers"]
    ).json()
    (entry,) = [e for e in wall["entries"] if e["flyer_id"] == detail["flyer"]["id"]]
    assert entry["review_status"] == "published"
    assert entry["event"]["id"] == event["id"]
    assert entry["event"]["title"] == "Harvest Supper"


def test_wall_period_filters_other_months(world, client):
    gid = world["group"]["id"]
    upload_image(client, world["headers"], gid, poster_bytes(RED))
    wall = client.get(
        f"/api/v1/groups/{gid}/flyers?period=1999-01", headers=world["headers"]
    ).json()
    assert wall["entries"] == []


def _make_event(client, world, **overrides):
    body = {
        "title": "Harvest Supper",
        "description": "The year's big shared meal.",
        "starts_at": "2026-11-06T23:30:00Z",  # 18:30 in New York
        "ends_at": "2026-11-07T02:00:00Z",
        "timezone": "America/New_York",
        "place_name": "Village Hall",
        "place_address": "1 High Street",
        "host_label": "Ana",
        "cost_text": "£6",
        "bring_list": "a dish to share",
        "status": "published",
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json=body, headers=world["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_print_month_is_text_complete(world, client):
    """B-072 acceptance: every fact of the event appears in the
    printed text — asserted one fact at a time."""
    _make_event(client, world)
    gid = world["group"]["id"]
    printed = client.get(
        f"/api/v1/groups/{gid}/print/month?month=2026-11",
        headers=world["headers"],
    ).json()
    assert printed["period"] == "2026-11"
    (row,) = printed["events"]
    assert row["title"] == "Harvest Supper"
    assert "Friday 06 November 2026" in row["when"]
    assert "6:30 PM" in row["when"]
    text = printed["text"]
    for fact in (
        "Harvest Supper",
        "Friday 06 November 2026",
        "Village Hall",
        "1 High Street",
        "Ana",
        "£6",
        "a dish to share",
        "The year's big shared meal.",
    ):
        assert fact in text, f"print text is missing {fact!r}"
        assert fact in str(row), f"print row is missing {fact!r}"


def test_print_month_empty_month_says_so(world, client):
    printed = client.get(
        f"/api/v1/groups/{world['group']['id']}/print/month?month=1999-01",
        headers=world["headers"],
    ).json()
    assert printed["events"] == []
    assert "No events this month." in printed["text"]
