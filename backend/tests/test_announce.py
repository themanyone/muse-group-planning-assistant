"""B-078 — re-announce drafts: a materially changed event gets
a drafted announcement naming what changed, old → new, in
plain words. Drafting only (sending is Phase 10), and drafting
never clears needs_reannounce — a draft in a drawer tells
nobody.
"""

from conftest import bearer, make_group, register
from test_groups import invite_and_accept


def _world(client):
    owner = register(client, "an-owner@example.org")
    gid = make_group(client, owner, home_timezone="America/New_York")["id"]
    created = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Harvest Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:30:00-05:00",
            "place_name": "Village Hall",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return owner, gid, created.json(), created.headers["etag"]


def _move_start(client, owner, gid, event, etag):
    response = client.patch(
        f"/api/v1/groups/{gid}/events/{event['id']}",
        json={"starts_at": "2026-11-06T19:30:00-05:00"},
        headers={**bearer(owner), "If-Match": etag},
    )
    assert response.status_code == 200, response.text
    return response.json(), response.headers["etag"]


def _draft(client, owner, gid, event_id):
    return client.post(
        f"/api/v1/groups/{gid}/events/{event_id}/reannounce-drafts",
        headers=bearer(owner),
    )


def test_draft_names_old_and_new_values(client):
    owner, gid, event, etag = _world(client)
    _move_start(client, owner, gid, event, etag)
    response = _draft(client, owner, gid, event["id"])
    assert response.status_code == 201, response.text
    draft = response.json()
    assert "Start:" in draft["body"]
    assert "06:30 PM" in draft["body"]  # the old value, named
    assert "07:30 PM" in draft["body"]  # the new value, named
    assert "→" in draft["body"]
    # The bare word is never the whole message.
    assert draft["body"].strip().lower() != "updated"
    assert draft["changes"][0]["field"] == "starts_at"
    assert "06:30 PM" in draft["changes"][0]["old"]
    assert "07:30 PM" in draft["changes"][0]["new"]


def test_nothing_to_announce_is_refused_plainly(client):
    owner, gid, event, _etag = _world(client)
    response = _draft(client, owner, gid, event["id"])
    assert response.status_code == 422
    assert "nothing to announce" in response.json()["detail"].lower()


def test_second_draft_covers_only_what_changed_since(client):
    owner, gid, event, etag = _world(client)
    moved, moved_etag = _move_start(client, owner, gid, event, etag)
    assert _draft(client, owner, gid, event["id"]).status_code == 201
    response = client.patch(
        f"/api/v1/groups/{gid}/events/{event['id']}",
        json={"place_name": "School Gym"},
        headers={**bearer(owner), "If-Match": moved_etag},
    )
    assert response.status_code == 200, response.text
    second = _draft(client, owner, gid, event["id"])
    assert second.status_code == 201, second.text
    body = second.json()["body"]
    assert "Place: Village Hall → School Gym" in body
    assert "Start:" not in body  # already announced in draft one
    drafts = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/reannounce-drafts",
        headers=bearer(owner),
    ).json()
    assert len(drafts) == 2


def test_drafting_does_not_clear_needs_reannounce(client):
    owner, gid, event, etag = _world(client)
    moved, _etag = _move_start(client, owner, gid, event, etag)
    assert moved["needs_reannounce"] is True
    assert _draft(client, owner, gid, event["id"]).status_code == 201
    after = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    ).json()
    assert after["needs_reannounce"] is True


def test_cancellation_is_announced_as_a_change(client):
    owner, gid, event, _etag = _world(client)
    cancelled = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/cancel",
        json={"reason": "hall double-booked"},
        headers=bearer(owner),
    )
    assert cancelled.status_code == 200, cancelled.text
    draft = _draft(client, owner, gid, event["id"])
    assert draft.status_code == 201, draft.text
    assert "Status: published → cancelled" in draft.json()["body"]


def test_member_cannot_draft(client):
    owner, gid, event, etag = _world(client)
    member = invite_and_accept(client, owner, gid, "an-member@example.org", "member")
    _move_start(client, owner, gid, event, etag)
    response = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/reannounce-drafts",
        headers=bearer(member),
    )
    assert response.status_code == 403
