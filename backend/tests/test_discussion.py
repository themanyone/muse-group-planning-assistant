"""Event discussion (roadmap B-060).

Threads v1 under an event: post, edit your own, delete your
own; organiser moderation by hiding — audited, reversible, and
never a rewrite of someone else's words. Plus the stream: a new
comment arrives as a ``comment.created`` SSE frame, polled from
the durable tables like everything else on that stream.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.services import stream as stream_service
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Harvest Supper",
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:00:00-05:00",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return {
        "owner": owner, "member": member, "group": group,
        "event": created.json(),
    }


def comments_url(world):
    return f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/comments"


def post(client, world, who, body):
    response = client.post(comments_url(world), json={"body": body}, headers=bearer(who))
    assert response.status_code == 201, response.text
    return response.json()


def test_post_edit_delete_own(client, world):
    mine = post(client, world, world["member"], "I'll bring chairs")
    assert mine["author_label"] == f"user:{world['member']['user_id']}"
    listed = client.get(comments_url(world), headers=bearer(world["owner"]))
    assert [row["body"] for row in listed.json()] == ["I'll bring chairs"]

    edited = client.patch(
        f"/api/v1/groups/{world['group']['id']}/comments/{mine['id']}",
        json={"body": "I'll bring chairs and a table"},
        headers=bearer(world["member"]),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["edited_at"]

    # Nobody edits anyone else's words — not even the owner.
    refused = client.patch(
        f"/api/v1/groups/{world['group']['id']}/comments/{mine['id']}",
        json={"body": "rewritten"}, headers=bearer(world["owner"]),
    )
    assert refused.status_code == 403

    deleted = client.delete(
        f"/api/v1/groups/{world['group']['id']}/comments/{mine['id']}",
        headers=bearer(world["member"]),
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    # Members no longer see it; organisers see the tombstone.
    member_view = client.get(comments_url(world), headers=bearer(world["member"]))
    assert member_view.json() == []
    owner_view = client.get(comments_url(world), headers=bearer(world["owner"]))
    assert owner_view.json()[0]["deleted"] is True
    assert owner_view.json()[0]["body"] == ""


def test_hiding_is_audited_and_reversible(client, world):
    comment = post(client, world, world["member"], "a hot take")
    gid = world["group"]["id"]
    hidden = client.post(
        f"/api/v1/groups/{gid}/comments/{comment['id']}/hide",
        headers=bearer(world["owner"]),
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["hidden"] is True
    assert client.get(comments_url(world), headers=bearer(world["member"])).json() == []
    owner_view = client.get(comments_url(world), headers=bearer(world["owner"]))
    assert owner_view.json()[0]["hidden"] is True
    assert owner_view.json()[0]["body"] == "a hot take"  # kept, for review

    audit = client.get(f"/api/v1/groups/{gid}/audit", headers=bearer(world["owner"]))
    actions = [row["action"] for row in audit.json()]
    assert "comment.hidden" in actions

    restored = client.post(
        f"/api/v1/groups/{gid}/comments/{comment['id']}/unhide",
        headers=bearer(world["owner"]),
    )
    assert restored.json()["hidden"] is False
    assert len(client.get(comments_url(world), headers=bearer(world["member"])).json()) == 1

    # A member cannot moderate.
    refused = client.post(
        f"/api/v1/groups/{gid}/comments/{comment['id']}/hide",
        headers=bearer(world["member"]),
    )
    assert refused.status_code == 403


def test_comment_created_arrives_on_the_stream(client, app, world):
    gid = world["group"]["id"]
    frames = stream_service.group_events(
        app.state.db.session, gid, poll_seconds=0.01, max_polls=50
    )
    first = next(frames)
    assert first.startswith("event: snapshot")
    post(client, world, world["member"], "streamed words")
    found = None
    for frame in frames:
        if frame.startswith("event: comment.created"):
            found = frame
            break
    assert found is not None
    assert "streamed words" in found
    assert world["event"]["id"] in found
