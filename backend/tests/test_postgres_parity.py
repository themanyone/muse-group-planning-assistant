"""PostgreSQL parity (roadmap B-152, decision D-06).

D-06 said the models were engine-portable and PostgreSQL was a
URL swap away. This file is where that sentence is PROVEN, four
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

The file runs ONLY when GPA_TEST_PG_ADMIN_URL names a real
PostgreSQL server the tests may create and drop scratch
databases on (e.g. postgresql+psycopg://postgres@127.0.0.1:
54329/postgres). Without one it skips — a named absence in
the SQLite run's report, never a silent pass. DEPLOYMENT.md
("PostgreSQL promotion") records how the throwaway cluster
for this run was stood up.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from conftest import bearer, make_group, register
from gpa import models  # noqa: F401 — registers every table
from gpa.config import Settings
from gpa.db import Base, Database
from gpa.main import create_app
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
    # with a fresh id lands beside the moved ones.
    engine = create_engine(pg_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, "
                "timezone, locale, created_at) VALUES "
                "(:id, :email, 'Post Move', NULL, 'UTC', 'en', now())"
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
