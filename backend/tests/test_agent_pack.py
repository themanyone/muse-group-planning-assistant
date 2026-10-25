"""Agent API pack (roadmap B-129): the seat experience.

- The seat's working surface is tagged ``agent`` in the
  live OpenAPI schema — and the doors outside the pack
  (integrations, member admin) are NOT;
- the worked example (examples/secretary_seat.py) runs in
  mock mode: a secretary seat with read+draft scopes reads
  a Samtal hall, drafts a candidate from the hall's own
  words into the queue, and meets the approval refusal —
  403 ``seat_cannot_approve`` — that defines the seat.
"""

import importlib.util
from pathlib import Path

import pytest

from conftest import bearer, make_group, register
from gpa.services import agent_pack
from gpa.services import samtal as samtal_service

HALL_ID = "hall-longhouse"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    samtal_service.set_client(None)


def load_example():
    path = Path(__file__).resolve().parent.parent / "examples" / "secretary_seat.py"
    spec = importlib.util.spec_from_file_location("secretary_seat", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_operations_are_tagged_in_the_schema(app):
    schema = app.openapi()
    tagged = set()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if "agent" in operation.get("tags", []):
                tagged.add((method.upper(), path))
    # The whole declared pack is present in the contract...
    assert set(agent_pack.AGENT_OPERATIONS) <= tagged
    # ...and the doors outside the pack stay outside it.
    assert ("POST", "/api/v1/groups/{group_id}/integrations") not in tagged
    assert ("POST", "/api/v1/groups/{group_id}/candidates/{candidate_id}/approve") not in tagged


def test_scope_guide_names_every_scope():
    assert set(agent_pack.SCOPE_GUIDE) == {"read", "draft", "publish", "admin"}
    # The one law the guide must never blur:
    assert "never approves" in agent_pack.SCOPE_GUIDE["publish"]


def test_secretary_seat_example_runs_in_mock_mode(client):
    owner = register(client)
    group = make_group(client, owner)
    samtal_service.set_client(
        samtal_service.FixtureSamtalClient(
            halls={
                HALL_ID: [
                    {
                        "id": "m1", "sender_id": "samtal-ana",
                        "sender_name": "Ana",
                        "text": "Club night is on! this Saturday at the hall, doors 7pm",
                        "sent_at": "2026-10-21T23:02:00+00:00",
                    },
                    {
                        "id": "m2", "sender_id": "samtal-ben",
                        "sender_name": "Ben",
                        "text": "I can bring the projector",
                        "sent_at": "2026-10-21T23:03:00+00:00",
                    },
                ]
            }
        )
    )
    live = client.post(
        f"/api/v1/groups/{group['id']}/sources/live",
        json={
            "connector_kind": "samtal",
            "external_id": HALL_ID,
            "granted_scope": "group-history",
            "title": "The longhouse hall",
        },
        headers=bearer(owner),
    )
    assert live.status_code == 201, live.text
    synced = client.post(
        f"/api/v1/groups/{group['id']}/sources/{live.json()['id']}/sync",
        headers=bearer(owner),
    )
    assert synced.status_code == 200, synced.text

    # The seat holds read + draft + publish — publish
    # included on purpose: the example's point is that even
    # WITH the publish scope, approval refuses it (B-018).
    seat = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={
            "name": "Secretary",
            "role": "member",
            "scopes": ["read", "draft", "publish"],
        },
        headers=bearer(owner),
    )
    assert seat.status_code == 201, seat.text

    example = load_example()
    report = example.secretary_pass(
        client,
        base_url="",
        api_key=seat.json()["key"],
        group_id=group["id"],
        source_id=live.json()["id"],
    )
    # It read the hall, and drafted the hall's own plan line.
    assert report["chunks_read"] == 2
    assert report["candidate_title"] == (
        "Club night is on! this Saturday at the hall, doors 7pm"
    )
    # And the boundary held: drafted by the seat, decidable
    # only by a person.
    assert report["approve_status"] == 403
    assert report["approve_code"] == "seat_cannot_approve"

    queue = client.get(
        f"/api/v1/groups/{group['id']}/candidates", headers=bearer(owner)
    ).json()
    drafted = [row for row in queue if row["id"] == report["candidate_id"]]
    assert len(drafted) == 1
    assert drafted[0]["status"] == "pending"
    assert drafted[0]["created_by"].startswith("seat:")
