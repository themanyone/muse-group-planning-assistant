"""Threat model coherence + gap tests (roadmap B-139).

The threat model lives in THREAT_MODEL.md at the repo root.
Its central rule: every abuse case names the test that carries
it, and a protection with no test is an intention, not a fact.
So this file PARSES the model: every `tests/...` path it names
must exist, Case #1 must be there, and every router module
must be covered by the model's route families. A row that
names a missing test fails here — the document is not allowed
to drift ahead of the evidence.

Below the coherence checks sit the small abuse cases that had
no home elsewhere: a seat reaching into a foreign group, and
the public meta surfaces leaking nothing.
"""

import re
from pathlib import Path

from conftest import bearer, make_group, register

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "THREAT_MODEL.md").read_text(encoding="utf-8")


def test_threat_model_names_only_tests_that_exist():
    named = sorted(set(re.findall(r"`(tests/[a-z0-9_]+\.py)`", DOC)))
    assert len(named) >= 30, "the model should map dozens of tests"
    missing = [name for name in named if not (ROOT / name).exists()]
    assert missing == [], f"threat model names tests that do not exist: {missing}"


def test_threat_model_covers_every_router_module():
    routers = sorted(
        path.stem
        for path in (ROOT / "src" / "gpa" / "routers").glob("*.py")
        if path.stem not in {"__init__"}
    )
    uncovered = [name for name in routers if name not in DOC]
    assert uncovered == [], f"router modules missing from the threat model: {uncovered}"


def test_threat_model_opens_with_case_one():
    """Case #1 (Henry's first version: anyone could create
    pages about anyone) is the failure the design exists to
    prevent; the model must tell the story, not just cite it."""
    assert "Case #1" in DOC
    assert "Henry" in DOC
    assert "no permission" in DOC


def test_meta_and_health_leak_nothing(settings):
    """The two unauthenticated surfaces report liveness and
    version — never configuration, paths, or secrets."""
    from fastapi.testclient import TestClient

    from gpa.main import create_app

    client = TestClient(create_app(settings))
    for path in ("/health", "/api/v1/meta"):
        body = client.get(path)
        assert body.status_code == 200
        text = body.text.lower()
        assert settings.secret_key.lower() not in text
        assert "sqlite" not in text
        assert str(settings.storage_dir).lower() not in text
        assert "secret" not in text


def test_anonymous_callers_are_refused_group_surfaces(client):
    owner = register(client)
    group = make_group(client, owner)
    for method, path in (
        ("GET", f"/api/v1/groups/{group['id']}"),
        ("GET", f"/api/v1/groups/{group['id']}/audit"),
        ("GET", f"/api/v1/groups/{group['id']}/members"),
        ("GET", f"/api/v1/groups/{group['id']}/sources"),
    ):
        response = getattr(client, method.lower())(path)
        assert response.status_code == 401, (method, path, response.text)


def test_seat_is_nobody_in_a_foreign_group(client):
    """A seat's world is its one group (B-018). In any other
    group its key must buy it nothing — not a read, not a
    member list, not even confirmation the group exists —
    and the foreign group's marker text must not ride back."""
    owner_a = register(client)
    group_a = make_group(client, owner_a, name="Seat Home")
    seat = client.post(
        f"/api/v1/groups/{group_a['id']}/seats",
        json={"name": "Secretary", "role": "member", "scopes": ["read", "draft"]},
        headers=bearer(owner_a),
    )
    assert seat.status_code == 201, seat.text
    seat_headers = {"X-Api-Key": seat.json()["key"]}

    owner_b = register(client)
    marker = "Foreign Marker Quux-139"
    group_b = make_group(client, owner_b, name=marker)

    for path in (
        f"/api/v1/groups/{group_b['id']}",
        f"/api/v1/groups/{group_b['id']}/members",
        f"/api/v1/groups/{group_b['id']}/sources",
        f"/api/v1/groups/{group_b['id']}/events",
    ):
        response = client.get(path, headers=seat_headers)
        assert response.status_code in (403, 404), (path, response.status_code)
        assert marker not in response.text
