"""Group export + erasure (roadmap B-143) — the acceptance
test, taken literally: build a group full of unique strings,
EXPORT it (the archive must be real and complete, and carry
no secrets), then ERASE it and grep — the raw database file,
the storage directory, the audit log. The group's strings
are gone; the neighbouring group, sharing the same database
and even one identical stored file, is untouched.
"""

import io
import json
import zipfile
from pathlib import Path

from conftest import bearer, make_group, register
from test_groups import invite_and_accept

from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.services import jobs as job_service
from gpa.services import sources as source_service

SHARED_BYTES = b"Shared lantern inventory, identical in both groups."


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (30, 120, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def _rich_group(client, app, db_session, mark: str, *, with_candidate=True):
    """One owner, one member, and a group full of marked
    content across the domains. Returns the world."""
    owner = register(client)
    group = make_group(client, owner, name=f"{mark} Hearth Circle")
    gid = group["id"]
    headers = bearer(owner)
    member = invite_and_accept(
        client, owner, gid, f"member-{mark.lower()}@example.org", "member"
    )

    pasted = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={"title": f"{mark} chat",
              "text": f"[21/10/2026, 23:02:00] Ana: {mark} pumpkin stew night, Friday 7pm at the hall"},
        headers=headers,
    )
    assert pasted.status_code == 201, pasted.text
    uploaded = client.post(
        f"/api/v1/groups/{gid}/sources/upload",
        params={"filename": "shared.txt"},
        content=SHARED_BYTES,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text
    own_file = client.post(
        f"/api/v1/groups/{gid}/sources/upload",
        params={"filename": "own.txt"},
        content=f"{mark} own file words about lanterns".encode(),
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert own_file.status_code == 201, own_file.text
    image = client.post(
        f"/api/v1/groups/{gid}/sources/image",
        params={"filename": "poster.png"},
        content=_png_bytes(),
        headers={**headers, "Content-Type": "image/png"},
    )
    assert image.status_code == 201, image.text

    event = client.post(
        f"/api/v1/groups/{gid}/events",
        json={"title": f"{mark} Lantern Supper",
              "starts_at": "2026-11-06T18:30:00+00:00",
              "ends_at": "2026-11-06T21:00:00+00:00"},
        headers=headers,
    )
    assert event.status_code == 201, event.text
    rsvp = client.put(
        f"/api/v1/groups/{gid}/events/{event.json()['id']}/rsvp",
        json={"status": "coming"}, headers=bearer(member),
    )
    assert rsvp.status_code == 200, rsvp.text
    comment = client.post(
        f"/api/v1/groups/{gid}/events/{event.json()['id']}/comments",
        json={"body": f"{mark} comment words"}, headers=bearer(member),
    )
    assert comment.status_code == 201, comment.text
    poll = client.post(
        f"/api/v1/groups/{gid}/polls",
        json={"question": f"{mark} poll question?",
              "options": [{"label": "Yes"}, {"label": "No"}]},
        headers=headers,
    )
    assert poll.status_code == 201, poll.text
    vote = client.post(
        f"/api/v1/groups/{gid}/polls/{poll.json()['id']}/vote",
        json={"option_id": poll.json()["options"][0]["id"]},
        headers=bearer(member),
    )
    assert vote.status_code in (200, 201), vote.text
    task = client.post(
        f"/api/v1/groups/{gid}/tasks",
        json={"title": f"{mark} task words"}, headers=headers,
    )
    assert task.status_code == 201, task.text
    subgroup = client.post(
        f"/api/v1/groups/{gid}/subgroups",
        json={"name": f"{mark} Crew"}, headers=headers,
    )
    assert subgroup.status_code == 201, subgroup.text
    announcement = client.post(
        f"/api/v1/groups/{gid}/announcements",
        json={"title": f"{mark} Announcement", "body": "Read me."},
        headers=headers,
    )
    assert announcement.status_code == 201, announcement.text
    integration = client.post(
        f"/api/v1/groups/{gid}/integrations",
        json={"kind": "samtal", "name": f"{mark} Link",
              "config": {"api_token": f"{mark}-SECRET-VALUE"}},
        headers=headers,
    )
    assert integration.status_code == 201, integration.text
    feed = client.post(
        f"/api/v1/groups/{gid}/calendar/feeds",
        json={"filters": {}}, headers=headers,
    )
    assert feed.status_code == 201, feed.text

    if with_candidate:
        # One extracted candidate, approved — provenance,
        # approvals and a minted event join the record.
        from gpa.models.sources import Source

        source_row = db_session.get(Source, pasted.json()["id"])
        chunks = source_service.list_chunks(db_session, source_row)
        answer = {
            "items": [{
                "fields": {
                    "title": {"value": f"{mark} Stew Night", "confidence": 0.9,
                              "quote": "pumpkin stew night"},
                    "starts": {"value": "Friday 7pm", "confidence": 0.9,
                               "quote": "Friday 7pm", "date_phrase": "Friday",
                               "time_phrase": "7pm"},
                    "place": {"value": "the hall", "confidence": 0.85,
                              "quote": "at the hall"},
                    "host": {"value": None, "confidence": 0.0, "quote": ""},
                }
            }]
        }
        driver = FixtureDriver()
        driver.add_json(
            build_prompt("extraction", chunks, "UTC"),
            promptlib.load_prompt("extraction").schema, answer,
        )
        ai_client.set_client(driver)
        try:
            extract = client.post(
                f"/api/v1/groups/{gid}/sources/{pasted.json()['id']}/extract",
                json={"kinds": ["event"]}, headers=headers,
            )
            assert extract.status_code == 202, extract.text
            assert job_service.process_one(
                db_session, app.state.settings, "test-worker"
            )
        finally:
            ai_client.set_client(None)
        queue = client.get(f"/api/v1/groups/{gid}/candidates", headers=headers)
        (candidate,) = queue.json()
        approved = client.post(
            f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
            headers=headers,
        )
        assert approved.status_code == 200, approved.text

    session = app.state.db.session()
    try:
        job_service.enqueue(
            session, app.state.settings, kind="digest_run",
            payload={"group_id": gid},
        )
        session.commit()
    finally:
        session.close()

    return {
        "owner": owner, "member": member, "group": group, "gid": gid,
        "feed_token": feed.json()["token"],
        "event_id": event.json()["id"],
    }


def _db_bytes(app) -> bytes:
    url = app.state.settings.database_url
    path = Path(url.split("sqlite:///", 1)[1])
    data = path.read_bytes()
    wal = Path(str(path) + "-wal")
    if wal.exists():
        data += wal.read_bytes()
    return data


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------


def test_export_is_a_real_archive_without_secrets(client, app, db_session):
    world = _rich_group(client, app, db_session, "UNIQUEA")
    gid, owner = world["gid"], world["owner"]

    member_try = client.post(
        f"/api/v1/groups/{gid}/export", headers=bearer(world["member"])
    )
    assert member_try.status_code == 403

    response = client.post(f"/api/v1/groups/{gid}/export", headers=bearer(owner))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    for expected in (
        "manifest.json", "group.json", "members.json", "sources.json",
        "events.json", "events.ics", "planning.json", "candidates.json",
        "integrations.json", "audit.json",
    ):
        assert expected in names, expected

    sources = json.loads(archive.read("sources.json"))
    assert "UNIQUEA pumpkin stew night" in json.dumps(sources)
    events = json.loads(archive.read("events.json"))
    assert "UNIQUEA Lantern Supper" in json.dumps(events)
    assert "UNIQUEA Lantern Supper" in archive.read("events.ics").decode()
    candidates = json.loads(archive.read("candidates.json"))
    assert "UNIQUEA Stew Night" in json.dumps(candidates)

    integrations = json.loads(archive.read("integrations.json"))
    rendered = json.dumps(integrations)
    assert "api_token" in rendered  # the key NAME travels…
    assert "UNIQUEA-SECRET-VALUE" not in rendered  # …never the value
    whole = b"\n".join(archive.read(name) for name in names)
    assert b"config_sealed" not in whole
    assert b"token_hash" not in whole
    assert b"password_hash" not in whole

    # The stored files ride as themselves.
    file_names = [n for n in names if n.startswith("files/")]
    assert file_names
    contents = [archive.read(n) for n in file_names]
    assert SHARED_BYTES in contents
    assert b"UNIQUEA own file words about lanterns" in contents

    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["group_id"] == gid
    assert "never exported" in manifest["withheld"]


# ------------------------------------------------------------------
# Erasure
# ------------------------------------------------------------------


def test_erase_then_grep(client, app, db_session):
    a = _rich_group(client, app, db_session, "UNIQUEA")
    b = _rich_group(client, app, db_session, "UNIQUEB", with_candidate=False)
    gid_a, gid_b = a["gid"], b["gid"]
    owner_a = a["owner"]

    # The shared file object exists once, for both groups.
    from gpa.models.core import FileAsset

    session = app.state.db.session()
    try:
        keys_a = {
            row.storage_key
            for row in session.query(FileAsset).filter(
                FileAsset.group_id == gid_a
            )
        }
        own_key_a = {
            row.storage_key
            for row in session.query(FileAsset).filter(
                FileAsset.group_id == gid_a,
                FileAsset.filename == "own.txt",
            )
        }
    finally:
        session.close()
    storage = app.state.storage
    assert own_key_a and all(storage.exists(k) for k in keys_a)

    # Refusals first: a member, and the owner through an API
    # key, and the owner with the wrong words — group intact.
    member_try = client.post(
        f"/api/v1/groups/{gid_a}/erase",
        json={"confirm_name": a["group"]["name"], "confirm_word": "ERASE"},
        headers=bearer(a["member"]),
    )
    assert member_try.status_code == 403
    key = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "erase key", "scopes": ["read", "draft", "publish", "admin"]},
        headers=bearer(owner_a),
    )
    assert key.status_code == 201, key.text
    key_try = client.post(
        f"/api/v1/groups/{gid_a}/erase",
        json={"confirm_name": a["group"]["name"], "confirm_word": "ERASE"},
        headers={"X-Api-Key": key.json()["key"]},
    )
    assert key_try.status_code == 403
    wrong = client.post(
        f"/api/v1/groups/{gid_a}/erase",
        json={"confirm_name": "Not The Name", "confirm_word": "ERASE"},
        headers=bearer(owner_a),
    )
    assert wrong.status_code == 422
    assert client.get(
        f"/api/v1/groups/{gid_a}", headers=bearer(owner_a)
    ).status_code == 200

    erased = client.post(
        f"/api/v1/groups/{gid_a}/erase",
        json={"confirm_name": a["group"]["name"], "confirm_word": "ERASE"},
        headers=bearer(owner_a),
    )
    assert erased.status_code == 200, erased.text
    body = erased.json()
    assert body["erased"] is True
    for table in ("groups", "events", "sources", "source_chunks",
                  "candidates", "polls", "tasks", "integrations",
                  "calendar_feeds", "jobs"):
        assert body["deleted"].get(table, 0) >= 1, table
    assert body["storage_objects_removed"] >= 1

    # The group is gone from every surface; its feed is dead.
    assert client.get(
        f"/api/v1/groups/{gid_a}", headers=bearer(owner_a)
    ).status_code == 404
    feed = client.get(f"/api/v1/calendar/feed/{a['feed_token']}.ics")
    assert feed.status_code == 404

    # THE GREP: the raw database file carries none of A's
    # strings — and still carries B's (we read the right file).
    data = _db_bytes(app)
    for marker in (
        b"UNIQUEA Hearth Circle", b"UNIQUEA pumpkin stew night",
        b"UNIQUEA Lantern Supper", b"UNIQUEA Stew Night",
        b"UNIQUEA own file words", b"UNIQUEA comment words",
        b"UNIQUEA poll question", b"UNIQUEA task words",
        b"UNIQUEA-SECRET-VALUE",
    ):
        assert marker not in data, marker
    assert b"UNIQUEB Lantern Supper" in data
    # B's chunk text is sealed at rest (B-167), so it is NOT
    # in the raw file even though B is alive and well — B's
    # survival is proven where survival means something now:
    # the API still serves B's source, words whole.
    assert b"UNIQUEB pumpkin stew night" not in data
    b_sources = client.get(
        f"/api/v1/groups/{gid_b}/sources", headers=bearer(b["owner"])
    )
    assert b_sources.status_code == 200
    b_source_id = b_sources.json()[0]["id"]
    b_detail = client.get(
        f"/api/v1/groups/{gid_b}/sources/{b_source_id}", headers=bearer(b["owner"])
    )
    assert "UNIQUEB pumpkin stew night" in json.dumps(b_detail.json())

    # Storage: A's own object is gone; the SHARED object —
    # B's identical file — survives.
    (own_key,) = own_key_a
    assert not storage.exists(own_key)
    shared_key = (keys_a & {
        row.storage_key
        for row in db_session.query(FileAsset).filter(
            FileAsset.group_id == gid_b
        )
    })
    assert shared_key and all(storage.exists(k) for k in shared_key)

    # The audit keeps only the fact.
    from gpa.models.core import AuditLog

    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.group_id == gid_a)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].action == "group.erased"
    assert rows[0].detail_json in ("{}", "")

    # B is untouched, and A's people still exist.
    assert client.get(
        f"/api/v1/groups/{gid_b}", headers=bearer(b["owner"])
    ).status_code == 200
    b_events = client.get(
        f"/api/v1/groups/{gid_b}/events", headers=bearer(b["owner"])
    )
    assert any(
        e["title"] == "UNIQUEB Lantern Supper" for e in b_events.json()
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": owner_a["email"], "password": owner_a["password"]},
    )
    assert login.status_code == 200, login.text
