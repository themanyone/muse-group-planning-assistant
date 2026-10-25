"""Sources core acceptance (B-023): create/list/get/delete, the
permission record, chunk ordering, group scoping, and the
tombstone rule — deleting a source removes its chunks and their
provenance, and never touches a candidate drafted from it."""

from conftest import bearer, make_group, register
from test_groups import invite_and_accept


def _create_source(client, owner, group_id, **overrides):
    body = {
        "kind": "chat_paste",
        "title": "Saturday thread",
        "granted_scope": "this-thread-only",
        "chunks": [
            {"text": "See you Saturday", "sender": "Alice"},
            {"text": "Bring chairs", "sender": "Bob"},
        ],
    }
    body.update(overrides)
    return client.post(f"/api/v1/groups/{group_id}/sources", json=body, headers=bearer(owner))


def test_create_source_records_permission_and_order(client):
    owner = register(client)
    group = make_group(client, owner)
    response = _create_source(client, owner, group["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    # The permission record: who granted it is the caller, the scope
    # is verbatim, and chunk order is the order they were handed in.
    assert body["granted_by"] == f"user:{owner['user_id']}"
    assert body["granted_scope"] == "this-thread-only"
    assert body["status"] == "ready"
    assert body["chunk_count"] == 2
    assert [c["text"] for c in body["chunks"]] == ["See you Saturday", "Bring chairs"]
    assert [c["position"] for c in body["chunks"]] == [0, 1]
    assert body["chunks"][0]["sender"] == "Alice"


def test_list_and_get_source(client):
    owner = register(client)
    group = make_group(client, owner)
    created = _create_source(client, owner, group["id"]).json()
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [created["id"]]
    fetched = client.get(
        f"/api/v1/groups/{group['id']}/sources/{created['id']}", headers=bearer(owner)
    )
    assert fetched.status_code == 200
    assert len(fetched.json()["chunks"]) == 2


def test_unknown_kind_is_refused(client):
    owner = register(client)
    group = make_group(client, owner)
    response = _create_source(client, owner, group["id"], kind="carrier_pigeon")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_source_ids_do_not_leak_across_groups(client):
    owner = register(client)
    first = make_group(client, owner, "First Circle")
    second = make_group(client, owner, "Second Circle")
    created = _create_source(client, owner, first["id"]).json()
    response = client.get(
        f"/api/v1/groups/{second['id']}/sources/{created['id']}", headers=bearer(owner)
    )
    assert response.status_code == 404


def test_non_member_cannot_see_sources_of_private_group(client):
    owner = register(client)
    stranger = register(client)
    group = make_group(client, owner)
    _create_source(client, owner, group["id"])
    response = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(stranger))
    assert response.status_code == 404


def test_delete_needs_organiser_and_removes_chunks(client):
    owner = register(client)
    group = make_group(client, owner)
    member = invite_and_accept(client, owner, group["id"], "mem@example.org", "member")
    created = _create_source(client, owner, group["id"]).json()
    denied = client.delete(
        f"/api/v1/groups/{group['id']}/sources/{created['id']}", headers=bearer(member)
    )
    assert denied.status_code == 403
    gone = client.delete(
        f"/api/v1/groups/{group['id']}/sources/{created['id']}", headers=bearer(owner)
    )
    assert gone.status_code == 204
    missing = client.get(
        f"/api/v1/groups/{group['id']}/sources/{created['id']}", headers=bearer(owner)
    )
    assert missing.status_code == 404


def test_delete_leaves_candidate_as_tombstone(client, db_session):
    """The B-023 law: provenance and chunks go; the candidate —
    the thing drafted from the source — stays, text intact."""
    from gpa.models.groups import Candidate
    from gpa.models.sources import Provenance, SourceChunk
    from gpa.services import sources as source_service

    owner = register(client)
    group = make_group(client, owner)
    created = _create_source(client, owner, group["id"]).json()
    candidate = client.post(
        f"/api/v1/groups/{group['id']}/candidates",
        json={"kind": "event", "title": "Hall meetup"},
        headers=bearer(owner),
    ).json()
    chunk_row = (
        db_session.query(SourceChunk)
        .filter(SourceChunk.source_id == created["id"])
        .order_by(SourceChunk.position)
        .first()
    )
    source_service.add_provenance(
        db_session, candidate_id=candidate["id"], chunk=chunk_row,
        field="title", quote="See you Saturday",
    )
    candidate_row = db_session.get(Candidate, candidate["id"])
    candidate_row.source_id = created["id"]
    db_session.commit()

    deleted = client.delete(
        f"/api/v1/groups/{group['id']}/sources/{created['id']}", headers=bearer(owner)
    )
    assert deleted.status_code == 204
    # The API worked in its own session; drop this session's cached
    # copies so the assertions below read the committed truth.
    db_session.expire_all()
    assert db_session.query(SourceChunk).filter(
        SourceChunk.source_id == created["id"]
    ).count() == 0
    assert db_session.query(Provenance).count() == 0
    survivor = db_session.get(Candidate, candidate["id"])
    assert survivor is not None and survivor.title == "Hall meetup"
    # …and the tombstone is stamped: it remembers which source it
    # came from, and when that material went away.
    assert survivor.source_id == created["id"]
    assert survivor.source_deleted_at is not None
