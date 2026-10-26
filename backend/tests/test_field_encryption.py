"""Field-level encryption tests (roadmap B-167).

The acceptance, in the roadmap's own words: *a raw-database
grep test — seeded secrets, names, and message text are
absent from the file, while the API serves them correctly to
authorised readers.* That test is here, plus the envelope
scheme's own laws: per-group keys wrapped under a master the
database never holds, seals bound to their group + field,
legacy plaintext still readable, and the blind index.
"""

import base64

import pytest

from conftest import bearer, make_group, register
from gpa.crypto import fields
from gpa.errors import AppError
from gpa.models.crypto import GroupKey
from gpa.models.sources import Source, SourceChunk
from gpa.services import sources as source_service

SECRET_TEXT = "the boathouse key is under the blue mat, tell no one"
SECRET_SENDER = "Zelda Nightjar"
SECRET_QUOTE = "boathouse key is under the blue mat"


def _db_bytes(app) -> bytes:
    """Every byte the database holds on disk: the main file
    AND its write-ahead log — under WAL + synchronous=FULL,
    recent pages live in the -wal file until a checkpoint,
    and a grep that skipped it would prove nothing."""
    url = app.state.settings.database_url
    assert url.startswith("sqlite:///")
    path = url.removeprefix("sqlite:///")
    blob = b""
    for suffix in ("", "-wal"):
        try:
            with open(path + suffix, "rb") as handle:
                blob += handle.read()
        except FileNotFoundError:
            pass
    return blob


def _world_with_secret(client):
    owner = register(client)
    group = make_group(client, owner)
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources",
        json={
            "kind": "chat_paste",
            "title": "Weekend thread",
            "granted_scope": "this-thread-only",
            "chunks": [{"text": SECRET_TEXT, "sender": SECRET_SENDER}],
        },
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    return owner, group, response.json()


def test_raw_database_grep_finds_nothing(app, client):
    """THE B-167 acceptance test: the seeded secret text and
    the sender's name are nowhere in the database file —
    while the API serves both, whole, to an authorised
    reader, in the same breath."""
    owner, group, created = _world_with_secret(client)
    # Served correctly first, so the grep cannot pass by the
    # data simply never having been stored.
    assert created["chunks"][0]["text"] == SECRET_TEXT
    assert created["chunks"][0]["sender"] == SECRET_SENDER

    blob = _db_bytes(app)
    assert SECRET_TEXT.encode() not in blob
    assert SECRET_SENDER.encode() not in blob
    assert b"boathouse" not in blob
    # The stored form is present and versioned — the seal is
    # in the file, the content is not.
    assert b"enc:v1:" in blob


def test_per_group_keys_are_wrapped_and_distinct(app, client, db_session):
    """Each group gets its own random data key, stored only
    wrapped; the unwrapped key appears nowhere in the file,
    and two groups' keys differ."""
    owner = register(client)
    group_a = make_group(client, owner, name="A")
    group_b = make_group(client, owner, name="B")
    for group in (group_a, group_b):
        client.post(
            f"/api/v1/groups/{group['id']}/sources",
            json={"kind": "chat_paste", "title": "t", "granted_scope": "s",
                  "chunks": [{"text": "hello", "sender": "x"}]},
            headers=bearer(owner),
        )
    rows = db_session.query(GroupKey).all()
    assert {row.group_id for row in rows} == {group_a["id"], group_b["id"]}
    key_a = fields.data_key(db_session, group_a["id"])
    key_b = fields.data_key(db_session, group_b["id"])
    assert key_a != key_b
    blob = _db_bytes(app)
    assert base64.b64encode(key_a) not in blob
    assert base64.b64encode(key_b) not in blob


def test_seals_are_bound_to_group_and_field(db_session):
    """A seal opened under the wrong group or the wrong field
    is tampering, and says so — ciphertext cannot wander."""
    group = _bare_group(db_session)
    sealed = fields.seal_text(db_session, group, "chunk.text", SECRET_TEXT)
    assert fields.open_text(db_session, group, "chunk.text", sealed) == SECRET_TEXT
    with pytest.raises(AppError) as caught:
        fields.open_text(db_session, "another-group", "chunk.text", sealed)
    assert caught.value.code == "internal_tampered"
    with pytest.raises(AppError):
        fields.open_text(db_session, group, "chunk.sender", sealed)


def _bare_group(db_session) -> str:
    from gpa.models.groups import Group

    group = Group(name="Sealing Circle", slug="sealing-circle", home_timezone="UTC")
    db_session.add(group)
    db_session.commit()
    return group.id


def test_provenance_quotes_are_sealed_too(client, db_session):
    """A quote is an excerpt of the same raw text: stored
    sealed, opened through the one opening."""
    owner, group, created = _world_with_secret(client)
    source = db_session.get(Source, created["id"])
    chunk = source_service.list_chunks(db_session, source)[0]
    from gpa.models.groups import Candidate

    candidate = Candidate(
        group_id=group["id"], kind="event", title="t",
        created_by="user:x", status="pending",
    )
    db_session.add(candidate)
    db_session.commit()
    row = source_service.add_provenance(
        db_session, candidate_id=candidate.id, chunk=chunk,
        field="title", quote=SECRET_QUOTE,
    )
    db_session.commit()
    db_session.refresh(row)
    assert row.quote.startswith(fields.FIELD_PREFIX)
    assert SECRET_QUOTE not in row.quote
    assert source_service.open_quote(db_session, row) == SECRET_QUOTE


def test_legacy_plaintext_chunks_still_read(client, db_session):
    """A chunk written before B-167 (plaintext in the column)
    reads as itself — the upgrade strands nothing."""
    owner = register(client)
    group = make_group(client, owner)
    source = Source(group_id=group["id"], kind="manual", title="old", granted_by="user:x")
    db_session.add(source)
    db_session.flush()
    db_session.add(
        SourceChunk(source_id=source.id, position=0, text="plain old words", sender="Pete")
    )
    db_session.commit()
    chunks = source_service.list_chunks(db_session, source)
    assert chunks[0].text == "plain old words"
    assert chunks[0].sender == "Pete"


def test_blind_index_is_deterministic_and_one_way():
    """The lookup token: stable for a value (lookups work),
    different across values, and not the value."""
    token = fields.blind_index("ana@example.org")
    assert token == fields.blind_index("ana@example.org")
    assert token != fields.blind_index("bob@example.org")
    assert "ana" not in token
