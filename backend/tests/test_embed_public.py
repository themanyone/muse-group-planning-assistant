"""Embed + public page data (roadmap B-135).

The widget's law, in one sentence: **an unregistered origin
gets nothing.** A group registers the origins allowed to
embed its page; CORS answers are made only for those
origins; the embed endpoint refuses a present-but-
unregistered Origin outright. The page data itself is the
B-059 public projection — the leak suite runs here too,
because a widget is exactly where a leak would hurt.
"""

import pytest

from conftest import bearer, make_group, register
from test_groups import invite_and_accept

ORIGIN = "https://village.example"


def world(client):
    owner = register(client)
    group = make_group(
        client, owner, "Village Circle", visibility="public-listed"
    )
    event = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Open Fete", "place_name": "The Green",
            "starts_at": "2026-11-06T18:30:00Z",
            "ends_at": "2026-11-06T21:00:00Z",
            "host_label": "Ana", "access_notes": "Door code 4413",
        },
        headers=bearer(owner),
    )
    assert event.status_code == 201, event.text
    return owner, group


def test_the_register_normalises_and_validates(client):
    owner, group = world(client)
    response = client.put(
        f"/api/v1/groups/{group['id']}/embed-origins",
        json={"origins": ["https://Village.Example/", "https://hall.example:8443"]},
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    assert response.json()["origins"] == [
        "https://village.example", "https://hall.example:8443",
    ]
    # A path is not an origin, and a bare word is not either.
    for bad in ("village.example/page", "just-a-word"):
        response = client.put(
            f"/api/v1/groups/{group['id']}/embed-origins",
            json={"origins": [bad]},
            headers=bearer(owner),
        )
        assert response.status_code == 422, (bad, response.text)
        assert response.json()["code"] == "validation_error"
    # Replacing the register is an admin act.
    ana = invite_and_accept(client, owner, group["id"], "ana@example.org", "member")
    response = client.put(
        f"/api/v1/groups/{group['id']}/embed-origins",
        json={"origins": [ORIGIN]},
        headers=bearer(ana),
    )
    assert response.status_code == 403


def test_public_page_carries_public_facts_only(client):
    owner, group = world(client)
    response = client.get(f"/api/v1/public/groups/{group['slug']}")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["slug"] == group["slug"]
    assert page["name"] == "Village Circle"
    assert page["events"][0]["title"] == "Open Fete"
    # The B-059 leak law, on the widget's payload too.
    assert "4413" not in response.text
    assert page["events"][0]["host_label"] is None


def test_cors_answers_only_for_registered_origins(client):
    owner, group = world(client)
    client.put(
        f"/api/v1/groups/{group['id']}/embed-origins",
        json={"origins": [ORIGIN]},
        headers=bearer(owner),
    )
    url = f"/api/v1/public/groups/{group['slug']}"
    # Registered: the answer echoes the origin.
    response = client.get(url, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    # Unregistered: the data is public, but the browser gets
    # NO CORS answer — so a browser cannot read it cross-origin.
    response = client.get(url, headers={"Origin": "https://stranger.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    # Preflight, both ways.
    response = client.options(
        f"/api/v1/embed/groups/{group['slug']}",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 204
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    response = client.options(
        f"/api/v1/embed/groups/{group['slug']}",
        headers={
            "Origin": "https://stranger.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_embed_door_refuses_an_unregistered_origin(client):
    owner, group = world(client)
    client.put(
        f"/api/v1/groups/{group['id']}/embed-origins",
        json={"origins": [ORIGIN]},
        headers=bearer(owner),
    )
    url = f"/api/v1/embed/groups/{group['slug']}"
    # The registered site embeds fine.
    response = client.get(url, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    assert response.json()["events"][0]["title"] == "Open Fete"
    # A stranger's origin gets nothing at all.
    response = client.get(url, headers={"Origin": "https://stranger.example"})
    assert response.status_code == 403
    assert "not registered to embed" in response.json()["detail"]
    # No Origin header is not a browser embed — served openly.
    response = client.get(url)
    assert response.status_code == 200


def test_a_private_group_has_no_page_anywhere(client):
    owner, group = world(client)
    private = make_group(client, owner, "Quiet Circle")  # private by default
    assert client.get(
        f"/api/v1/public/groups/{private['slug']}"
    ).status_code == 404
    assert client.get(
        f"/api/v1/embed/groups/{private['slug']}",
        headers={"Origin": ORIGIN},
    ).status_code == 404
