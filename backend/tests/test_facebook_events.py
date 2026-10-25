"""Facebook Event creation (roadmap B-131).

The acceptance is the recorded-fixture integration test: the
whole outward life of one event against the
``FixtureFacebookClient`` double — created on the press,
stored id + URL, edits standing as "differs" until the next
press, a cancellation carried out by a press, removal by the
unpublish door. The roadmap's live smoke test (a throwaway
event on a real Page, deleted after) is NOT run here: no
Facebook account is connected to this project in this
environment and publishing is Volmarr's word alone — the
test report records it as such, never as done.

The press's fences are tested too: no connected Page and an
incomplete config refuse in plain words; the unavailable
client (this environment's default) refuses in plain words;
a member cannot press; a draft never travels.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.services import facebook_events as facebook_service
from test_groups import invite_and_accept

PAGE_ID = "page-longhouse"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    facebook_service.set_client(None)


def world(client, *, with_config=True):
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    double = facebook_service.FixtureFacebookClient()
    facebook_service.set_client(double)
    config = (
        {"page_id": PAGE_ID, "access_token": "fixture-token-not-real"}
        if with_config
        else {}
    )
    integration = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "facebook_events", "name": "The Page", "config": config},
        headers=bearer(owner),
    )
    assert integration.status_code == 201, integration.text
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Autumn Social",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "place_name": "Greenfield Farm",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return owner, group, created.json(), double


def press(client, owner, group, event):
    return client.post(
        f"/api/v1/groups/{group['id']}/events/{event['id']}/facebook",
        headers=bearer(owner),
    )


def state(client, owner, group, event):
    response = client.get(
        f"/api/v1/groups/{group['id']}/events/{event['id']}/facebook",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_fixture_life_create_differs_update_cancel_remove(client):
    owner, group, event, double = world(client)

    # Before any press: nothing exists out there.
    assert state(client, owner, group, event) == {
        "status": "not_published", "external_id": None,
        "url": None, "differs": False, "action": None,
    }

    # THE PRESS: the copy is created; its id + URL are stored.
    first = press(client, owner, group, event)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["action"] == "created"
    assert body["status"] == "published"
    assert body["differs"] is False
    external_id, url = body["external_id"], body["url"]
    assert external_id and url.endswith(external_id)
    copy = double.get_event(PAGE_ID, external_id)
    assert copy["name"] == "Autumn Social"
    assert copy["start_time"] == "2026-11-06T18:30:00-05:00"

    # A press with nothing changed is a no-op, stated.
    again = press(client, owner, group, event).json()
    assert again["action"] == "unchanged"

    # An edit here does NOT travel: it stands as "differs".
    gid = group["id"]
    moved = client.patch(
        f"/api/v1/groups/{gid}/events/{event['id']}",
        json={"starts_at": "2026-11-06T19:30:00-05:00"},
        headers={**bearer(owner), "If-Match": '"v1"'},
    )
    assert moved.status_code == 200, moved.text
    assert state(client, owner, group, event)["differs"] is True
    assert double.get_event(PAGE_ID, external_id)["start_time"] == (
        "2026-11-06T18:30:00-05:00"  # the Page still says the old time
    )

    # The next press brings the copy back into agreement.
    updated = press(client, owner, group, event).json()
    assert updated["action"] == "updated"
    assert updated["differs"] is False
    assert double.get_event(PAGE_ID, external_id)["start_time"] == (
        "2026-11-06T19:30:00-05:00"
    )

    # Cancellation here does not reach out on its own either;
    # the press carries it out, and the copy ends cancelled.
    cancelled = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/cancel",
        json={"reason": "the barn flooded"},
        headers=bearer(owner),
    )
    assert cancelled.status_code == 200, cancelled.text
    carried = press(client, owner, group, event).json()
    assert carried["action"] == "cancelled"
    assert carried["status"] == "cancelled"
    assert double.get_event(PAGE_ID, external_id)["cancelled"] is True

    # Removal deletes the Page copy and the link with it.
    removed = client.delete(
        f"/api/v1/groups/{gid}/events/{event['id']}/facebook",
        headers=bearer(owner),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["status"] == "not_published"
    assert double.get_event(PAGE_ID, external_id) is None


def test_press_refusals_are_plain_words(client):
    owner = register(client)
    group = make_group(client, owner)
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Social",
            "starts_at": "2026-11-06T18:30:00Z",
            "ends_at": "2026-11-06T21:00:00Z",
        },
        headers=bearer(owner),
    ).json()

    # No integration at all: the press names the missing Page.
    refused = press(client, owner, group, created)
    assert refused.status_code == 422, refused.text
    assert "No Facebook Page is connected" in refused.json()["detail"]

    # An integration without its credentials: named, too.
    client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "facebook_events", "name": "Page", "config": {}},
        headers=bearer(owner),
    )
    refused = press(client, owner, group, created)
    assert refused.status_code == 422
    assert "missing: page_id, access_token" in refused.json()["detail"]

    # Config complete, but the client boundary is this
    # environment's default — unavailable — and says so.
    client.patch(
        f"/api/v1/groups/{group['id']}/integrations/"
        f"{client.get(f'/api/v1/groups/{group['id']}/integrations', headers=bearer(owner)).json()[0]['id']}",
        json={"config": {"page_id": PAGE_ID, "access_token": "x"}},
        headers=bearer(owner),
    )
    refused = press(client, owner, group, created)
    assert refused.status_code == 422
    assert "No Facebook Page is connected to this deployment" in (
        refused.json()["detail"]
    )


def test_member_cannot_press_and_draft_never_travels(client):
    owner, group, event, double = world(client)
    member = invite_and_accept(
        client, owner, group["id"], "member@example.org", "member"
    )
    refused = client.post(
        f"/api/v1/groups/{group['id']}/events/{event['id']}/facebook",
        headers=bearer(member),
    )
    assert refused.status_code == 403, refused.text

    # A clone is born a draft (B-051); the press 404s on it —
    # the bright line: drafts reach no outward surface.
    clone = client.post(
        f"/api/v1/groups/{group['id']}/events/{event['id']}/clone",
        headers=bearer(owner),
    )
    assert clone.status_code == 201, clone.text
    assert clone.json()["status"] == "draft"
    refused = press(client, owner, group, clone.json())
    assert refused.status_code == 404, refused.text
