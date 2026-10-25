"""Phase 0 acceptance: chassis, errors, config, durability pragmas.

Covers B-002 (request ids, config failure), B-003 (WAL/FULL pragmas,
concurrent writers), B-004 (problem+json shape on every class of
error), and B-010's health/meta skeleton. The cold-tree test at the
end is B-151's: the freeze drill found that a database whose parent
directory does not exist yet — every clean unzip of the release
ZIP — could not be opened at all.
"""

import threading

import pytest
from sqlalchemy import text

from conftest import register
from gpa.config import Settings
from gpa.db import Database
from gpa.services.identity import register_user


def test_health_and_meta(client):
    """Fresh app → /health answers ok, /api/v1/meta names the API."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"
    meta = client.get("/api/v1/meta")
    assert meta.status_code == 200
    assert meta.json()["api"] == "v1"


def test_request_id_header(client):
    """Every response carries the request id (B-002)."""
    response = client.get("/health")
    assert response.headers.get("x-request-id")
    custom = client.get("/health", headers={"X-Request-Id": "trace-123"})
    assert custom.headers["x-request-id"] == "trace-123"


def test_error_shape_unknown_route(client):
    """404 comes back in problem+json shape with a stable code."""
    response = client.get("/no-such-route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "not_found"
    assert body["status"] == 404
    assert "detail" in body and "errors" in body


def test_error_shape_method_not_allowed(client):
    """405 also wears the same shape (B-004 acceptance)."""
    response = client.post("/health")
    assert response.status_code == 405
    assert response.json()["status"] == 405


def test_error_shape_validation(client):
    """422 maps field problems into errors[] (B-004)."""
    response = client.post("/api/v1/auth/register", json={"email": "x", "password": "short"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"], "validation errors must be listed"


def test_prod_refuses_dev_secret():
    """Config errors fail at startup with plain words (B-002)."""
    with pytest.raises(Exception) as excinfo:
        Settings(env="prod", secret_key="dev-only-change-me")
    assert "GPA_SECRET_KEY" in str(excinfo.value)


def test_sqlite_pragmas(app):
    """WAL on, synchronous=FULL (2), foreign keys on (B-003)."""
    with app.state.db.engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 2  # FULL
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_concurrent_writers_do_not_lock_error(app):
    """Two threads writing at once: nobody gets 'database is locked'
    and every committed row survives (B-003 acceptance)."""
    errors: list[Exception] = []

    def writer(tag: int) -> None:
        session = app.state.db.session()
        try:
            for n in range(10):
                register_user(
                    session,
                    email=f"writer-{tag}-{n}@example.org",
                    password="correct-horse-9",
                )
        except Exception as exc:  # collected, asserted below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    session = app.state.db.session()
    try:
        count = session.execute(text("SELECT COUNT(*) FROM users")).scalar()
        assert count == 20
    finally:
        session.close()


def test_database_opens_in_a_directory_that_does_not_exist_yet(tmp_path):
    """B-151 regression: a cold tree has no data/ directory (the
    release ZIP never carries runtime data), and SQLite refuses a
    file whose parent is missing. The Database — the one place
    that opens it — guarantees the parent exists, so the seed's
    first connection on a clean unzip succeeds."""
    target = tmp_path / "fresh" / "data" / "gpa.db"
    assert not target.parent.exists()
    db = Database(f"sqlite:///{target}")
    db.create_all()
    session = db.session()
    try:
        assert session.execute(text("SELECT 1")).scalar() == 1
    finally:
        session.close()
    assert target.exists()
