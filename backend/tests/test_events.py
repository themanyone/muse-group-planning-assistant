"""Events core (roadmap B-049, B-050, B-051).

The phase's founding sentences, as tests: an event is born only
by approval or by direct organiser entry (which records a manual
source); a draft event appears in NO feed — it is a 404 even to
members; every edit writes its change history under optimistic
concurrency; cancellation is a visible state with a reason, and
a clone copies structure but never people's answers.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from extraction_world import build_world, candidates_by_kind, run_extraction_job
from gpa.models.events import Event, EventSourceLink, Rsvp
from gpa.models.sources import Source
from test_groups import invite_and_accept

TZ = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=TZ)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "group": group}


def create_event(client, world, **overrides):
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
    return response


# ------------------------------------------------------------------
# B-049 — minting + direct entry + the bright line
# ------------------------------------------------------------------


def test_direct_entry_records_a_manual_source(client, world, db_session):
    response = create_event(client, world)
    event = response.json()
    assert event["status"] == "published"
    assert event["starts_at"] == "2026-11-06T23:30:00Z"  # 18:30 EST
    assert response.headers["ETag"] == '"v1"'
    # The entry is material: a manual source exists and the event
    # links to it like any sourced event.
    sources = db_session.query(Source).filter(Source.kind == "manual").all()
    assert len(sources) == 1
    assert sources[0].granted_by == f"user:{world['owner']['user_id']}"
    links = (
        db_session.query(EventSourceLink)
        .filter(EventSourceLink.event_id == event["id"])
        .all()
    )
    assert [link.source_id for link in links] == [sources[0].id]


def test_draft_event_is_invisible_below_organiser(client, world):
    draft = create_event(client, world, status="draft").json()
    gid = world["group"]["id"]
    member_headers = bearer(world["member"])
    # Not gettable, not listed: a draft is a 404, existence-level.
    got = client.get(f"/api/v1/groups/{gid}/events/{draft['id']}", headers=member_headers)
    assert got.status_code == 404
    listed = client.get(f"/api/v1/groups/{gid}/events", headers=member_headers)
    assert all(row["id"] != draft["id"] for row in listed.json())
    # The organiser still sees and reads it, of course.
    owner_listed = client.get(f"/api/v1/groups/{gid}/events", headers=bearer(world["owner"]))
    assert any(row["id"] == draft["id"] for row in owner_listed.json())
    # Publish crosses the line on purpose.
    published = client.post(
        f"/api/v1/groups/{gid}/events/{draft['id']}/publish", headers=bearer(world["owner"])
    )
    assert published.status_code == 200
    assert client.get(
        f"/api/v1/groups/{gid}/events/{draft['id']}", headers=member_headers
    ).status_code == 200


def test_approval_mints_a_published_event(client, app, db_session):
    world = build_world(client, app, db_session)
    run_extraction_job(client, app, db_session, world)
    candidate = candidates_by_kind(client, world)["event"]
    gid = world["group"]["id"]
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=bearer(world["owner"]),
    )
    assert approved.status_code == 200, approved.text
    event_id = approved.json()["resulting_entity_id"]
    assert approved.json()["resulting_entity_type"] == "event"

    event = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=bearer(world["owner"])
    ).json()
    assert event["status"] == "published"
    assert event["title"] == "Club night"
    assert event["place_name"] == "the hall"
    # Sat 2026-10-24, doors 7pm in New York (EDT, UTC-4) → 23:00Z,
    # and the two-hour default end the memory map records.
    assert event["starts_at"] == "2026-10-24T23:00:00Z"
    assert event["ends_at"] == "2026-10-25T01:00:00Z"
    # The event answers "where from?": source links with quotes.
    links = (
        db_session.query(EventSourceLink)
        .filter(EventSourceLink.event_id == event_id)
        .all()
    )
    assert links and all(link.source_id == world["source_id"] for link in links)
    # Link quotes are sealed at rest (B-167); the assertion
    # reads them through the opening law.
    from gpa.crypto import fields

    assert any(
        "this Saturday" in fields.open_text(db_session, gid, "event_link.quote", link.quote)
        for link in links
    )


def test_approval_of_a_dateless_draft_is_refused_in_plain_words(client, world):
    gid = world["group"]["id"]
    stub = client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "event", "title": "Someday, somehow"},
        headers=bearer(world["owner"]),
    )
    assert stub.status_code == 201, stub.text
    refused = client.post(
        f"/api/v1/groups/{gid}/candidates/{stub.json()['id']}/approve",
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "validation_error"
    # The candidate is still pending — the refusal decided nothing.
    queue = client.get(f"/api/v1/groups/{gid}/candidates", headers=bearer(world["owner"]))
    row = next(r for r in queue.json() if r["id"] == stub.json()["id"])
    assert row["status"] == "pending"


def test_member_cannot_create_events(client, world):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json={
            "title": "Nope", "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=bearer(world["member"]),
    )
    assert response.status_code == 403


# ------------------------------------------------------------------
# B-050 — If-Match editing + change history
# ------------------------------------------------------------------


def test_patch_requires_if_match_and_rejects_stale_versions(client, world):
    event = create_event(client, world).json()
    gid = world["group"]["id"]
    url = f"/api/v1/groups/{gid}/events/{event['id']}"
    headers = bearer(world["owner"])

    missing = client.patch(url, json={"title": "New name"}, headers=headers)
    assert missing.status_code == 422

    stale = client.patch(
        url, json={"title": "New name"}, headers={**headers, "If-Match": '"v99"'}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_version"
    assert "version 1" in stale.json()["detail"]

    ok = client.patch(
        url, json={"title": "New name"}, headers={**headers, "If-Match": '"v1"'}
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["ETag"] == '"v2"'
    assert ok.json()["title"] == "New name"


def test_change_history_is_complete_and_ordered(client, world):
    event = create_event(client, world).json()
    gid = world["group"]["id"]
    url = f"/api/v1/groups/{gid}/events/{event['id']}"
    headers = bearer(world["owner"])
    client.patch(
        url, json={"place_name": "The Old School", "reason": "hall double-booked"},
        headers={**headers, "If-Match": '"v1"'},
    )
    client.patch(
        url, json={"starts_at": "2026-11-06T19:00:00-05:00"},
        headers={**headers, "If-Match": '"v2"'},
    )
    feed = client.get(f"{url}/changes", headers=headers)
    assert feed.status_code == 200
    rows = feed.json()
    assert [row["field"] for row in rows] == ["place_name", "starts_at"]
    assert rows[0]["old_value"] == "Village Hall"
    assert rows[0]["new_value"] == "The Old School"
    assert rows[0]["reason"] == "hall double-booked"
    assert rows[1]["old_value"] == "2026-11-06T23:30:00Z"
    assert rows[1]["new_value"] == "2026-11-07T00:00:00Z"
    # Both were material: the event must be re-announced.
    current = client.get(url, headers=headers).json()
    assert current["needs_reannounce"] is True


def test_nonmaterial_patch_does_not_set_needs_reannounce(client, world):
    event = create_event(client, world).json()
    gid = world["group"]["id"]
    url = f"/api/v1/groups/{gid}/events/{event['id']}"
    headers = bearer(world["owner"])
    client.patch(
        url, json={"description": "Bring a dish to share."},
        headers={**headers, "If-Match": '"v1"'},
    )
    current = client.get(url, headers=headers).json()
    assert current["needs_reannounce"] is False
    assert current["etag"] == '"v2"'


def test_noop_patch_changes_nothing(client, world):
    event = create_event(client, world).json()
    gid = world["group"]["id"]
    url = f"/api/v1/groups/{gid}/events/{event['id']}"
    headers = bearer(world["owner"])
    response = client.patch(
        url, json={"title": event["title"]}, headers={**headers, "If-Match": '"v1"'}
    )
    assert response.status_code == 200
    assert response.headers["ETag"] == '"v1"'  # no bump
    assert client.get(f"{url}/changes", headers=headers).json() == []


# ------------------------------------------------------------------
# B-051 — cancellation + clone
# ------------------------------------------------------------------


def test_cancellation_is_a_visible_state_with_a_reason(client, world):
    event = create_event(client, world).json()
    gid = world["group"]["id"]
    url = f"/api/v1/groups/{gid}/events/{event['id']}"
    headers = bearer(world["owner"])
    cancelled = client.post(
        f"{url}/cancel", json={"reason": "Hall flooded"}, headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_reason"] == "Hall flooded"
    assert cancelled.json()["needs_reannounce"] is True
    # Members still SEE it — as cancelled, never vanished.
    seen = client.get(url, headers=bearer(world["member"]))
    assert seen.status_code == 200
    assert seen.json()["status"] == "cancelled"
    changes = client.get(f"{url}/changes", headers=headers).json()
    assert changes[-1]["field"] == "status"
    assert changes[-1]["new_value"] == "cancelled"
    # And a cancelled event refuses edits and re-cancellation.
    assert client.post(
        f"{url}/cancel", json={"reason": "again"}, headers=headers
    ).status_code == 409
    assert client.patch(
        url, json={"title": "x"}, headers={**headers, "If-Match": '"v2"'}
    ).status_code == 409


def test_clone_copies_structure_never_answers(client, world, db_session):
    event = create_event(client, world, capacity=30).json()
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    cloned = client.post(f"/api/v1/groups/{gid}/events/{event['id']}/clone", headers=headers)
    assert cloned.status_code == 201, cloned.text
    body = cloned.json()
    assert body["id"] != event["id"]
    assert body["status"] == "draft"
    assert body["title"] == event["title"]
    assert body["place_name"] == event["place_name"]
    assert body["capacity"] == 30
    assert body["starts_at"] == event["starts_at"]
    # No RSVPs rode along (there are none to copy — the table is
    # untouched by cloning, asserted structurally).
    assert db_session.query(Rsvp).filter(Rsvp.event_id == body["id"]).count() == 0
    # The clone starts with an empty change history of its own.
    assert client.get(
        f"/api/v1/groups/{gid}/events/{body['id']}/changes", headers=headers
    ).json() == []
    assert isinstance(db_session.get(Event, body["id"]), Event)
