"""Durability manager tests (roadmap B-171).

The three acceptances:

* **structural** — a handler that writes and forgets to
  commit does NOT answer 200-with-dirty-session; it answers
  the structural 500 and its writes are gone;
* **autosave conflict** — a stale autosave never overwrites:
  it is kept as a conflict copy and answered 409 with both
  versions;
* **resume after kill** — an upload begun and partly sent
  against one app instance resumes against a NEW instance
  over the same storage: status names the missing parts,
  only they are sent, and completion assembles a source
  whose hash verifies.
"""

import hashlib

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import bearer, make_group, make_settings, register
from gpa.db import get_db
from gpa.errors import AppError
from gpa.main import create_app
from gpa.models.groups import Group


def test_forgetting_handler_cannot_answer(tmp_path):
    """THE structural acceptance."""
    app = create_app(make_settings(tmp_path))

    @app.post("/api/v1/testing/forget")
    def forget(db: Session = Depends(get_db)):
        db.add(Group(name="Ghost", slug="ghost-group"))
        db.flush()
        return {"ok": True}  # no commit — the lie B-171 kills

    @app.post("/api/v1/testing/remember")
    def remember(db: Session = Depends(get_db)):
        db.add(Group(name="Kept", slug="kept-group"))
        db.commit()
        return {"ok": True}

    @app.post("/api/v1/testing/forget-and-refuse")
    def forget_and_refuse(db: Session = Depends(get_db)):
        db.add(Group(name="Refused", slug="refused-group"))
        db.flush()
        raise AppError("validation_error", detail="Refused on purpose.")

    with TestClient(app, raise_server_exceptions=False) as client:
        bad = client.post("/api/v1/testing/forget")
        assert bad.status_code == 500
        assert bad.json()["code"] == "uncommitted_writes"

        good = client.post("/api/v1/testing/remember")
        assert good.status_code == 200

        refused = client.post("/api/v1/testing/forget-and-refuse")
        assert refused.status_code == 422  # the handler's own error stands (validation_error)
        assert refused.json()["code"] == "validation_error"

    session = app.state.db.session()
    try:
        slugs = {g.slug for g in session.query(Group).all()}
        assert slugs == {"kept-group"}  # the ghost write was rolled back
    finally:
        session.close()


def test_autosave_conflict_keeps_both_versions(tmp_path):
    """THE autosave acceptance."""
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        owner = register(client)
        group = make_group(client, owner)
        url = f"/api/v1/groups/{group['id']}/autosaves/candidate/cand-1"

        first = client.put(url, json={"payload": {"title": "v1 words"}, "base_version": 0},
                           headers=bearer(owner))
        assert first.status_code == 200, first.text
        assert first.json()["version"] == 1

        second = client.put(url, json={"payload": {"title": "v2 words"}, "base_version": 1},
                            headers=bearer(owner))
        assert second.status_code == 200
        assert second.json()["version"] == 2

        stale = client.put(url, json={"payload": {"title": "stale words"}, "base_version": 1},
                           headers=bearer(owner))
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == "draft_conflict"

        state = client.get(url, headers=bearer(owner))
        assert state.status_code == 200
        body = state.json()
        assert body["payload"] == {"title": "v2 words"}  # the live draft won nothing away
        assert len(body["conflicts"]) == 1
        assert body["conflicts"][0]["payload"] == {"title": "stale words"}  # ...and the stale words are KEPT


def test_resumable_upload_survives_a_kill(tmp_path):
    """THE resume acceptance."""
    settings = make_settings(tmp_path)
    content = b"First part of the flyer. " + b"Second part here. " + b"Third and last."
    chunk = 25
    parts = [content[i : i + chunk] for i in range(0, len(content), chunk)]
    digest = hashlib.sha256(content).hexdigest()

    app_one = create_app(settings)
    with TestClient(app_one) as client:
        owner = register(client)
        group = make_group(client, owner)
        begin = client.post(
            f"/api/v1/groups/{group['id']}/uploads",
            json={"filename": "flyer.txt", "total_bytes": len(content),
                  "chunk_bytes": chunk, "sha256": digest},
            headers=bearer(owner),
        )
        assert begin.status_code == 201, begin.text
        upload_id = begin.json()["upload_id"]
        # Out of order — the last and the first, then "kill".
        for index in (len(parts) - 1, 0):
            sent = client.put(
                f"/api/v1/groups/{group['id']}/uploads/{upload_id}/parts/{index}",
                content=parts[index], headers=bearer(owner),
            )
            assert sent.status_code == 200, sent.text

    # A new instance over the same directories: the resume.
    app_two = create_app(settings)
    with TestClient(app_two) as client:
        owner2 = register(client, email="second@example.com")
        # The second user is not a member; resume as the owner
        # needs the owner's token — register() made a NEW user;
        # use the status through the owner instead: re-login
        # is register's job in these tests, so assert via a
        # fresh group member flow is overkill — the owner
        # token from instance one is a JWT for the same
        # secret, valid on instance two.
        status = client.get(
            f"/api/v1/groups/{group['id']}/uploads/{upload_id}",
            headers=bearer(owner),
        )
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["parts_received"] == [0, len(parts) - 1]
        assert body["parts_missing"] == list(range(1, len(parts) - 1))

        # A wrong-sized part is refused at its position.
        bad = client.put(
            f"/api/v1/groups/{group['id']}/uploads/{upload_id}/parts/1",
            content=b"short", headers=bearer(owner),
        )
        assert bad.status_code == 422
        assert bad.json()["code"] == "upload_part_size"

        for index in body["parts_missing"]:
            sent = client.put(
                f"/api/v1/groups/{group['id']}/uploads/{upload_id}/parts/{index}",
                content=parts[index], headers=bearer(owner),
            )
            assert sent.status_code == 200, sent.text

        done = client.post(
            f"/api/v1/groups/{group['id']}/uploads/{upload_id}/complete",
            headers=bearer(owner),
        )
        assert done.status_code == 201, done.text
        source_id = done.json()["source_id"]
        detail = client.get(
            f"/api/v1/groups/{group['id']}/sources/{source_id}",
            headers=bearer(owner),
        )
        assert detail.status_code == 200
        text = " ".join(c["text"] for c in detail.json()["chunks"])
        assert "Second part here" in text


def test_hash_mismatch_refuses_and_stays_open(tmp_path):
    settings = make_settings(tmp_path)
    content = b"some bytes that will not match the declared hash"
    app = create_app(settings)
    with TestClient(app) as client:
        owner = register(client)
        group = make_group(client, owner)
        begin = client.post(
            f"/api/v1/groups/{group['id']}/uploads",
            json={"filename": "x.txt", "total_bytes": len(content),
                  "chunk_bytes": len(content), "sha256": "0" * 64},
            headers=bearer(owner),
        )
        upload_id = begin.json()["upload_id"]
        client.put(
            f"/api/v1/groups/{group['id']}/uploads/{upload_id}/parts/0",
            content=content, headers=bearer(owner),
        )
        done = client.post(
            f"/api/v1/groups/{group['id']}/uploads/{upload_id}/complete",
            headers=bearer(owner),
        )
        assert done.status_code == 422
        assert done.json()["code"] == "upload_hash_mismatch"
        status = client.get(
            f"/api/v1/groups/{group['id']}/uploads/{upload_id}",
            headers=bearer(owner),
        )
        assert status.json()["status"] == "open"
