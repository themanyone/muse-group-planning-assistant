"""PostgreSQL parity (roadmap B-152, decision D-06).

D-06 said the models were engine-portable and PostgreSQL was a
URL swap away. This file is where that sentence is PROVEN, seven
ways, against a real PostgreSQL server process:

1. The whole Alembic chain migrates a fresh PostgreSQL
   database up, down to base, and up again — and the resulting
   schema is the models' schema.
2. The data mover (scripts/migrate_to_postgres.py) copies a
   rich SQLite database — the full demo story — into an empty
   PostgreSQL one, and every table's row count AND content
   hash agree; a second run refuses the now-non-empty target
   in plain words.
3. The core product flow runs on PostgreSQL end to end
   (register → group → source → event → agenda), including
   group ERASURE — whose compact step is VACUUM (FULL) here,
   not the SQLite file purge.
4. An integration secret sealed on PostgreSQL is absent from
   the RAW stored column — the server-engine equivalent of
   the SQLite file grep (tests/test_integrations_framework.py
   carries the file version; conftest's PG mode skips it
   there and this file is where the guarantee lives on PG).
5. Field seals and group keys (B-167's twins, added at the
   Phase 17 gate): the raw stored chunk columns hold
   ``enc:v1:`` seals — never the secret text or sender — and
   each group's data key rests wrapped under the master and
   distinct from every other group's, the unwrapped keys
   absent from everything at rest.
6. Backup key material (B-169's twin): the B-146 bundle is a
   SQLite artifact, so backup.py REFUSES a PostgreSQL
   deployment in plain words — and the operator's real backup
   surface on PG, the server's stored values, holds neither
   the master key, nor an unwrapped data key, nor any of the
   recovery codes the escrow keeps.
7. Data repair (B-173's twin): every repairable damage kind
   is planted on PostgreSQL and fixed forward by one repair
   run, the audit naming each fix — and the one kind that
   cannot be planted, orphaned provenance, cannot exist here
   at all, because the server enforces the foreign keys
   itself and refuses the orphan insert.

The file runs ONLY when GPA_TEST_PG_ADMIN_URL names a real
PostgreSQL server the tests may create and drop scratch
databases on (e.g. postgresql+psycopg://postgres@127.0.0.1:
54329/postgres). Without one it skips — a named absence in
the SQLite run's report, never a silent pass. DEPLOYMENT.md
("PostgreSQL promotion") records how the throwaway cluster
for this run was stood up.
"""

import base64
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from conftest import bearer, make_group, register
from gpa import models  # noqa: F401 — registers every table
from gpa.config import Settings
from gpa.crypto import fields
from gpa.db import Base, Database
from gpa.main import create_app
from gpa.models.core import AuditLog, Job
from gpa.models.crypto import GroupKey, RecoveryEscrow
from gpa.models.groups import Candidate, Group
from gpa.models.notifications import Digest
from gpa.models.sources import Flyer, Source, SourceChunk
from gpa.services import digests as digest_service
from gpa.services import keymgmt
from gpa.services import repair as repair_service
from gpa.services.demo import load_demo_story

ADMIN_URL = os.environ.get("GPA_TEST_PG_ADMIN_URL", "")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL,
    reason=(
        "GPA_TEST_PG_ADMIN_URL not set — PostgreSQL parity needs a "
        "real PostgreSQL server (see DEPLOYMENT.md, B-152)"
    ),
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The same stand-in master the key-management tests use
# (tests/test_key_management.py): 32 bytes, base64-carried.
MASTER_A = base64.b64encode(b"A" * 32).decode()


def _admin_engine():
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


@pytest.fixture()
def pg_url():
    """A fresh, empty PostgreSQL database for one test."""
    name = f"gpa_parity_{uuid.uuid4().hex[:12]}"
    engine = _admin_engine()
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()
    yield str(make_url(ADMIN_URL).set(database=name))
    engine = _admin_engine()
    with engine.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    engine.dispose()


def _pg_settings(pg_url: str, tmp_path: Path, **overrides) -> Settings:
    values = dict(
        env="test",
        database_url=pg_url,
        secret_key="test-secret-for-tests-only",
        storage_dir=tmp_path / "storage",
        outbox_dir=tmp_path / "outbox",
        auth_rate_per_minute=1000,
        job_backoff_base_seconds=0,
        auto_create_tables=True,
    )
    values.update(overrides)
    return Settings(**values)


def _alembic(pg_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GPA_DATABASE_URL=pg_url)
    env.pop("GPA_TEST_PG_URL", None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_migration_chain_on_postgres(pg_url):
    """Up the whole chain, down to base, up again (B-152)."""
    up = _alembic(pg_url, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr

    engine = create_engine(pg_url)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    engine.dispose()
    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert expected <= tables, expected - tables

    down = _alembic(pg_url, "downgrade", "base")
    assert down.returncode == 0, down.stdout + down.stderr
    engine = create_engine(pg_url)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    engine.dispose()
    assert tables <= {"alembic_version"}, tables

    up_again = _alembic(pg_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stdout + up_again.stderr


def test_mover_counts_hashes_and_refusals(pg_url, tmp_path):
    """The demo story, moved and proven; the guards refuse (B-152)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from migrate_to_postgres import MoverError, migrate

    # A rich source: the full demo story on SQLite.
    source_url = f"sqlite:///{tmp_path}/source.db"
    database = Database(source_url)
    database.create_all()
    session = database.session()
    try:
        result = load_demo_story(session, REPO_ROOT / "seeds")
    finally:
        session.close()
    assert result  # the story actually loaded

    report = migrate(source_url, pg_url, log=lambda *_: None)
    assert report, "the mover verified nothing"
    mismatches = {name: row for name, row in report.items() if not row["ok"]}
    assert not mismatches, mismatches
    assert report["users"]["source_count"] > 0
    assert report["events"]["source_count"] > 0
    assert report["sources"]["source_count"] > 0

    # The target is a working database, not a museum: a new row
    # with a fresh id lands beside the moved ones. The column
    # list speaks the CURRENT schema: B-174 added NOT NULL
    # account-security columns (migration 0021 gives them
    # server defaults on the alembic road; a raw insert names
    # their resting values itself, as this one now does).
    engine = create_engine(pg_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, "
                "timezone, locale, created_at, mfa_enabled, "
                "mfa_last_step, failed_login_attempts) VALUES "
                "(:id, :email, 'Post Move', NULL, 'UTC', 'en', now(), "
                "0, -1, 0)"
            ),
            {"id": uuid.uuid4().hex, "email": f"post-{uuid.uuid4().hex[:8]}@example.org"},
        )
    engine.dispose()

    # A second move into the now-non-empty target refuses.
    with pytest.raises(MoverError, match="must be empty"):
        migrate(source_url, pg_url, log=lambda *_: None)

    # And the directions are guarded in plain words.
    with pytest.raises(MoverError, match="PostgreSQL"):
        migrate(source_url, source_url, log=lambda *_: None)


def test_core_flow_and_erasure_on_postgres(pg_url, tmp_path):
    """The product runs on PG — including erasure's VACUUM (FULL)."""
    settings = _pg_settings(pg_url, tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        owner = register(client)
        group = make_group(client, owner, name="Parity Circle")

        pasted = client.post(
            f"/api/v1/groups/{group['id']}/sources/paste",
            json={
                "text": (
                    "Ana: Lantern Supper is on Saturday at 7pm\n"
                    "Ben: I will bring the lanterns"
                ),
                "title": "parity chat",
            },
            headers=bearer(owner),
        )
        assert pasted.status_code == 201, pasted.text

        created = client.post(
            f"/api/v1/groups/{group['id']}/events",
            json={
                "title": "Parity Supper",
                "starts_at": "2026-11-07T19:00:00",
                "ends_at": "2026-11-07T21:00:00",
                "timezone": "UTC",
                "place_name": "The Long Hall",
            },
            headers=bearer(owner),
        )
        assert created.status_code == 201, created.text

        agenda = client.get(
            f"/api/v1/groups/{group['id']}/agenda", headers=bearer(owner)
        )
        assert agenda.status_code == 200, agenda.text
        assert "Parity Supper" in agenda.text

        # Erasure on PostgreSQL: the compact step runs VACUUM
        # (FULL) — if the SQLite PRAGMAs leaked through, this
        # call would 500 instead of erasing.
        erased = client.post(
            f"/api/v1/groups/{group['id']}/erase",
            json={"confirm_name": "Parity Circle", "confirm_word": "ERASE"},
            headers=bearer(owner),
        )
        assert erased.status_code == 200, erased.text
        gone = client.get(
            f"/api/v1/groups/{group['id']}", headers=bearer(owner)
        )
        assert gone.status_code == 404


def test_sealed_secret_not_in_raw_pg_storage(pg_url, tmp_path):
    """The PG twin of the SQLite file grep (B-152 ↔ B-127)."""
    marker = "PARITY-SECRET-VALUE-0123456789"
    settings = _pg_settings(pg_url, tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        owner = register(client)
        group = make_group(client, owner)
        created = client.post(
            f"/api/v1/groups/{group['id']}/integrations",
            json={
                "kind": "samtal",
                "name": "The village",
                "config": {
                    "api_token": marker,
                    "base_url": "https://example.invalid",
                },
            },
            headers=bearer(owner),
        )
        assert created.status_code == 201, created.text

    engine = create_engine(pg_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT config_sealed FROM integrations")
        ).scalars().all()
    engine.dispose()
    assert rows, "no integration row was stored"
    for sealed in rows:
        assert marker not in sealed
        assert "example.invalid" not in sealed


def test_sealed_fields_and_group_keys_on_postgres(pg_url, tmp_path):
    """The PG twins of B-167's file greps: raw stored columns
    hold seals, never secrets — and per-group keys rest
    wrapped and distinct, their unwrapped forms absent."""
    secret_text = "the boathouse key is under the blue mat, tell no one"
    secret_sender = "Zelda Nightjar"
    settings = _pg_settings(pg_url, tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        owner = register(client)
        group_a = make_group(client, owner, name="A")
        group_b = make_group(client, owner, name="B")
        created = client.post(
            f"/api/v1/groups/{group_a['id']}/sources",
            json={
                "kind": "chat_paste",
                "title": "Weekend thread",
                "granted_scope": "this-thread-only",
                "chunks": [{"text": secret_text, "sender": secret_sender}],
            },
            headers=bearer(owner),
        )
        assert created.status_code == 201, created.text
        # Served whole first, so the raw read below cannot
        # pass by the data simply never having been stored.
        assert created.json()["chunks"][0]["text"] == secret_text
        assert created.json()["chunks"][0]["sender"] == secret_sender
        client.post(
            f"/api/v1/groups/{group_b['id']}/sources",
            json={
                "kind": "chat_paste", "title": "t", "granted_scope": "s",
                "chunks": [{"text": "hello", "sender": "x"}],
            },
            headers=bearer(owner),
        )

    # The server-engine equivalent of reading the file: the
    # RAW stored column values, exactly as they rest.
    engine = create_engine(pg_url)
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT text, sender FROM source_chunks")
        ).all()
    engine.dispose()
    assert stored, "no chunk rows were stored"
    for text_value, sender_value in stored:
        assert secret_text not in (text_value or "")
        assert secret_sender not in (sender_value or "")
    assert any(
        (text_value or "").startswith("enc:v1:") for text_value, _ in stored
    ), "the seal itself is not what rests in the column"

    # Per-group keys: one row each, wrapped, distinct — and
    # the unwrapped keys appear in nothing that rests.
    db = Database(pg_url)
    session = db.session()
    try:
        rows = session.query(GroupKey).all()
        assert {row.group_id for row in rows} == {
            group_a["id"],
            group_b["id"],
        }
        key_a = fields.data_key(session, group_a["id"])
        key_b = fields.data_key(session, group_b["id"])
    finally:
        session.close()
    assert key_a != key_b
    at_rest = " ".join(row.wrapped_key for row in rows) + " ".join(
        (value or "") for pair in stored for value in pair
    )
    assert base64.b64encode(key_a).decode() not in at_rest
    assert base64.b64encode(key_b).decode() not in at_rest


def test_no_key_material_in_pg_backup_forms(pg_url, tmp_path):
    """The PG twin of B-169's bundle grep. On PostgreSQL the
    B-146 bundle does not exist — backup.py refuses a PG
    deployment in plain words — and the operator's real
    backup surface, the server's stored values, holds no
    key material either."""
    # The refusal, honestly spoken by the tool itself.
    env = dict(
        os.environ,
        GPA_DATABASE_URL=pg_url,
        GPA_STORAGE_DIR=str(tmp_path / "storage"),
        GPA_SECRET_KEY="test-secret-for-tests-only",
        GPA_MASTER_KEY_B64=MASTER_A,
        GPA_ENV="test",
    )
    env.pop("GPA_TEST_PG_URL", None)
    run = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "backup.py"),
         "--out", str(tmp_path / "bundle")],
        env=env, capture_output=True, text=True, cwd=REPO_ROOT,
        timeout=120,
    )
    assert run.returncode != 0
    assert "not sqlite" in run.stderr

    # Seed real key material on the PG deployment, exactly as
    # the bundle test seeds it on SQLite: a group, a minted
    # set of recovery codes, one sealed field.
    settings = _pg_settings(pg_url, tmp_path, master_key_b64=MASTER_A)
    fields.set_settings(settings)
    db = Database(pg_url)
    db.create_all()
    session = db.session()
    try:
        session.add(Group(id="g1", name="Test Group", slug="test-group"))
        session.commit()
        codes = keymgmt.mint_recovery_codes(session, settings)
        assert len(codes) == 8
        fields.seal_text(session, "g1", "chunk.text", "bundle witness")
        session.commit()
        data_key = fields._existing_data_key(session, "g1", settings)
        escrow = session.query(RecoveryEscrow).all()
        keys = session.query(GroupKey).all()
        # The sweep below cannot pass vacuously: the escrow
        # and the wrapped key really are at rest.
        assert len(escrow) == 8
        assert len(keys) == 1
    finally:
        session.close()

    at_rest = " ".join(
        [key.wrapped_key for key in keys]
        + [
            row.code_check + row.code_hint + row.salt + row.sealed_master
            for row in escrow
        ]
    )
    assert MASTER_A not in at_rest
    assert base64.b64encode(data_key).decode() not in at_rest
    for code in codes:
        assert code not in at_rest


def test_data_repair_on_postgres(pg_url, tmp_path):
    """The PG twin of B-173's repair proof: every repairable
    damage kind is fixed forward and named in the audit —
    and orphaned provenance, the one kind that cannot be
    planted here, is refused by the server itself."""
    settings = _pg_settings(pg_url, tmp_path)
    app = create_app(settings)
    db = Database(pg_url)
    with TestClient(app) as client:
        owner = register(client)
        group = make_group(client, owner)
        group_id = group["id"]
        session = db.session()
        try:
            group_row = session.get(Group, group_id)

            # (a) A finished job resting at a stale progress.
            session.add(
                Job(kind="notify", payload_json="{}",
                    status="done", progress=40)
            )
            # (b) Gapped/duplicated chunk positions (the
            # orphan half of this kind is proven below).
            source = Source(
                group_id=group_id, kind="chat_paste", title="Chat",
                granted_by=f"user:{owner['user_id']}",
                granted_scope="group-history",
            )
            session.add(source)
            session.flush()
            for position in (0, 2, 2):
                session.add(
                    SourceChunk(source_id=source.id,
                                position=position, text="words")
                )
            candidate = Candidate(
                group_id=group_id, kind="event", title="Picnic",
                created_by=f"user:{owner['user_id']}",
            )
            session.add(candidate)
            # (c) Two flyers whose status lies about their text.
            session.add(
                Flyer(group_id=group_id, source_id=source.id,
                      alt_text="awaiting description",
                      alt_text_status="complete")
            )
            session.add(
                Flyer(
                    group_id=group_id, source_id=source.id,
                    alt_text=(
                        "A red barn dance poster with gold "
                        "lettering and a fiddle"
                    ),
                    alt_text_status="awaiting_description",
                )
            )
            session.commit()
            # (d) A digest whose renderings were emptied.
            digest = digest_service.compose_digest(
                session, group=group_row
            )
            digest.body_text = ""
            digest.body_html = ""
            session.commit()
            digest_id = digest.id

            # The orphan class cannot be planted on this
            # engine: the foreign keys are enforced by the
            # server, so the damage the SQLite road leaves
            # behind is refused at the door — the strongest
            # form of this guarantee.
            raw = create_engine(pg_url)
            with pytest.raises(IntegrityError):
                with raw.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO provenance (id, candidate_id,"
                            " chunk_id, field, quote, created_at) VALUES"
                            " ('prov-orphan-chunk', :cand,"
                            " 'chunk-that-is-gone', 'title', 'words',"
                            " '2026-10-25 00:00:00')"
                        ),
                        {"cand": candidate.id},
                    )
            raw.dispose()

            repair_service.handle_data_repair(session, {})

            stale = session.query(Job).filter(Job.kind == "notify").one()
            assert stale.progress == 100
            positions = [
                chunk.position
                for chunk in session.query(SourceChunk)
                .order_by(SourceChunk.position)
            ]
            assert positions == [0, 1, 2]
            flyers = session.query(Flyer).all()
            by_text = {
                flyer.alt_text[:10]: flyer.alt_text_status
                for flyer in flyers
            }
            assert by_text["awaiting d"] == "awaiting_description"
            assert by_text["A red barn"] == "complete"
            digests = session.query(Digest).all()
            assert len(digests) == 1
            assert digests[0].id == digest_id
            assert digests[0].body_text and digests[0].body_html

            # The audit names the fixes — every kind but the
            # provenance kind that cannot exist here.
            rows = (
                session.query(AuditLog)
                .filter(AuditLog.action == "repair.completed")
                .all()
            )
            assert len(rows) == 1
            detail = json.loads(rows[0].detail_json or "{}")
            assert detail["fix_count"] >= 4
            joined = " ".join(detail["fixes"])
            for needle in ("progress set to 100", "renumbered",
                           "alt_text_status", "re-rendered"):
                assert needle in joined, needle

            # Idempotent: a second run finds nothing to fix.
            assert repair_service.run_repairs(session) == []
            session.rollback()
        finally:
            session.close()
