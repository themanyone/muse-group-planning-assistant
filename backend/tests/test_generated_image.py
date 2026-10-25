"""Generated event image tests (roadmap B-067).

An organiser may ask for a generated picture for an event that
has none. The law of the feature: ``generated: true`` — in every
projection, forever, because it is a column, not a label. Also
proven here: an event with a real flyer refuses (409), the mock
driver carries the whole flow offline, and the alt-text law
applies to generated pictures too (the organiser's own
description completes it; silence leaves it flagged).
"""

from conftest import bearer, make_group, register
from test_flyer_wall import _make_event


def _world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York",
                       visibility="public-listed")
    return {"owner": owner, "group": group, "headers": bearer(owner)}


def test_generated_image_is_flagged_in_every_projection(client, app):
    world = _world(client)
    gid = world["group"]["id"]
    event = _make_event(client, world)
    assert event["flyer"] is None

    made = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/generate-image",
        json={"description": "A lantern-lit harvest table under a "
                             "string of paper stars, drawn in warm "
                             "reds and golds."},
        headers=world["headers"],
    )
    assert made.status_code == 201, made.text
    flyer = made.json()["flyer"]
    assert flyer["generated"] is True
    # The organiser's own description is the alt text — complete.
    assert flyer["alt_text_status"] == "complete"
    assert "lantern-lit harvest table" in flyer["alt_text"]

    # Member + organiser reads carry it…
    detail = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=world["headers"]
    ).json()
    assert detail["flyer"]["generated"] is True
    listing = client.get(
        f"/api/v1/groups/{gid}/events", headers=world["headers"]
    ).json()
    assert listing[0]["flyer"]["generated"] is True
    # …the public projection carries it…
    public = client.get(
        f"/api/v1/public/groups/{world['group']['slug']}/events"
    ).json()
    assert public[0]["flyer"]["generated"] is True
    # …the wall carries it…
    wall = client.get(
        f"/api/v1/groups/{gid}/flyers?period=2026-11", headers=world["headers"]
    ).json()
    generated_entries = [e for e in wall["entries"] if e["generated"]]
    assert len(generated_entries) == 1
    assert generated_entries[0]["event"]["id"] == event["id"]
    # …and tonight carries it.
    tonight = client.get(
        f"/api/v1/groups/{gid}/tonight?date=2026-11-06", headers=world["headers"]
    ).json()
    assert tonight[0]["flyer"]["generated"] is True


def test_generated_image_without_a_description_stays_flagged(client, app):
    world = _world(client)
    gid = world["group"]["id"]
    event = _make_event(client, world)
    made = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/generate-image",
        json={}, headers=world["headers"],
    )
    assert made.status_code == 201, made.text
    assert made.json()["flyer"]["generated"] is True
    assert made.json()["flyer"]["alt_text"] == "awaiting description"
    assert made.json()["flyer"]["alt_text_status"] == "awaiting_description"


def test_an_event_with_a_picture_refuses_a_generated_one(client, app):
    world = _world(client)
    gid = world["group"]["id"]
    event = _make_event(client, world)
    first = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/generate-image",
        json={}, headers=world["headers"],
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/generate-image",
        json={}, headers=world["headers"],
    )
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"
