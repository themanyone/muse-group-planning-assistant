"""Recaps, newsletters, and the loop (roadmap B-118).

The acceptance is the loop closing: an event's recap is
drafted (discussion + attendance count + completed alt
text), approved by a person, woven into a monthly newsletter
draft, approved again — and the approved newsletter returns
as a SOURCE in the group's history, where next month's
pruning can find its words. Drafts never enter memory on
their own: only approval admits them.
"""

import calendar
from datetime import datetime, timezone

import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai import promptlib, pruning
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.nonprofit import AttendanceMark
from gpa.models.sources import Flyer, Source
from gpa.services import discussion as discussion_service
from gpa.services import memory as memory_service
from gpa.services import sources as source_service
from test_pruning import record_judgements

ALT_TEXT = (
    "The lantern parade crosses the stone bridge at dusk, "
    "walkers carrying paper lanterns."
)
RECAP_TEXT = (
    "The Lantern Walk glowed as the parade crossed the stone "
    "bridge at dusk with paper lanterns, and everyone agreed "
    "it was the best walk yet."
)
NEWSLETTER_BODY = (
    "Dear neighbours,\n\n"
    "The Lantern Walk glowed across the stone bridge, and the "
    "month felt golden.\n\n"
    "More soon, from all of us."
)
GOLDEN_PHRASE = "The Lantern Walk glowed across the stone bridge"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    ai_client.set_client(None)


def world(client, db_session):
    owner = register(client)
    second = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])
    label = f"user:{owner['user_id']}"
    created = client.post(
        f"/api/v1/groups/{group_json['id']}/events",
        json={
            "title": "Lantern Walk",
            "starts_at": "2026-10-24T18:30:00-04:00",
            "ends_at": "2026-10-24T20:00:00-04:00",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    event_json = created.json()
    for body in (
        "Best walk yet — the bridge looked magical.",
        "Next year we bring the choir along too.",
    ):
        discussion_service.post_comment(
            db_session, author_label=label,
            author_user_id=owner["user_id"], author_seat_id=None,
            group=group, parent_type="event", parent_id=event_json["id"],
            viewer_role="owner", body=body,
        )
    for user in (owner, second):
        db_session.add(
            AttendanceMark(
                group_id=group.id, event_id=event_json["id"],
                user_id=user["user_id"], marked_by=label,
            )
        )
    photo_source = source_service.create_source(
        db_session, actor_label=label, group=group, kind="flyer",
        title="Lantern Walk photos", granted_scope="group-history",
    )
    db_session.add(
        Flyer(
            group_id=group.id, source_id=photo_source.id,
            alt_text=ALT_TEXT, alt_text_status="complete",
        )
    )
    db_session.commit()
    event = db_session.get(Event, event_json["id"])
    comments = discussion_service.list_comments(
        db_session, group=group, parent_type="event",
        parent_id=event_json["id"], viewer_role="owner",
    )
    chunks = [
        pruning.MemoryChunk(
            chunk_id=f"comment:{c.id}", position=i, text=c.body,
            sender=c.author_label, sent_at=c.created_at, origin="comment",
        )
        for i, c in enumerate(comments)
    ]
    return owner, group_json, group, event_json, event, chunks


def record_recap(driver: FixtureDriver, event, chunks):
    template = promptlib.load_prompt("recap")
    prompt = template.render(
        EVENT=(
            f"{event.title} — {event.starts_at.isoformat()} "
            f"({event.timezone})"
        ),
        ATTENDANCE="2 people were marked present.",
        DISCUSSION=pruning.render_chunks(chunks),
        PHOTOS=f"- {ALT_TEXT}",
    )
    driver.add_json(prompt, template.schema, {"text": RECAP_TEXT})


def approve(client, owner, group_json, summary_id):
    response = client.post(
        f"/api/v1/groups/{group_json['id']}/summaries/{summary_id}/approve",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_loop_closes(client, db_session):
    owner, group_json, group, event_json, event, chunks = world(
        client, db_session
    )
    driver = FixtureDriver()
    record_recap(driver, event, chunks)
    ai_client.set_client(driver)

    drafted = client.post(
        f"/api/v1/groups/{group_json['id']}/events/{event_json['id']}/recap",
        headers=bearer(owner),
    )
    assert drafted.status_code == 201, drafted.text
    recap = drafted.json()["summary"]
    assert recap["kind"] == "recap" and recap["status"] == "draft"
    assert recap["body"] == RECAP_TEXT

    # A DRAFT is not memory: the corpus does not hold it yet.
    corpus = memory_service.approved_corpus(db_session, group)
    assert not any("Lantern Walk glowed" in c.text for c in corpus)

    settled = approve(client, owner, group_json, recap["id"])
    assert settled["status"] == "approved"

    # The newsletter, woven from this month's approved recap.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    template = promptlib.load_prompt("newsletter")
    driver.add_json(
        template.render(
            MONTH=f"{calendar.month_name[now.month]} {now.year}",
            RECAPS=f"## Recap — Lantern Walk\n{RECAP_TEXT}",
        ),
        template.schema,
        {"subject": "This month with the Lantern Walk", "body": NEWSLETTER_BODY},
    )
    letter = client.post(
        f"/api/v1/groups/{group_json['id']}/newsletters/draft",
        json={"year": now.year, "month": now.month},
        headers=bearer(owner),
    )
    assert letter.status_code == 201, letter.text
    letter_body = letter.json()["summary"]
    assert letter_body["kind"] == "newsletter"
    assert letter_body["status"] == "draft"

    approve(client, owner, group_json, letter_body["id"])

    # THE LOOP: the approved letter is now a source, and its
    # golden phrase sits in the approved corpus where pruning
    # can keep it for next month's questions.
    sources = (
        db_session.query(Source)
        .filter(Source.group_id == group.id, Source.kind == "newsletter")
        .all()
    )
    assert len(sources) == 1
    corpus = memory_service.approved_corpus(db_session, group)
    golden = [c for c in corpus if GOLDEN_PHRASE in c.text]
    assert len(golden) == 1

    pruner = FixtureDriver()
    record_judgements(
        pruner, "What happened on the Lantern Walk?", corpus,
        {golden[0].position},
    )
    outcome = pruning.prune_once(
        pruner, question="What happened on the Lantern Walk?",
        chunks=corpus, token_budget=100_000,
    )
    assert [c.chunk_id for c in outcome.kept] == [golden[0].chunk_id]

    # And a settled summary cannot be approved twice.
    again = client.post(
        f"/api/v1/groups/{group_json['id']}/summaries/{letter_body['id']}/approve",
        headers=bearer(owner),
    )
    assert again.status_code == 409, again.text
