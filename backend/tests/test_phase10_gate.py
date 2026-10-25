"""Phase 10 gate (roadmap B-122): the three memory demos.

Ask, briefing, recap — run end to end on one golden corpus,
twice over:

* MOCK mode — the schema-synthesising driver. The machinery
  runs in full and pretends at nothing: the mock keeps no
  chunks, so the ask honestly comes back "I couldn't verify
  this", while the briefing and recap still assemble their
  shapes from the group's real data.
* LIVE mode — the hosted driver's real code path (wire
  shape, headers, retries, parsing) against a stub transport
  whose answers are CONTENT-AWARE: it reads the prompt it was
  sent and judges the chunks in it, like a judge would. No
  real Meta endpoint or credential exists in this
  environment — that boundary is recorded in TEST_REPORT.md,
  and the fixture/mock drivers remain the demo drivers.

The golden corpus carries the phase's two planted truths:
the hall decision, and the marquee landmine.
"""

import json
import re
from datetime import datetime

import httpx
import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai.drivers_live import LiveDriver
from gpa.ai.drivers_mock import MockDriver
from gpa.config import Settings
from gpa.models.groups import Group
from gpa.models.nonprofit import AttendanceMark
from gpa.models.planning import Decision, RoleSlot
from gpa.services import discussion as discussion_service
from gpa.services import sources as source_service

LANDMINE = (
    "Careful: the marquee flooded the last time it rained at "
    "Greenfield, bring the groundsheet and raise the floor."
)
HALL_TEXT = "Agreed then: the hall, not the school gym, for club nights."
HALL_QUOTE = "the hall, not the school gym, for club nights"
RECAP_TEXT = (
    "The Autumn Social recap: neighbours gathered at the hall, "
    "the quiz ran long, and the marquee stayed dry this time."
)


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    ai_client.set_client(None)


def world(client, db_session):
    owner = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])
    label = f"user:{owner['user_id']}"
    source_service.create_source(
        db_session, actor_label=label, group=group, kind="chat_paste",
        title="Season chat", granted_scope="group-history",
        chunks=[
            {"text": "Quiz rota: Ben brings the cards, Cy scores.",
             "sender": "Ben", "sent_at": datetime(2026, 8, 3, 19, 0)},
            {"text": LANDMINE, "sender": "Ben",
             "sent_at": datetime(2026, 8, 3, 19, 5)},
            {"text": HALL_TEXT, "sender": "Ana",
             "sent_at": datetime(2026, 8, 3, 19, 9)},
            {"text": "The tea urn needs descaling before winter.",
             "sender": "Cy", "sent_at": datetime(2026, 8, 3, 19, 12)},
        ],
    )
    created = client.post(
        f"/api/v1/groups/{group_json['id']}/events",
        json={
            "title": "Autumn Social",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
            "place_name": "Greenfield Farm",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    event_json = created.json()
    db_session.add(
        Decision(
            group_id=group.id, event_id=event_json["id"],
            text="Club nights use the hall, not the school gym",
            status="current", created_by=label,
        )
    )
    db_session.add(
        RoleSlot(
            group_id=group.id, event_id=event_json["id"],
            role_name="Gate keeper", created_by=label,
        )
    )
    db_session.add(
        AttendanceMark(
            group_id=group.id, event_id=event_json["id"],
            user_id=owner["user_id"], marked_by=label,
        )
    )
    db_session.commit()
    discussion_service.post_comment(
        db_session, author_label=label, author_user_id=owner["user_id"],
        author_seat_id=None, group=group, parent_type="event",
        parent_id=event_json["id"], viewer_role="owner",
        body="Best night yet — the hall was warm and the quiz ran long.",
    )
    return owner, group_json, event_json


# --- The three demos -------------------------------------------------


def demo_ask(client, owner, group_json):
    return client.post(
        f"/api/v1/groups/{group_json['id']}/ask",
        json={"question": "Where do club nights happen?"},
        headers=bearer(owner),
    )


def demo_briefing(client, owner, group_json, event_json):
    return client.post(
        f"/api/v1/groups/{group_json['id']}/events/{event_json['id']}/briefing",
        headers=bearer(owner),
    )


def demo_recap(client, owner, group_json, event_json):
    return client.post(
        f"/api/v1/groups/{group_json['id']}/events/{event_json['id']}/recap",
        headers=bearer(owner),
    )


def test_phase10_demos_in_mock_mode(client, db_session):
    owner, group_json, event_json = world(client, db_session)
    ai_client.set_client(MockDriver())

    asked = demo_ask(client, owner, group_json)
    assert asked.status_code == 200, asked.text
    # The mock judges nothing, so the honest refusal is the
    # correct demo outcome — machinery exercised, nothing
    # pretended.
    assert asked.json()["verified"] is False
    assert asked.json()["answer"].startswith("I couldn't verify this")

    briefed = demo_briefing(client, owner, group_json, event_json)
    assert briefed.status_code == 201, briefed.text
    assert briefed.json()["summary"]["kind"] == "event_brief"
    # The data side of the briefing is real even in mock mode.
    assert briefed.json()["role_gaps"] == ["Gate keeper"]

    recapped = demo_recap(client, owner, group_json, event_json)
    assert recapped.status_code == 201, recapped.text
    assert recapped.json()["summary"]["kind"] == "recap"
    assert recapped.json()["summary"]["status"] == "draft"


# --- Live mode: the real wire path, a content-aware stub ------------

_MARKERS = ("hall", "marquee", "flooded", "Greenfield")


def _judge(content: str) -> dict:
    """Keep the chunk positions whose own text carries a
    marker — a stub that reads what it was sent. Chunks render
    as a ``[chunk N · sender · stamp]`` header line followed
    by the chunk's text."""
    kept = []
    blocks = re.split(r"\[chunk (\d+) · [^\]]*\]\n", content)
    # blocks: [prefix, pos, text, pos, text, ...]
    for index in range(1, len(blocks) - 1, 2):
        position, text = int(blocks[index]), blocks[index + 1]
        if any(marker in text for marker in _MARKERS):
            kept.append(position)
    return {"kept": kept}


def live_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    content = body["messages"][0]["content"]
    if '"title": "prune_reconsider"' in content:
        payload = _judge(content)
    elif '"title": "prune"' in content:
        payload = _judge(content)
    elif '"title": "ask_memory"' in content:
        payload = {
            "answer": "Club nights use the hall, not the school gym.",
            "citations": [{"quote": HALL_QUOTE}],
        }
    elif '"title": "briefing"' in content:
        text = (
            "Host briefing: the marquee flooded at Greenfield "
            "before — bring the groundsheet. Club nights use "
            "the hall."
            if "flooded" in content
            else "Host briefing: nothing in the history bears on this event."
        )
        payload = {"text": text, "open_questions": []}
    elif '"title": "recap"' in content:
        payload = {"text": RECAP_TEXT}
    else:  # pragma: no cover - a prompt the gate did not expect
        raise AssertionError(f"unexpected prompt routed to the stub: {content[:80]}")
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": json.dumps(payload)}}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
    )


def test_phase10_demos_in_live_mode(client, db_session):
    owner, group_json, event_json = world(client, db_session)
    settings = Settings(
        ai_api_base="https://models.test",
        ai_api_key="stub-key-for-stub-transport",
        ai_model_spark="stub-spark",
        ai_model_wonder="stub-wonder",
    )
    ai_client.set_client(
        LiveDriver(settings, transport=httpx.MockTransport(live_handler))
    )

    asked = demo_ask(client, owner, group_json)
    assert asked.status_code == 200, asked.text
    assert asked.json()["verified"] is True
    assert asked.json()["citations"][0]["quote"] == HALL_QUOTE
    assert asked.json()["citations"][0]["chunk_id"] is not None

    briefed = demo_briefing(client, owner, group_json, event_json)
    assert briefed.status_code == 201, briefed.text
    # The landmine, carried by the live wire path this time.
    assert "marquee flooded" in briefed.json()["summary"]["body"]
    assert briefed.json()["role_gaps"] == ["Gate keeper"]

    recapped = demo_recap(client, owner, group_json, event_json)
    assert recapped.status_code == 201, recapped.text
    assert recapped.json()["summary"]["body"] == RECAP_TEXT
    assert recapped.json()["summary"]["status"] == "draft"
