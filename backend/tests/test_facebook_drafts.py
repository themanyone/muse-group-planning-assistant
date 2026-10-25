"""Facebook Event drafts (roadmap B-058).

The boundary IS the test: the backend produces a complete
draft — every field Meta's event creation requires, in Meta's
shape — and creates NOTHING outward. Draft events refuse
(existence-level), cancelled events refuse (a cancelled event
is announced as cancelled, never re-drafted as live).
"""

import pytest

from conftest import bearer, make_group, register
from gpa.services import facebook as facebook_service

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    return {"owner": owner, "group": group}


def create(client, world, **overrides):
    body = {
        "title": "Harvest Supper",
        "description": "A night of food.",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:00:00-05:00",
        "place_name": "Village Hall",
        "cost_text": "£6", "bring_list": "a dish", "host_label": "Ana",
        **overrides,
    }
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events",
        json=body, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_draft_is_complete_against_metas_required_fields(client, world):
    event = create(client, world)
    response = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}/facebook-draft",
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    payload = body["payload"]
    for field_name in facebook_service.META_REQUIRED_FIELDS:
        assert payload.get(field_name), f"draft is missing Meta-required {field_name}"
    assert payload["name"] == "Harvest Supper"
    # Times carry the event's own true offset (EST in November).
    assert payload["start_time"] == "2026-11-06T18:30:00-05:00"
    assert payload["end_time"] == "2026-11-06T21:00:00-05:00"
    assert payload["place"]["name"] == "Village Hall"
    assert "Cost: £6" in payload["description"]
    # The copy-ready block stands alone, pasteable anywhere.
    assert body["copy_text"].startswith("Harvest Supper\nFriday 06 November 2026, 18:30")
    assert "Where: Village Hall" in body["copy_text"]


def test_draft_and_cancelled_events_refuse(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    draft = create(client, world, status="draft")
    assert client.get(
        f"/api/v1/groups/{gid}/events/{draft['id']}/facebook-draft", headers=headers
    ).status_code == 404
    doomed = create(client, world, title="Doomed")
    client.post(
        f"/api/v1/groups/{gid}/events/{doomed['id']}/cancel",
        json={"reason": "rain"}, headers=headers,
    )
    refused = client.get(
        f"/api/v1/groups/{gid}/events/{doomed['id']}/facebook-draft", headers=headers
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "conflict"
