"""Integration framework (roadmap B-127).

The acceptance, attacked directly: a config carrying a
marker secret is registered; the marker must appear NOWHERE
in the database file (the grep test), while the service can
still read the config back (the envelope round-trips).
Health verdicts are recorded on the row in plain words —
including the honest ones: Samtal's unavailable refusal, and
Phase 12 kinds reporting "not connected yet" instead of a
fake green.
"""

from pathlib import Path

import pytest

from conftest import bearer, make_group, register
from gpa.models.groups import Group
from gpa.services import integrations as integration_service
from gpa.services import samtal as samtal_service
from gpa.services import sealing

MARKER = "PLAINTEXT-MARKER-TOKEN-9f27c1"


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    samtal_service.set_client(None)


def test_seal_round_trip_and_tamper(settings):
    sealed = sealing.seal(settings, "the village token")
    assert "the village token" not in sealed
    assert sealing.open_sealed(settings, sealed) == "the village token"
    # A fresh nonce per call: same secret, different text.
    assert sealing.seal(settings, "the village token") != sealed
    flipped = sealed[:-2] + ("AA" if not sealed.endswith("AA") else "BB")
    with pytest.raises(ValueError):
        sealing.open_sealed(settings, flipped)


def test_config_is_sealed_at_rest_and_reads_back(client, settings, db_session):
    owner = register(client)
    group = make_group(client, owner)
    created = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={
            "kind": "samtal",
            "name": "The village",
            "config": {"api_token": MARKER, "base_url": "https://example.invalid"},
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    # The wire states the NAMES of what is held, never a value.
    assert body["config_keys"] == ["api_token", "base_url"]
    assert MARKER not in str(body)

    # THE grep test: the plaintext is nowhere in the DB file.
    db_file = Path(settings.database_url.replace("sqlite:///", "/"))
    blob = db_file.read_bytes()
    assert MARKER.encode("utf-8") not in blob
    assert b"example.invalid" not in blob

    # And the envelope still opens for the app's own use.
    row = integration_service.get_integration(
        db_session, db_session.get(Group, group["id"]), body["id"]
    )
    config = integration_service.read_config(settings, row)
    assert config["api_token"] == MARKER


def test_health_records_plain_words_verdicts(client):
    owner = register(client)
    group = make_group(client, owner)

    samtal_row = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "samtal", "name": "Village", "config": {}},
        headers=bearer(owner),
    ).json()
    # No client configured: the refusal is the verdict.
    checked = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{samtal_row['id']}/health",
        headers=bearer(owner),
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "error"
    assert "No live Samtal connection" in checked.json()["last_error"]
    assert checked.json()["last_checked_at"] is not None

    # With the fixture double answering, the verdict is green.
    samtal_service.set_client(samtal_service.FixtureSamtalClient())
    checked = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{samtal_row['id']}/health",
        headers=bearer(owner),
    )
    assert checked.json()["status"] == "ok"
    assert checked.json()["last_error"] == ""
    assert checked.json()["last_success_at"] is not None

    # A Phase 12 kind: unconfigured, in plain words — not green.
    google_row = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "google_calendar", "name": "Google", "config": {}},
        headers=bearer(owner),
    ).json()
    checked = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{google_row['id']}/health",
        headers=bearer(owner),
    )
    assert checked.json()["status"] == "unconfigured"
    assert "Not connected yet" in checked.json()["last_error"]


def test_update_replaces_config_whole_and_kind_vocabulary(client):
    owner = register(client)
    group = make_group(client, owner)
    created = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "custom", "name": "Hook", "config": {"old_key": "x"}},
        headers=bearer(owner),
    ).json()
    updated = client.patch(
        f"/api/v1/groups/{group['id']}/integrations/{created['id']}",
        json={"config": {"new_key": "y"}},
        headers=bearer(owner),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config_keys"] == ["new_key"]
    assert updated.json()["status"] == "unconfigured"

    refused = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "carrier_pigeon", "name": "Birds", "config": {}},
        headers=bearer(owner),
    )
    assert refused.status_code == 422, refused.text


def test_framework_doors(client):
    owner = register(client)
    group = make_group(client, owner)
    member = register(client)
    client.post(
        f"/api/v1/groups/{group['id']}/invites",
        json={"email": member["email"], "role": "member"},
        headers=bearer(owner),
    )
    client.post(
        f"/api/v1/groups/{group['id']}/invites/accept", headers=bearer(member)
    )
    # A plain member cannot register an integration (admin act).
    refused = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": "custom", "name": "Nope", "config": {}},
        headers=bearer(member),
    )
    assert refused.status_code == 403, refused.text
    # And another group's integration is a 404, never a leak.
    other = make_group(client, owner, name="Elsewhere")
    row = client.post(
        f"/api/v1/groups/{other['id']}/integrations",
        json={"kind": "custom", "name": "Theirs", "config": {}},
        headers=bearer(owner),
    ).json()
    missing = client.get(
        f"/api/v1/groups/{group['id']}/integrations/{row['id']}",
        headers=bearer(owner),
    )
    assert missing.status_code == 404, missing.text
