"""Secrets + config audit (roadmap B-145).

- Production refuses known-default and short secrets at
  startup, in plain words; development is unaffected.
- The access log never carries a live token: feed, invite,
  email-in, and webhook-source URLs all bear their credential
  in the path, and the log redacts exactly that segment.
- The repository tracks no .env file (checked against git
  itself, not a list kept by hand).

The dependency audit (pip-audit over the exported lockfile)
is a slice-level act, not a unit test: its verbatim output
is recorded in TEST_REPORT.md's Phase 13 addendum.
"""

import logging
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import bearer, make_group, register

from gpa.config import DEV_SECRET, Settings


# ------------------------------------------------------------------
# Startup refusals
# ------------------------------------------------------------------


def test_prod_refuses_known_default_secrets():
    for secret in (DEV_SECRET, "changeme", "test-secret-for-tests-only"):
        with pytest.raises(ValidationError) as caught:
            Settings(env="prod", secret_key=secret)
        assert "GPA_SECRET_KEY" in str(caught.value)
        assert "known default" in str(caught.value)


def test_prod_refuses_short_secrets():
    with pytest.raises(ValidationError) as caught:
        Settings(env="prod", secret_key="unique-but-short-123")
    assert "too short" in str(caught.value)


def test_prod_accepts_a_real_secret_and_dev_is_unaffected():
    Settings(env="prod", secret_key="x" * 40)
    Settings(env="dev", secret_key=DEV_SECRET)
    Settings(env="test", secret_key="test-secret-for-tests-only")


# ------------------------------------------------------------------
# The access log carries no live tokens
# ------------------------------------------------------------------


def test_access_log_redacts_token_carrying_paths(client, caplog):
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    feed = client.post(
        f"/api/v1/groups/{gid}/calendar/feeds",
        json={"filters": {}}, headers=bearer(owner),
    )
    assert feed.status_code == 201, feed.text
    token = feed.json()["token"]

    with caplog.at_level(logging.INFO, logger="gpa.access"):
        served = client.get(f"/api/v1/calendar/feed/{token}.ics")
        assert served.status_code == 200
        forged = client.post(
            "/api/v1/email-in/FORGEDTOKEN123",
            content=b"Subject: hi\n\nbody",
            headers={"Content-Type": "message/rfc822"},
        )
        assert forged.status_code in (401, 404, 422)
        hook = client.post("/api/v1/webhooks/source/FORGEDHOOK456", json={})
        assert hook.status_code in (401, 404, 422)
        control = client.get(f"/api/v1/groups/{gid}", headers=bearer(owner))
        assert control.status_code == 200

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in logged
    assert "FORGEDTOKEN123" not in logged
    assert "FORGEDHOOK456" not in logged
    assert "calendar/feed/REDACTED" in logged
    assert "email-in/REDACTED" in logged
    assert "webhooks/source/REDACTED" in logged
    # …while ordinary routes stay recognisable in the log.
    assert f"/api/v1/groups/{gid}" in logged


# ------------------------------------------------------------------
# Repository hygiene
# ------------------------------------------------------------------


def test_no_env_file_is_tracked():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True
    )
    if result.returncode == 0:
        tracked = [line.strip() for line in result.stdout.splitlines()]
        offenders = [
            name for name in tracked
            if name == ".env"
            or (name.endswith(".env") and "example" not in name)
        ]
        assert offenders == []
        return
    # No git metadata (a release ZIP, the B-146 drill's clean
    # restored tree): the same guarantee, proven by walking
    # the tree — no real .env ships in it either. (.venv and
    # caches are other tooling's trees; skip them.)
    offenders = []
    for path in root.rglob(".env"):
        parts = set(path.parts)
        if ".venv" in parts or "__pycache__" in parts:
            continue
        offenders.append(str(path))
    assert offenders == []
