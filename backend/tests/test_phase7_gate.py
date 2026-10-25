"""Phase 7 gate (roadmap B-094): Flow B, end to end.

The proposal's Flow B — "The chat that becomes a plan": a
group argues happily in chat about a trip for forty messages;
the assistant drafts the event, a task list with owners, a
decisions log, and a list of what is still undecided; the
group approves, edits two things, and carries on talking —
underneath the event this time.

Here the forty messages are pasted, extracted by the fixture
driver through the real job queue (forty one-line messages
are exactly one extraction window), reviewed — two edits: the
place corrected, and one task's volunteered owner CONFIRMED
by a person naming the member (the B-083 law inside the
flow) — approved, and the whole plan stands: event published,
tasks minted (the rest unclaimed, their owner words kept as
suggestions), decisions current, questions open, and the
conversation continuing in the event's own thread.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from extraction_world import record_answers, run_extraction_job
from gpa.ai import client as ai_client
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.services import sources as source_service
from test_groups import invite_and_accept

GROUP_TZ = "America/New_York"

# The forty messages (Flow B's count, exactly): Ana, Ben,
# Cleo, and Dan plan a cabin trip on Wed 21 Oct 2026.
TRIP_CHAT = "\n".join(
    [
        "[21/10/2026, 19:00:00] Ana: Right, the cabin trip is happening — this Saturday 10am at the lake cabin, no more delays",
        "[21/10/2026, 19:01:00] Ben: FINALLY",
        "[21/10/2026, 19:02:00] Cleo: I have waited all year for this trip",
        "[21/10/2026, 19:03:00] Dan: Saturday works for me, I am in",
        "[21/10/2026, 19:04:00] Ana: I will book the cabin tonight before someone else takes it",
        "[21/10/2026, 19:05:00] Ben: The big one or the small one?",
        "[21/10/2026, 19:06:00] Cleo: Big one, we are six with the cousins",
        "[21/10/2026, 19:07:00] Ana: Agreed then — we take the big cabin",
        "[21/10/2026, 19:08:00] Dan: And the dogs?",
        "[21/10/2026, 19:09:00] Cleo: The dogs come too, obviously",
        "[21/10/2026, 19:10:00] Ben: I can sort the kayaks, my brother has two spare",
        "[21/10/2026, 19:11:00] Ana: Perfect, kayaks are yours",
        "[21/10/2026, 19:12:00] Dan: Big question — who is driving the van?",
        "[21/10/2026, 19:13:00] Cleo: Not me, I drove last time",
        "[21/10/2026, 19:14:00] Ben: We will figure the van out",
        "[21/10/2026, 19:15:00] Ana: I will plan the food shop on Friday",
        "[21/10/2026, 19:16:00] Cleo: Get the good cheese this time",
        "[21/10/2026, 19:17:00] Dan: And marshmallows, non negotiable",
        "[21/10/2026, 19:18:00] Ben: Also — do we need a second boat?",
        "[21/10/2026, 19:19:00] Ana: Maybe, depends who confirms",
        "[21/10/2026, 19:20:00] Cleo: My cousin Mia is a maybe",
        "[21/10/2026, 19:21:00] Dan: Tom is coming, he told me today",
        "[21/10/2026, 19:22:00] Ana: So that is six plus maybe Mia",
        "[21/10/2026, 19:23:00] Ben: The big cabin sleeps eight, fine",
        "[21/10/2026, 19:24:00] Cleo: What about firewood?",
        "[21/10/2026, 19:25:00] Dan: There is a stack at the cabin already",
        "[21/10/2026, 19:26:00] Ana: Good, one less thing",
        "[21/10/2026, 19:27:00] Ben: I will bring the big pan again",
        "[21/10/2026, 19:28:00] Cleo: And I will bring cards and the speaker",
        "[21/10/2026, 19:29:00] Dan: Weather looks clear for Saturday",
        "[21/10/2026, 19:30:00] Ana: If it rains we still go, agreed?",
        "[21/10/2026, 19:31:00] Ben: Agreed, rain or shine",
        "[21/10/2026, 19:32:00] Cleo: Rain or shine!",
        "[21/10/2026, 19:33:00] Dan: What time do we leave the city?",
        "[21/10/2026, 19:34:00] Ana: Aim to arrive by 10am, so leave at eight",
        "[21/10/2026, 19:35:00] Ben: Early but worth it",
        "[21/10/2026, 19:36:00] Cleo: I will set three alarms",
        "[21/10/2026, 19:37:00] Dan: Bringing my guitar for the evening",
        "[21/10/2026, 19:38:00] Ana: Perfect. Saturday, the lake cabin, all of us",
        "[21/10/2026, 19:39:00] Ben: Cannot wait",
    ]
)
assert len(TRIP_CHAT.splitlines()) == 40


def _field(value, confidence, quote, **extra):
    return {"value": value, "confidence": confidence, "quote": quote, **extra}


TRIP_ANSWERS = {
    "event": (
        "extraction",
        {"items": [{"fields": {
            "title": _field("Cabin trip", 0.95, "cabin trip is happening"),
            "starts": _field(
                "this Saturday 10am", 0.9, "this Saturday",
                date_phrase="this Saturday", time_phrase="10am",
            ),
            "place": _field("the lake cabin", 0.85, "at the lake cabin"),
            "host": _field(None, 0.0, ""),
        }}]},
    ),
    "task": (
        "extraction_tasks",
        {"items": [
            {"fields": {
                "title": _field("Book the cabin", 0.9, "book the cabin"),
                "owner": _field("Ana", 0.85, "I will book the cabin tonight"),
            }},
            {"fields": {
                "title": _field("Sort the kayaks", 0.9, "sort the kayaks"),
                "owner": _field("Ben", 0.9, "I can sort the kayaks"),
            }},
            {"fields": {
                "title": _field("Plan the food shop", 0.9, "plan the food shop"),
                "owner": _field("Ana", 0.85, "I will plan the food shop"),
            }},
        ]},
    ),
    "decision": (
        "extraction_decisions",
        {"items": [
            {"fields": {"text": _field("we take the big cabin", 0.92, "we take the big cabin")}},
            {"fields": {"text": _field("the dogs come too", 0.9, "the dogs come too")}},
        ]},
    ),
    "question": (
        "extraction_questions",
        {"items": [
            {"fields": {"text": _field("who is driving the van?", 0.9, "who is driving the van?")}},
            {"fields": {"text": _field("do we need a second boat?", 0.9, "do we need a second boat?")}},
        ]},
    ),
}


@pytest.fixture()
def world(client, app, db_session):
    owner = register(client, name="Ana")
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    pasted = client.post(
        f"/api/v1/groups/{group['id']}/sources/paste",
        json={"title": "Trip chat", "text": TRIP_CHAT},
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text
    source_id = pasted.json()["id"]
    from gpa.models.groups import Group
    from gpa.models.sources import Source

    group_row = db_session.get(Group, group["id"])
    source_row = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source_row)
    driver = FixtureDriver()
    record_answers(driver, chunks, TRIP_ANSWERS)
    ai_client.set_client(driver)
    return {
        "owner": owner, "member": member, "group": group,
        "group_row": group_row, "source_id": source_id,
        "source": source_row, "chunks": chunks, "driver": driver,
    }


def test_flow_b_chat_becomes_a_plan(client, app, db_session, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    assert len(world["chunks"]) == 40  # one message, one chunk

    run_extraction_job(client, app, db_session, world)
    queue = client.get(f"/api/v1/groups/{gid}/candidates", headers=headers)
    assert queue.status_code == 200, queue.text
    by_kind: dict[str, list] = {}
    for row in queue.json():
        by_kind.setdefault(row["kind"], []).append(row)
    assert {kind: len(rows) for kind, rows in by_kind.items()} == {
        "event": 1, "task": 3, "decision": 2, "question": 2,
    }
    assert all(
        row["status"] == "pending" for rows in by_kind.values() for row in rows
    )

    # Edit one of two: the reviewer corrects the event's place.
    event_candidate = by_kind["event"][0]
    draft = dict(event_candidate["draft"])
    draft["fields"] = dict(draft["fields"])
    draft["fields"]["place"] = {
        **draft["fields"]["place"], "value": "the big lake cabin",
    }
    draft["place"] = "the big lake cabin"
    edited = client.patch(
        f"/api/v1/groups/{gid}/candidates/{event_candidate['id']}",
        json={"draft": draft}, headers=headers,
    )
    assert edited.status_code == 200, edited.text

    # Edit two of two: Ben volunteered for the kayaks in words;
    # a PERSON confirms it by naming the member in the draft —
    # the only road from words to assignment (B-083).
    kayak = next(
        row for row in by_kind["task"] if row["title"] == "Sort the kayaks"
    )
    draft = dict(kayak["draft"])
    draft["owner_id"] = world["member"]["user_id"]
    edited = client.patch(
        f"/api/v1/groups/{gid}/candidates/{kayak['id']}",
        json={"draft": draft}, headers=headers,
    )
    assert edited.status_code == 200, edited.text

    # Approve everything; each approval mints its entity in
    # the same transaction and says what it became.
    minted: dict[str, list[str]] = {}
    for kind, rows in by_kind.items():
        for row in rows:
            approved = client.post(
                f"/api/v1/groups/{gid}/candidates/{row['id']}/approve",
                headers=headers,
            )
            assert approved.status_code == 200, approved.text
            body = approved.json()
            assert body["resulting_entity_type"] == kind
            minted.setdefault(kind, []).append(body["resulting_entity_id"])

    # The event: published, resolved from "this Saturday"
    # (sent Wed 2026-10-21) to Sat 2026-10-24 10:00 New York,
    # at the reviewer's corrected place.
    event = client.get(
        f"/api/v1/groups/{gid}/events/{minted['event'][0]}", headers=headers
    ).json()
    assert event["status"] == "published"
    assert event["title"] == "Cabin trip"
    assert event["starts_at"] == "2026-10-24T14:00:00Z"
    assert event["place_name"] == "the big lake cabin"

    # The task list: three minted tasks. The kayaks are Ben's —
    # confirmed by a person. The others are UNCLAIMED, their
    # owner words kept as suggestions and nothing more.
    tasks = client.get(f"/api/v1/groups/{gid}/tasks", headers=headers).json()
    assert len(tasks) == 3
    by_title = {row["title"]: row for row in tasks}
    assert by_title["Sort the kayaks"]["owner_id"] == world["member"]["user_id"]
    assert by_title["Sort the kayaks"]["unclaimed"] is False
    assert by_title["Sort the kayaks"]["suggested_owner_label"] == "Ben"
    assert by_title["Book the cabin"]["unclaimed"] is True
    assert by_title["Book the cabin"]["suggested_owner_label"] == "Ana"
    assert by_title["Plan the food shop"]["unclaimed"] is True

    # The decisions log: both, and both are settled truth.
    current = client.get(
        f"/api/v1/groups/{gid}/decisions?current=true", headers=headers
    ).json()
    assert {row["text"] for row in current} == {
        "we take the big cabin", "the dogs come too",
    }

    # What is still undecided: both questions, still open.
    questions = client.get(
        f"/api/v1/groups/{gid}/questions?status=open", headers=headers
    ).json()
    assert {row["text"] for row in questions} == {
        "who is driving the van?", "do we need a second boat?",
    }

    # And the group carries on talking — underneath the event
    # this time: the thread on the minted event holds the next
    # words, through the one thread system (B-084).
    posted = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/comments",
        json={"body": "Van driver still needed — any takers?"},
        headers=bearer(world["member"]),
    )
    assert posted.status_code == 201, posted.text
    thread = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/comments", headers=headers
    ).json()
    assert [row["body"] for row in thread] == [
        "Van driver still needed — any takers?"
    ]
