"""B-080 — tombstones + undelete: deletion is a door that
swings both ways for thirty days, then closes for good.

Events soft-delete (the row stays; every read treats it as
gone) and restore whole — history, links, RSVPs. Sources keep
the B-023 law (chunks and provenance go at once) but are
snapshotted into their tombstone first, so a restore rebuilds
them with their original ids and lifts their candidates'
stamps. After the window, restore is refused in plain words
and the purge makes the ending final.
"""

from datetime import timedelta

from gpa.models.core import Tombstone
from gpa.models.events import Event
from gpa.models.groups import Candidate
from gpa.models.sources import Provenance, Source, SourceChunk
from gpa.services import sources as source_service
from gpa.services import tombstones as tombstone_service
from gpa.models.identity import now_utc
from conftest import bearer, make_group, register
from test_groups import invite_and_accept


def _event(client, owner, gid, **overrides):
    body = {
        "title": "Harvest Supper",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:30:00-05:00",
        "place_name": "Village Hall",
    }
    body.update(overrides)
    created = client.post(
        f"/api/v1/groups/{gid}/events", json=body, headers=bearer(owner)
    )
    assert created.status_code == 201, created.text
    return created.json()


def _world(client):
    owner = register(client, "tb-owner@example.org")
    gid = make_group(client, owner, home_timezone="America/New_York")["id"]
    return owner, gid


def test_delete_hides_event_and_restore_brings_it_back_whole(client):
    owner, gid = _world(client)
    event = _event(client, owner, gid)
    # Give the event a history and an answer, so "whole" means something.
    current = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    )
    patched = client.patch(
        f"/api/v1/groups/{gid}/events/{event['id']}",
        json={"cost_text": "£6"},
        headers={**bearer(owner), "If-Match": current.headers["etag"]},
    )
    assert patched.status_code == 200, patched.text
    rsvp = client.put(
        f"/api/v1/groups/{gid}/events/{event['id']}/rsvp",
        json={"status": "coming"}, headers=bearer(owner),
    )
    assert rsvp.status_code == 200, rsvp.text

    deleted = client.delete(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    )
    assert deleted.status_code == 204
    gone = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    )
    assert gone.status_code == 404
    listed = client.get(f"/api/v1/groups/{gid}/events", headers=bearer(owner)).json()
    assert listed == []
    stones = client.get(
        f"/api/v1/groups/{gid}/tombstones", headers=bearer(owner)
    ).json()
    assert len(stones) == 1
    assert stones[0]["entity_type"] == "event"
    assert stones[0]["entity_id"] == event["id"]
    assert stones[0]["restorable"] is True

    restored = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/restore",
        headers=bearer(owner),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["title"] == "Harvest Supper"
    assert restored.json()["cost_text"] == "£6"
    feed = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/changes",
        headers=bearer(owner),
    ).json()
    assert any(row["field"] == "cost_text" for row in feed)
    answers = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/rsvps",
        headers=bearer(owner),
    ).json()
    assert [row["status"] for row in answers["rsvps"]] == ["coming"]
    stones = client.get(
        f"/api/v1/groups/{gid}/tombstones", headers=bearer(owner)
    ).json()
    assert stones == []


def test_member_cannot_delete_an_event(client):
    owner, gid = _world(client)
    event = _event(client, owner, gid)
    member = invite_and_accept(client, owner, gid, "tb-member@example.org", "member")
    response = client.delete(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(member)
    )
    assert response.status_code == 403


def test_source_delete_snapshots_and_restore_rebuilds(client, db_session):
    owner, gid = _world(client)
    pasted = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={"title": "Chat", "text": "[21/10/2026, 23:02:00] Ana: See you Saturday"},
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text
    source_id = pasted.json()["id"]
    candidate = client.post(
        f"/api/v1/groups/{gid}/candidates",
        json={"kind": "event", "title": "Hall meetup"},
        headers=bearer(owner),
    ).json()
    chunk_row = (
        db_session.query(SourceChunk)
        .filter(SourceChunk.source_id == source_id)
        .first()
    )
    source_service.add_provenance(
        db_session, candidate_id=candidate["id"], chunk=chunk_row,
        field="title", quote="See you Saturday",
    )
    candidate_row = db_session.get(Candidate, candidate["id"])
    candidate_row.source_id = source_id
    db_session.commit()

    deleted = client.delete(
        f"/api/v1/groups/{gid}/sources/{source_id}", headers=bearer(owner)
    )
    assert deleted.status_code == 204
    db_session.expire_all()
    # The B-023 law, at full strength, immediately:
    assert db_session.query(SourceChunk).filter(
        SourceChunk.source_id == source_id
    ).count() == 0
    assert db_session.query(Provenance).count() == 0
    assert db_session.get(Candidate, candidate["id"]).source_deleted_at is not None
    assert db_session.get(Source, source_id) is None

    restored = client.post(
        f"/api/v1/groups/{gid}/sources/{source_id}/restore",
        headers=bearer(owner),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["id"] == source_id
    assert len(restored.json()["chunks"]) == 1
    db_session.expire_all()
    assert db_session.query(SourceChunk).filter(
        SourceChunk.source_id == source_id
    ).count() == 1
    assert db_session.query(Provenance).count() == 1
    assert db_session.get(Candidate, candidate["id"]).source_deleted_at is None


def _backdate_stone(db_session, entity_id, days=31):
    stone = (
        db_session.query(Tombstone)
        .filter(Tombstone.entity_id == entity_id)
        .one()
    )
    stone.purge_after = now_utc() - timedelta(days=1)
    stone.deleted_at = now_utc() - timedelta(days=days)
    db_session.commit()
    return stone


def test_restore_after_the_window_is_refused_plainly(client, db_session):
    owner, gid = _world(client)
    event = _event(client, owner, gid)
    assert client.delete(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    ).status_code == 204
    _backdate_stone(db_session, event["id"])
    response = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/restore",
        headers=bearer(owner),
    )
    assert response.status_code == 422
    assert "gone for good" in response.json()["detail"]


def test_purge_makes_the_ending_final(client, db_session):
    owner, gid = _world(client)
    expired = _event(client, owner, gid, title="Old Supper")
    kept = _event(client, owner, gid, title="Fresh Supper")
    for event in (expired, kept):
        assert client.delete(
            f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
        ).status_code == 204
    _backdate_stone(db_session, expired["id"])
    from gpa.models.groups import Group

    group = db_session.get(Group, gid)
    result = tombstone_service.purge_expired(db_session, group=group)
    assert result == {"tombstones": 1, "events_purged": 1}
    db_session.expire_all()
    assert db_session.get(Event, expired["id"]) is None
    assert db_session.get(Event, kept["id"]) is not None  # window still open
    stones = client.get(
        f"/api/v1/groups/{gid}/tombstones", headers=bearer(owner)
    ).json()
    assert [row["entity_id"] for row in stones] == [kept["id"]]
    # And the kept one still restores.
    assert client.post(
        f"/api/v1/groups/{gid}/events/{kept['id']}/restore",
        headers=bearer(owner),
    ).status_code == 200
