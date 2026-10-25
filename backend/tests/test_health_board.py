"""Integration health board (roadmap B-136).

One screen: every integration's state, and the SINGLE next
step, in words an admin can act on. The acceptance case is
the broken token: the board must say "reconnect the account
and re-enter a fresh access token" — not "check settings",
not a stack trace. The board reads recorded verdicts; it
never pings anything itself.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.services import facebook_events, samtal


@pytest.fixture(autouse=True)
def _clients():
    facebook_events.set_client(None)
    samtal.set_client(None)
    yield
    facebook_events.set_client(None)
    samtal.set_client(None)


class RefusingFacebookClient(facebook_events.FixtureFacebookClient):
    """A Page connection whose token the platform refuses."""

    def ping(self):
        raise RuntimeError("401 — the access token expired")


def make_integration(client, owner, group, kind, config=None):
    response = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": kind, "name": kind, "config": config or {}},
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    return response.json()


def board(client, owner, group):
    response = client.get(
        f"/api/v1/groups/{group['id']}/integrations/health-board",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return {entry["kind"]: entry for entry in response.json()}


def test_broken_token_advice_names_the_reconnect_step(client, _clients):
    owner = register(client)
    group = make_group(client, owner)
    facebook_events.set_client(RefusingFacebookClient())
    row = make_integration(
        client, owner, group, "facebook_events",
        {"page_id": "page-1", "access_token": "tok"},
    )
    client.post(
        f"/api/v1/groups/{group['id']}/integrations/{row['id']}/health",
        headers=bearer(owner),
    )
    entry = board(client, owner, group)["facebook_events"]
    assert entry["status"] == "error"
    # The outside system's own words stay on the entry…
    assert "token expired" in entry["last_error"]
    # …and the advice names the step, concretely.
    assert "reconnect the account" in entry["advice"]
    assert "fresh access token" in entry["advice"]
    assert entry["last_checked_at"] is not None


def test_unconfigured_and_unchecked_advice(client, _clients):
    owner = register(client)
    group = make_group(client, owner)
    make_integration(client, owner, group, "google_calendar")
    # Never checked: the board says so, plainly.
    entry = board(client, owner, group)["google_calendar"]
    assert entry["advice"].startswith("Not checked yet")
    # Checked with no config: the connect step, naming the keys.
    row_id = entry["integration_id"]
    client.post(
        f"/api/v1/groups/{group['id']}/integrations/{row_id}/health",
        headers=bearer(owner),
    )
    entry = board(client, owner, group)["google_calendar"]
    assert entry["status"] == "unconfigured"
    assert "calendar_id and access_token" in entry["advice"]


def test_a_working_integration_needs_nothing(client, _clients):
    owner = register(client)
    group = make_group(client, owner)
    samtal.set_client(samtal.FixtureSamtalClient())
    row = make_integration(
        client, owner, group, "samtal", {"base_url": "https://hall.example"}
    )
    client.post(
        f"/api/v1/groups/{group['id']}/integrations/{row['id']}/health",
        headers=bearer(owner),
    )
    entry = board(client, owner, group)["samtal"]
    assert entry["status"] == "ok"
    assert entry["advice"] == "Working — nothing to do."
    assert entry["last_success_at"] is not None
    # A member may not read the board (organiser+).
    from test_groups import invite_and_accept

    ana = invite_and_accept(client, owner, group["id"], "ana@example.org", "member")
    assert client.get(
        f"/api/v1/groups/{group['id']}/integrations/health-board",
        headers=bearer(ana),
    ).status_code == 403
