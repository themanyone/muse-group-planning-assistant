"""Privacy surfaces (roadmap B-144).

- ``GET /users/me/data`` — a person's whole footprint, in
  plain sections, with no hash or secret anywhere in it;
- ``GET /groups/{id}/sources/permissions`` — who shared
  what, on what permission, readable by the group's members;
- retention — a group that sets a window watches its old
  raw sources hard-expire (no tombstone: expiry is a privacy
  act), while the candidates they produced keep their
  tombstone stamp and approved events stand.
"""

from datetime import timedelta

from conftest import bearer, make_group, register
from test_groups import invite_and_accept

from gpa.models.identity import now_utc
from gpa.models.groups import Candidate
from gpa.models.sources import Source


def _world(client):
    owner = register(client)
    group = make_group(client, owner, name="Privacy Circle")
    gid = group["id"]
    member = invite_and_accept(
        client, owner, gid, "privacy-member@example.org", "member"
    )
    event = client.post(
        f"/api/v1/groups/{gid}/events",
        json={"title": "Privacy Supper",
              "starts_at": "2026-11-06T18:30:00+00:00",
              "ends_at": "2026-11-06T21:00:00+00:00"},
        headers=bearer(owner),
    ).json()
    return owner, group, member, event


# ------------------------------------------------------------------
# /users/me/data
# ------------------------------------------------------------------


def test_me_data_is_the_whole_footprint_without_secrets(client, db_session):
    owner, group, member, event = _world(client)
    gid = group["id"]
    mh = bearer(member)

    key = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "my laptop", "scopes": ["read"]}, headers=mh,
    )
    assert key.status_code == 201, key.text
    raw_key = key.json()["key"]
    client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/comments",
        json={"body": "Bringing the privacy pudding."}, headers=mh,
    )
    client.put(
        f"/api/v1/groups/{gid}/events/{event['id']}/rsvp",
        json={"status": "coming"}, headers=mh,
    )
    poll = client.post(
        f"/api/v1/groups/{gid}/polls",
        json={"question": "Pudding or pie?",
              "options": [{"label": "Pudding"}, {"label": "Pie"}]},
        headers=bearer(owner),
    ).json()
    client.post(
        f"/api/v1/groups/{gid}/polls/{poll['id']}/vote",
        json={"option_id": poll["options"][0]["id"]}, headers=mh,
    )

    response = client.get("/api/v1/users/me/data", headers=mh)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["account"]["email"] == member["email"]
    assert data["account"]["member_since"]
    (mine,) = data["my_groups"]
    assert mine["group_name"] == "Privacy Circle"
    assert mine["role"] == "member"
    (key_row,) = data["my_api_keys"]
    assert key_row["label"] == "my laptop"
    assert key_row["prefix"] == key.json()["prefix"]
    assert [c["body"] for c in data["comments_i_wrote"]] == [
        "Bringing the privacy pudding."
    ]
    assert data["my_rsvps"][0]["event_title"] == "Privacy Supper"
    assert data["my_rsvps"][0]["status"] == "coming"
    assert data["my_poll_votes"][0]["question"] == "Pudding or pie?"

    # No secret material anywhere in the payload: not the raw
    # key, not its hash, not the password hash — checked
    # against the real stored values, not just field names.
    from gpa.models.identity import ApiKey, User

    user_row = db_session.get(User, member["user_id"])
    key_row_db = (
        db_session.query(ApiKey).filter(ApiKey.user_id == member["user_id"]).one()
    )
    text = response.text
    assert raw_key not in text
    assert key_row_db.key_hash not in text
    assert user_row.password_hash not in text
    assert "password_hash" not in text and "key_hash" not in text

    # And it is PERSONAL: another member's record is their own.
    other = client.get("/api/v1/users/me/data", headers=bearer(owner))
    assert other.json()["comments_i_wrote"] == []
    assert other.json()["my_api_keys"] == []


def test_me_data_refuses_seats(client):
    owner, group, member, event = _world(client)
    seat = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={"name": "Privacy Seat", "role": "member"},
        headers=bearer(owner),
    )
    assert seat.status_code == 201, seat.text
    response = client.get(
        "/api/v1/users/me/data", headers={"X-Api-Key": seat.json()["key"]}
    )
    assert response.status_code == 403


# ------------------------------------------------------------------
# Per-source permissions, in plain words
# ------------------------------------------------------------------


def test_source_permissions_listing(client, db_session):
    owner, group, member, event = _world(client)
    gid = group["id"]
    pasted = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={
            "title": "Hall chat",
            "text": "Ana: supper on Friday",
            "granted_scope": "Shared by the hall committee for planning",
        },
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text
    uploaded = client.post(
        f"/api/v1/groups/{gid}/sources/upload",
        params={"filename": "notes.txt"},
        content=b"Notes with no permission note attached.",
        headers={**bearer(owner), "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text

    listing = client.get(
        f"/api/v1/groups/{gid}/sources/permissions", headers=bearer(member)
    )
    assert listing.status_code == 200, listing.text
    rows = {row["title"]: row for row in listing.json()}
    assert rows["Hall chat"]["permission"] == (
        "Shared by the hall committee for planning"
    )
    from gpa.models.identity import User

    owner_name = db_session.get(User, owner["user_id"]).name
    assert rows["Hall chat"]["added_by"] == owner_name
    assert rows["notes.txt"]["permission"] == (
        "No permission note was recorded for this source."
    )
    outsider = register(client)
    refused = client.get(
        f"/api/v1/groups/{gid}/sources/permissions", headers=bearer(outsider)
    )
    assert refused.status_code in (403, 404)


# ------------------------------------------------------------------
# Retention
# ------------------------------------------------------------------


def _paste(client, headers, gid, title, text):
    response = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={"title": title, "text": text}, headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_retention_expires_old_sources_only(client, app, db_session):
    owner = register(client)
    group = make_group(client, owner, name="Retention Circle")
    gid = group["id"]
    headers = bearer(owner)
    old_id = _paste(client, headers, gid, "Old chat", "Ana: RETOLD stew night")
    new_id = _paste(client, headers, gid, "New chat", "Ana: RETNEW picnic day")

    # Backdate the old source past the window the group is
    # about to choose; the new one stays inside it.
    old = db_session.get(Source, old_id)
    old.created_at = now_utc() - timedelta(days=90)
    db_session.commit()

    # A candidate born from the old source (as extraction
    # would leave it): it must SURVIVE expiry, stamped.
    candidate = Candidate(
        group_id=gid, source_id=old_id, kind="event", status="pending",
        draft_json="{}", created_by=f"user:{owner['user_id']}",
        prompt_name="extraction", prompt_version=1,
    )
    db_session.add(candidate)
    db_session.commit()

    # No window yet: the manual sweep honestly expires nothing.
    response = client.post(f"/api/v1/groups/{gid}/retention/run", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["sources_expired"] == 0
    assert db_session.get(Source, old_id) is not None

    # Set a 30-day window: the settings save anchors the
    # nightly chain (one queued retention_sweep job)…
    saved = client.put(
        f"/api/v1/groups/{gid}/settings",
        json={"source_retention_days": 30}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    from gpa.models.core import Job

    queued = [
        j for j in db_session.query(Job).all()
        if j.kind == "retention_sweep"
    ]
    assert len(queued) == 1

    # …and running the sweep now expires only the old source.
    response = client.post(f"/api/v1/groups/{gid}/retention/run", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["sources_expired"] == 1
    db_session.expire_all()
    assert db_session.get(Source, old_id) is None
    assert db_session.get(Source, new_id) is not None
    survivor = db_session.get(Candidate, candidate.id)
    assert survivor is not None
    assert survivor.source_deleted_at is not None

    # The listing agrees, and the audit kept counts, not words.
    listing = client.get(f"/api/v1/groups/{gid}/sources", headers=headers)
    assert [s["id"] for s in listing.json()] == [new_id]
    from gpa.models.core import AuditLog

    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "source.expired")
        .all()
    )
    assert len(rows) == 1
    assert "RETOLD" not in rows[0].detail_json

    # Lifting the window cancels the chain.
    lifted = client.put(
        f"/api/v1/groups/{gid}/settings",
        json={"source_retention_days": None}, headers=headers,
    )
    assert lifted.status_code == 200
    db_session.expire_all()
    outstanding = [
        j for j in db_session.query(Job).all()
        if j.kind == "retention_sweep" and j.status == "queued"
    ]
    assert outstanding == []
