"""Generalised discussion threads (roadmap B-084).

B-060 built ONE thread system under events. B-084 generalises
it — the same Thread/Comment rows, the same routes' shapes,
the same moderation — to four parents: events, tasks,
decisions, polls. These tests pin that there is no second
comment system: a comment posted through the event path is
the same row the generic path reads, and moderation behaves
identically on a task's thread.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.events import Comment, Thread
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    gid = group["id"]
    headers = bearer(owner)
    event = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=headers,
    ).json()
    task = client.post(
        f"/api/v1/groups/{gid}/tasks", json={"title": "Book the band"}, headers=headers
    ).json()
    decision = client.post(
        f"/api/v1/groups/{gid}/decisions", json={"text": "Fridays it is"}, headers=headers
    ).json()
    poll = client.post(
        f"/api/v1/groups/{gid}/polls",
        json={"question": "Pizza or tacos?", "options": [{"label": "Pizza"}, {"label": "Tacos"}]},
        headers=headers,
    ).json()
    return {
        "owner": owner, "member": member, "group": group,
        "event": event, "task": task, "decision": decision, "poll": poll,
    }


def _post(client, world, who, parent_type, parent_id, body):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/comments/{parent_type}/{parent_id}",
        json={"body": body}, headers=bearer(who),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_task_thread(client, world, db_session):
    comment = _post(client, world, world["member"], "task", world["task"]["id"], "I can do it Friday")
    assert comment["parent_type"] == "task"
    assert comment["parent_id"] == world["task"]["id"]
    # An event-shaped field stays honest: null, not the task id.
    assert comment["event_id"] is None
    listed = client.get(
        f"/api/v1/groups/{world['group']['id']}/comments/task/{world['task']['id']}",
        headers=bearer(world["owner"]),
    ).json()
    assert [row["body"] for row in listed] == ["I can do it Friday"]
    # One system: exactly one thread row, of the shared kind.
    threads = db_session.query(Thread).all()
    assert len(threads) == 1
    assert threads[0].parent_type == "task"


def test_decision_and_poll_threads(client, world):
    _post(client, world, world["member"], "decision", world["decision"]["id"], "Agreed, Fridays")
    _post(client, world, world["owner"], "poll", world["poll"]["id"], "Vote by Thursday")
    gid = world["group"]["id"]
    decisions = client.get(
        f"/api/v1/groups/{gid}/comments/decision/{world['decision']['id']}",
        headers=bearer(world["member"]),
    ).json()
    assert [row["body"] for row in decisions] == ["Agreed, Fridays"]
    polls = client.get(
        f"/api/v1/groups/{gid}/comments/poll/{world['poll']['id']}",
        headers=bearer(world["member"]),
    ).json()
    assert [row["body"] for row in polls] == ["Vote by Thursday"]


def test_event_thread_is_the_same_rows_on_both_paths(client, world, db_session):
    gid = world["group"]["id"]
    via_event_path = client.post(
        f"/api/v1/groups/{gid}/events/{world['event']['id']}/comments",
        json={"body": "See you there"}, headers=bearer(world["member"]),
    )
    assert via_event_path.status_code == 201, via_event_path.text
    assert via_event_path.json()["event_id"] == world["event"]["id"]
    assert via_event_path.json()["parent_type"] == "event"
    via_generic = client.get(
        f"/api/v1/groups/{gid}/comments/event/{world['event']['id']}",
        headers=bearer(world["owner"]),
    ).json()
    assert [row["id"] for row in via_generic] == [via_event_path.json()["id"]]
    assert db_session.query(Comment).count() == 1
    assert db_session.query(Thread).count() == 1


def test_moderation_works_on_a_task_thread(client, world):
    gid = world["group"]["id"]
    comment = _post(client, world, world["member"], "task", world["task"]["id"], "a hot take")
    hidden = client.post(
        f"/api/v1/groups/{gid}/comments/{comment['id']}/hide",
        headers=bearer(world["owner"]),
    )
    assert hidden.status_code == 200, hidden.text
    member_view = client.get(
        f"/api/v1/groups/{gid}/comments/task/{world['task']['id']}",
        headers=bearer(world["member"]),
    ).json()
    assert member_view == []
    owner_view = client.get(
        f"/api/v1/groups/{gid}/comments/task/{world['task']['id']}",
        headers=bearer(world["owner"]),
    ).json()
    assert owner_view[0]["hidden"] is True


def test_unknown_parent_type_and_missing_parent_404(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    bogus = client.post(
        f"/api/v1/groups/{gid}/comments/banana/{world['task']['id']}",
        json={"body": "hello"}, headers=headers,
    )
    assert bogus.status_code == 404
    missing = client.post(
        f"/api/v1/groups/{gid}/comments/task/no-such-task",
        json={"body": "hello"}, headers=headers,
    )
    assert missing.status_code == 404
