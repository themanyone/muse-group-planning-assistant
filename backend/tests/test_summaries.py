"""Discussion summaries (roadmap B-116).

A thread → a summary, stored as a draft — and the decisions
and next actions the summary names arrive as CANDIDATES in
the review queue. The law under test: summaries propose, the
queue disposes; a summary never silently writes a decision or
a task. Comments are posted through the discussion service so
the fixture recordings key on the very rows the summariser
reads.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai import promptlib, pruning
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.models.groups import Candidate, Group
from gpa.models.planning import Decision, Task
from gpa.services import discussion as discussion_service

COMMENTS = [
    "We need to settle the supper date tonight, it keeps slipping.",
    "Agreed — we will hold the harvest supper on Nov 6 at the hall.",
    "Ana will book the hall this week, the deposit is forty pounds.",
    "The quiz can move to Thursdays if the supper takes the Friday.",
]
SUMMARY_TEXT = (
    "The group settled the supper: we will hold the harvest supper "
    "on Nov 6 at the hall. Next action: Ana will book the hall this week."
)


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    ai_client.set_client(None)


def world(client, db_session):
    owner = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])
    created = client.post(
        f"/api/v1/groups/{group_json['id']}/events",
        json={
            "title": "Harvest Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    event = created.json()
    label = f"user:{owner['user_id']}"
    for body in COMMENTS:
        discussion_service.post_comment(
            db_session, author_label=label, author_user_id=owner["user_id"],
            author_seat_id=None, group=group, parent_type="event",
            parent_id=event["id"], viewer_role="owner", body=body,
        )
    comments = discussion_service.list_comments(
        db_session, group=group, parent_type="event",
        parent_id=event["id"], viewer_role="owner",
    )
    chunks = [
        pruning.MemoryChunk(
            chunk_id=f"comment:{c.id}", position=i, text=c.body,
            sender=c.author_label, sent_at=c.created_at, origin="comment",
        )
        for i, c in enumerate(comments)
    ]
    return owner, group_json, group, event, chunks


def record_all(driver: FixtureDriver, chunks):
    summary_schema = promptlib.load_prompt("summary").schema
    driver.add_json(
        promptlib.render_prompt(
            "summary", CHUNKS=pruning.render_chunks(chunks)
        ),
        summary_schema, {"text": SUMMARY_TEXT},
    )
    tz = "UTC"
    decisions_schema = promptlib.load_prompt("extraction_decisions").schema
    driver.add_json(
        build_prompt("extraction_decisions", chunks, tz), decisions_schema,
        {"items": [{"fields": {"text": {
            "value": "we will hold the harvest supper on Nov 6 at the hall",
            "confidence": 0.9,
            "quote": "we will hold the harvest supper on Nov 6 at the hall",
        }}}]},
    )
    tasks_schema = promptlib.load_prompt("extraction_tasks").schema
    driver.add_json(
        build_prompt("extraction_tasks", chunks, tz), tasks_schema,
        {"items": [{"fields": {"title": {
            "value": "Ana will book the hall",
            "confidence": 0.85,
            "quote": "Ana will book the hall this week",
        }}}]},
    )


def test_summary_proposes_candidates_and_writes_nothing_itself(client, db_session):
    owner, group_json, group, event, chunks = world(client, db_session)
    driver = FixtureDriver()
    record_all(driver, chunks)
    ai_client.set_client(driver)

    response = client.post(
        f"/api/v1/groups/{group_json['id']}/summaries/discussion",
        json={"parent_type": "event", "parent_id": event["id"]},
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["summary"]["kind"] == "discussion"
    assert body["summary"]["status"] == "draft"
    assert body["summary"]["body"] == SUMMARY_TEXT
    assert len(body["candidate_ids"]) == 2

    candidates = (
        db_session.query(Candidate)
        .filter(Candidate.group_id == group.id, Candidate.status == "pending")
        .all()
    )
    assert {c.kind for c in candidates} == {"decision", "task"}
    assert all(c.created_by == "system:summary" for c in candidates)
    assert all("from_summary" in c.flags_json for c in candidates)

    # THE law: no Decision and no Task rows exist. The queue
    # holds drafts; nothing was disposed by the summary itself.
    assert db_session.query(Decision).filter(Decision.group_id == group.id).count() == 0
    assert db_session.query(Task).filter(Task.group_id == group.id).count() == 0


def test_summarising_an_empty_thread_is_refused_plainly(client, db_session):
    owner = register(client)
    group_json = make_group(client, owner)
    created = client.post(
        f"/api/v1/groups/{group_json['id']}/events",
        json={
            "title": "Quiet Event",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=bearer(owner),
    )
    event = created.json()
    ai_client.set_client(FixtureDriver())
    response = client.post(
        f"/api/v1/groups/{group_json['id']}/summaries/discussion",
        json={"parent_type": "event", "parent_id": event["id"]},
        headers=bearer(owner),
    )
    assert response.status_code == 422, response.text
    assert "no discussion to summarise" in response.json()["detail"]
