"""Polls (roadmap B-087).

Create, vote, change vote, close, results — and the date
poll's winner minting or moving an event on an ORGANISER's
action, never automatically. Two laws are pinned hard:

- one member, one CURRENT vote, under real concurrent writes
  (the unique constraint plus the service's retry — the tally
  after the storm counts distinct voters, never attempts);
- commit-before-answer: a vote is committed before any tally
  including it is read — proven by reading the vote row from a
  SEPARATE database session the moment the response returns.
"""

import threading

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.planning import PollVote
from gpa.services import polls as poll_service
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    member = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    second = invite_and_accept(client, owner, group["id"], unique_email(), "member")
    return {"owner": owner, "member": member, "second": second, "group": group}


def _poll(client, world, **overrides):
    body = {
        "question": "Pizza or tacos?",
        "options": [{"label": "Pizza"}, {"label": "Tacos"}],
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/polls",
        json=body, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _vote(client, world, who, poll, option_index=0):
    return client.post(
        f"/api/v1/groups/{world['group']['id']}/polls/{poll['id']}/vote",
        json={"option_id": poll["options"][option_index]["id"]},
        headers=bearer(who),
    )


def _date_poll(client, world, **overrides):
    body = {
        "question": "When is the cabin trip?",
        "kind": "date",
        "options": [
            {"starts_at": "2026-11-07T15:00:00-05:00", "ends_at": "2026-11-09T11:00:00-05:00"},
            {"starts_at": "2026-11-14T15:00:00-05:00", "ends_at": "2026-11-16T11:00:00-05:00"},
        ],
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/polls",
        json=body, headers=bearer(world["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_vote_change_vote_and_results(client, world):
    poll = _poll(client, world)
    gid = world["group"]["id"]
    first = _vote(client, world, world["member"], poll, 0)
    assert first.status_code == 200, first.text
    body = first.json()
    # The answer IS the post-commit tally: my vote is in it.
    assert body["total_votes"] == 1
    assert body["my_option_id"] == poll["options"][0]["id"]
    assert body["leader_option_ids"] == [poll["options"][0]["id"]]

    _vote(client, world, world["second"], poll, 1)
    tied = client.get(
        f"/api/v1/groups/{gid}/polls/{poll['id']}", headers=bearer(world["owner"])
    ).json()
    assert tied["total_votes"] == 2
    assert {row["votes"] for row in tied["options"]} == {1}

    # Changing a vote MOVES it — the total never grows.
    changed = _vote(client, world, world["member"], poll, 1)
    assert changed.status_code == 200
    body = changed.json()
    assert body["total_votes"] == 2
    counts = {row["id"]: row["votes"] for row in body["options"]}
    assert counts[poll["options"][0]["id"]] == 0
    assert counts[poll["options"][1]["id"]] == 2
    assert body["leader_option_ids"] == [poll["options"][1]["id"]]


def test_vote_is_committed_before_the_tally_is_answered(client, world, app):
    """The commit-before-answer law, proven from outside: the
    instant the vote response returns, a separate session (a
    separate connection's view of committed state) already
    holds the vote row."""
    poll = _poll(client, world)
    response = _vote(client, world, world["member"], poll, 0)
    assert response.status_code == 200
    fresh = app.state.db.session()
    try:
        rows = fresh.query(PollVote).filter(PollVote.poll_id == poll["id"]).all()
        assert len(rows) == 1
        assert rows[0].option_id == poll["options"][0]["id"]
    finally:
        fresh.close()


def test_concurrent_votes_one_member_one_current_vote(client, world, app, db_session):
    """Eight threads vote as the SAME member at once, flipping
    between two options. Whatever the races, the member ends
    with exactly one vote row and the tally counts them once."""
    poll = _poll(client, world)
    from gpa.models.groups import Group

    group_row = db_session.get(Group, world["group"]["id"])
    label = f"user:{world['member']['user_id']}"
    option_ids = [row["id"] for row in poll["options"]]
    errors: list[Exception] = []

    def cast(index: int) -> None:
        session = app.state.db.session()
        try:
            poll_service.cast_vote(
                session,
                actor_label=label,
                user_id=world["member"]["user_id"],
                seat_id=None,
                group=group_row,
                poll_id=poll["id"],
                option_id=option_ids[index % 2],
            )
        except Exception as exc:  # collected, asserted below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=cast, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    fresh = app.state.db.session()
    try:
        rows = (
            fresh.query(PollVote)
            .filter(PollVote.poll_id == poll["id"], PollVote.voter_label == label)
            .all()
        )
        assert len(rows) == 1
        results = poll_service.poll_results(fresh, poll_service.get_poll(fresh, group_row, poll["id"]))
        assert results["total_votes"] == 1
    finally:
        fresh.close()


def test_close_freezes_the_tally(client, world):
    gid = world["group"]["id"]
    poll = _poll(client, world)
    _vote(client, world, world["member"], poll, 0)
    closed = client.post(
        f"/api/v1/groups/{gid}/polls/{poll['id']}/close",
        headers=bearer(world["owner"]),
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    refused = _vote(client, world, world["second"], poll, 1)
    assert refused.status_code == 409
    # A member cannot close a poll — that door is organiser+.
    other = _poll(client, world, question="Second question?")
    refused = client.post(
        f"/api/v1/groups/{gid}/polls/{other['id']}/close",
        headers=bearer(world["member"]),
    )
    assert refused.status_code == 403


def test_closes_at_ends_voting(client, world):
    poll = _poll(client, world, closes_at="2020-01-01T00:00:00Z")
    refused = _vote(client, world, world["member"], poll, 0)
    assert refused.status_code == 409


def test_option_from_another_poll_is_refused(client, world):
    mine = _poll(client, world)
    other = _poll(client, world, question="Unrelated?")
    refused = client.post(
        f"/api/v1/groups/{world['group']['id']}/polls/{mine['id']}/vote",
        json={"option_id": other["options"][0]["id"]},
        headers=bearer(world["member"]),
    )
    assert refused.status_code == 404


def test_date_poll_needs_instants_and_labels_come_from_them(client, world):
    gid = world["group"]["id"]
    refused = client.post(
        f"/api/v1/groups/{gid}/polls",
        json={
            "question": "When?", "kind": "date",
            "options": [{"label": "Soon"}, {"label": "Later"}],
        },
        headers=bearer(world["owner"]),
    )
    assert refused.status_code == 422
    poll = _date_poll(client, world)
    # No labels were given: each option's words ARE its moment.
    assert all(row["label"] for row in poll["options"])
    assert poll["options"][0]["starts_at"] == "2026-11-07T20:00:00Z"


def test_date_poll_winner_mints_an_event(client, world):
    gid = world["group"]["id"]
    poll = _date_poll(client, world)
    _vote(client, world, world["member"], poll, 1)
    _vote(client, world, world["second"], poll, 1)
    applied = client.post(
        f"/api/v1/groups/{gid}/polls/{poll['id']}/apply-winner",
        json={}, headers=bearer(world["owner"]),
    )
    assert applied.status_code == 200, applied.text
    event = applied.json()
    assert event["status"] == "published"
    assert event["starts_at"] == "2026-11-14T20:00:00Z"
    after = client.get(
        f"/api/v1/groups/{gid}/polls/{poll['id']}", headers=bearer(world["owner"])
    ).json()
    assert after["applied_event_id"] == event["id"]


def test_date_poll_winner_moves_the_attached_event(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    created = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Cabin trip",
            "starts_at": "2026-11-01T15:00:00-05:00",
            "ends_at": "2026-11-03T11:00:00-05:00",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    event = created.json()
    poll = _date_poll(client, world, event_id=event["id"])
    _vote(client, world, world["member"], poll, 0)
    applied = client.post(
        f"/api/v1/groups/{gid}/polls/{poll['id']}/apply-winner",
        json={}, headers=headers,
    )
    assert applied.status_code == 200, applied.text
    moved = applied.json()
    assert moved["id"] == event["id"]
    assert moved["starts_at"] == "2026-11-07T20:00:00Z"
    # A move is a material change: the event says so.
    assert moved["needs_reannounce"] is True


def test_tied_date_poll_refuses_to_choose(client, world):
    gid = world["group"]["id"]
    poll = _date_poll(client, world)
    _vote(client, world, world["member"], poll, 0)
    _vote(client, world, world["second"], poll, 1)
    refused = client.post(
        f"/api/v1/groups/{gid}/polls/{poll['id']}/apply-winner",
        json={}, headers=bearer(world["owner"]),
    )
    assert refused.status_code == 409
    # And no event was minted behind the refusal.
    events = client.get(
        f"/api/v1/groups/{gid}/events", headers=bearer(world["owner"])
    ).json()
    assert events == []


def test_unvoted_and_choice_polls_refuse_the_calendar(client, world):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    date_poll = _date_poll(client, world)
    refused = client.post(
        f"/api/v1/groups/{gid}/polls/{date_poll['id']}/apply-winner",
        json={}, headers=headers,
    )
    assert refused.status_code == 409  # nobody voted
    choice = _poll(client, world)
    _vote(client, world, world["member"], choice, 0)
    refused = client.post(
        f"/api/v1/groups/{gid}/polls/{choice['id']}/apply-winner",
        json={}, headers=headers,
    )
    assert refused.status_code == 422  # only date polls touch the calendar
    # A plain member cannot apply a winner at all.
    _vote(client, world, world["second"], date_poll, 0)
    refused = client.post(
        f"/api/v1/groups/{gid}/polls/{date_poll['id']}/apply-winner",
        json={}, headers=bearer(world["member"]),
    )
    assert refused.status_code == 403
