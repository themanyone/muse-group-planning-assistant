"""Test chassis (roadmap B-005).

Fixtures every test shares: a fresh app on a fresh temporary
database file (per test, so tests can never pollute each other),
an HTTP client for it, and small factories for users and groups.
Rate limiting is effectively off here (1000/min) except in the
dedicated rate-limit tests, which build their own app.

PostgreSQL parity mode (B-152, decision D-06): when the
environment names ``GPA_TEST_PG_URL`` — a scratch PostgreSQL
database the run may empty freely — the SAME suite runs
against a real PostgreSQL server instead of SQLite files.
Per-test isolation is a full truncate of every table before
each test (one shared scratch database; the tests are
sequential). Tests that are about the SQLite FILE itself —
its raw bytes, its backup API, its per-phase migration
archaeology — cannot mean anything on a server engine; they
are named in PG_FILE_TESTS below and skipped in this mode,
each with its parity equivalent in tests/test_postgres_parity.py.
"""

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from gpa.config import Settings
from gpa.main import create_app

# The parity switch (B-152). Empty = the default SQLite mode.
PG_URL = os.environ.get("GPA_TEST_PG_URL", "")

# (file, test name) pairs that are SQLite-file tests by nature;
# a test name of None skips the whole file in PG mode. Every
# entry's guarantee is re-proven on PostgreSQL in
# tests/test_postgres_parity.py (migration chain, mover,
# sealed-storage raw read, erasure service path).
PG_FILE_TESTS = {
    # The backup/restore scripts speak SQLite's online-backup
    # API to a file; the drill is a file drill by definition.
    ("test_backup_restore.py", None),
    # Per-phase migration archaeology builds throwaway SQLite
    # files; the PG parity test runs the WHOLE chain on PG.
    ("test_migrations_phase2.py", None),
    ("test_migrations_phase5.py", None),
    ("test_migrations_phase6.py", None),
    ("test_migrations_phase7.py", None),
    ("test_migrations_phase8.py", None),
    ("test_migrations_phase9.py", None),
    ("test_migrations_phase10.py", None),
    ("test_migrations_phase11.py", None),
    ("test_migrations_phase12.py", None),
    ("test_migrations_phase13.py", None),
    # Reads the raw SQLite file to prove erased bytes are gone
    # from the FILE — a file property; on PG the erasure test
    # beside it (export/erase over the API) still runs, and the
    # service's VACUUM (FULL) step is exercised there.
    ("test_erasure.py", "test_erase_then_grep"),
    # The B-151 parent-directory guarantee is about SQLite
    # file paths.
    ("test_foundation.py", "test_database_opens_in_a_directory_that_does_not_exist_yet"),
    # WAL + synchronous=FULL are SQLite PRAGMAs; there is no
    # PostgreSQL spelling of them. The durability intent is
    # carried by the server's own configuration, and that the
    # app honours its engine is what the whole PG mode proves.
    ("test_foundation.py", "test_sqlite_pragmas"),
    # Greps the SQLite file's bytes for an unsealed secret;
    # the parity test reads the raw stored column on PG.
    ("test_integrations_framework.py", "test_config_is_sealed_at_rest_and_reads_back"),
    ("test_webhooks_in.py", "test_secrets_are_nowhere_in_the_database_file"),
}


def pytest_collection_modifyitems(items):
    """In PG mode, skip the named SQLite-file tests (B-152)."""
    if not PG_URL:
        return
    for item in items:
        file_name = item.path.name
        if (file_name, None) in PG_FILE_TESTS or (
            file_name,
            item.originalname,
        ) in PG_FILE_TESTS:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "SQLite-file test — its guarantee is proven on "
                        "PostgreSQL in tests/test_postgres_parity.py (B-152)"
                    )
                )
            )


_pg_engine = None
_pg_schema_ready = False


def _pg_reset() -> None:
    """Empty the scratch PostgreSQL database for one test.

    Creates the schema on first use, then truncates every
    table (FK-safe via CASCADE) so each test starts from the
    same nothing a fresh SQLite file would give it.
    """
    global _pg_engine, _pg_schema_ready
    from gpa import models  # noqa: F401 — registers every table
    from gpa.db import Base

    if _pg_engine is None:
        _pg_engine = create_engine(PG_URL)
    if not _pg_schema_ready:
        Base.metadata.create_all(_pg_engine)
        _pg_schema_ready = True
    table_names = ", ".join(
        f'"{table.name}"' for table in Base.metadata.sorted_tables
    )
    with _pg_engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE")
        )


# Modules whose world is MODULE-scoped by design (a fixture
# built once from tmp_path_factory). On SQLite such a module
# gets one database FILE for the whole module — its tests are
# written to share that world, and broader-scope fixtures are
# built BEFORE any function-scoped fixture runs. So no
# function-scoped reset can ever be early enough for them:
# the PG gate caught the naive version as 401s throughout
# test_authz_fuzz.py (the reset wiped the just-built world).
# The faithful analogue is: the shared DB reaches such a
# module EMPTY — guaranteed by the per-test resets of every
# module before it, plus one run-level reset below — and is
# then left alone for the module's duration.
_PG_SHARED_WORLD_MODULES = {"test_authz_fuzz.py"}


@pytest.fixture(scope="session", autouse=True)
def _pg_run_start():
    """One reset before anything is built (PG mode only).

    Session scope is the point: it instantiates before every
    module-scoped world, which a function-scoped reset can
    never do (broader scopes set up first — the trap the two
    attempts above fell into)."""
    if PG_URL:
        _pg_reset()
    yield


@pytest.fixture(autouse=True)
def _pg_isolation(request):
    """Per-test clean slate in PG mode; a no-op on SQLite.

    Exception, above: shared-world modules are not reset
    inside — their world must live for the whole module,
    exactly as it does on its one SQLite file. They still
    start empty: this fixture reset the database after every
    earlier module's tests, and _pg_run_start reset it
    before the run."""
    if PG_URL and request.path.name not in _PG_SHARED_WORLD_MODULES:
        _pg_reset()
    yield


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Test settings: isolated files under tmp_path, fast jobs."""
    values = dict(
        env="test",
        database_url=PG_URL or f"sqlite:///{tmp_path}/test.db",
        secret_key="test-secret-for-tests-only",
        storage_dir=tmp_path / "storage",
        outbox_dir=tmp_path / "outbox",
        auth_rate_per_minute=1000,
        job_backoff_base_seconds=0,
        auto_create_tables=True,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session(app):
    """A direct DB session on the test app (for service-level tests)."""
    session = app.state.db.session()
    yield session
    session.close()


def unique_email() -> str:
    """A fresh email per call, so tests never collide on uniqueness."""
    return f"user-{uuid.uuid4().hex[:10]}@example.org"


def register(client: TestClient, email: str | None = None, password: str = "correct-horse-9", name: str = "Test Person") -> dict:
    """Register a user; return {email, password, access, refresh, user_id}."""
    email = email or unique_email()
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": name},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "email": email,
        "password": password,
        "access": body["access_token"],
        "refresh": body["refresh_token"],
        "user_id": body["user_id"],
    }


def bearer(session: dict) -> dict:
    """Authorization header for a session returned by register()."""
    return {"Authorization": f"Bearer {session['access']}"}


def latest_token(settings: Settings, *, to: str, purpose: str) -> str:
    """Read the newest one-time token the mail driver sent someone.

    In dev the outbox file IS the mailbox (services/mail.py), so the
    test reads the token the way a person's mail client would show
    it — from the message itself.
    """
    files = sorted(Path(settings.outbox_dir).glob("*.json"))
    matches = []
    for path in files:
        message = json.loads(path.read_text(encoding="utf-8"))
        if message["to"] == to and message["purpose"] == purpose:
            matches.append(message["token"])
    assert matches, f"no {purpose} mail for {to} in {settings.outbox_dir}"
    return matches[-1]


def make_group(client: TestClient, owner: dict, name: str = "Planning Circle", **kwargs) -> dict:
    """Create a group as ``owner``; return the group JSON."""
    response = client.post(
        "/api/v1/groups", json={"name": name, **kwargs}, headers=bearer(owner)
    )
    assert response.status_code == 201, response.text
    return response.json()
