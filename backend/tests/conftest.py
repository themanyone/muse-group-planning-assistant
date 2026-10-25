"""Test chassis (roadmap B-005).

Fixtures every test shares: a fresh app on a fresh temporary
database file (per test, so tests can never pollute each other),
an HTTP client for it, and small factories for users and groups.
Rate limiting is effectively off here (1000/min) except in the
dedicated rate-limit tests, which build their own app.
"""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpa.config import Settings
from gpa.main import create_app


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Test settings: isolated files under tmp_path, fast jobs."""
    values = dict(
        env="test",
        database_url=f"sqlite:///{tmp_path}/test.db",
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
