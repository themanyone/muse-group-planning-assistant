"""Second-wave live sources (roadmap B-158): Discord + Slack.

The acceptance is that the framework did NOT change: a new
kind is a new driver (services/discord.py, services/slack.py)
behind the B-032 permission record, the B-123 intake, the
B-123 identity law, and the B-127 integration framework.
Everything here runs against the recorded-fixture doubles —
no Discord bot or Slack app is connected in this
environment, and the unavailable defaults refuse in plain
words, recorded on the sync state / integration record.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.services import discord as discord_service
from gpa.services import slack as slack_service

DISCORD_CHANNEL = "chan-general"
DISCORD_MESSAGES = [
    {
        "id": "d1", "sender_id": "discord-ana", "sender_name": "Ana",
        "text": "Trail day moved to Sunday, 9am at the gate",
        "sent_at": "2026-10-22T14:01:00+00:00",
    },
    {
        "id": "d2", "sender_id": "discord-ben", "sender_name": "Ben",
        "text": "I can drive, four seats",
        "sent_at": "2026-10-22T14:02:00+00:00",
    },
]

SLACK_CHANNEL = "C-general"
SLACK_MESSAGES = [
    {
        "id": "1758816000.000100", "sender_id": "U-ana", "sender_name": "Ana",
        "text": "Book club: Thursday 7pm, Mara's place",
        "sent_at": "2026-10-22T15:00:00+00:00",
    },
    {
        "id": "1758816060.000200", "sender_id": "U-cleo", "sender_name": "Cleo",
        "text": "Bringing the discussion questions",
        "sent_at": "2026-10-22T15:01:00+00:00",
    },
]


@pytest.fixture(autouse=True)
def _reset_clients():
    yield
    discord_service.set_client(None)
    slack_service.set_client(None)


def _register_live(client, owner, group_id, kind, external_id):
    response = client.post(
        f"/api/v1/groups/{group_id}/sources/live",
        json={
            "connector_kind": kind,
            "external_id": external_id,
            "granted_scope": "group-history",
            "title": f"{kind} channel",
        },
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sync(client, owner, group_id, source_id):
    return client.post(
        f"/api/v1/groups/{group_id}/sources/{source_id}/sync",
        headers=bearer(owner),
    )


def _chunks(client, owner, group_id, source_id):
    detail = client.get(
        f"/api/v1/groups/{group_id}/sources/{source_id}", headers=bearer(owner)
    )
    assert detail.status_code == 200, detail.text
    return detail.json()["chunks"]


def test_discord_source_syncs_through_the_framework(client):
    owner = register(client)
    group = make_group(client, owner)
    fixture = discord_service.FixtureDiscordClient(
        channels={DISCORD_CHANNEL: DISCORD_MESSAGES}
    )
    discord_service.set_client(fixture)
    source = _register_live(client, owner, group["id"], "discord", DISCORD_CHANNEL)

    synced = _sync(client, owner, group["id"], source["id"])
    assert synced.status_code == 200, synced.text
    assert synced.json()["added"] == 2

    chunks = _chunks(client, owner, group["id"], source["id"])
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Ana", "Trail day moved to Sunday, 9am at the gate"),
        ("Ben", "I can drive, four seats"),
    ]
    # The stable outside ids ride the chunks — the account-link
    # key, exactly as a Samtal hall's do.
    assert [c["sender_ref"] for c in chunks] == ["discord-ana", "discord-ben"]

    # The cursor advanced: new words only, on the next poll.
    fixture.post_message(
        DISCORD_CHANNEL,
        {
            "id": "d3", "sender_id": "discord-ana", "sender_name": "Ana",
            "text": "Gate code is on the board",
            "sent_at": "2026-10-22T14:05:00+00:00",
        },
    )
    again = _sync(client, owner, group["id"], source["id"])
    assert again.json()["added"] == 1
    assert len(_chunks(client, owner, group["id"], source["id"])) == 3


def test_slack_source_syncs_through_the_framework(client):
    owner = register(client)
    group = make_group(client, owner)
    slack_service.set_client(
        slack_service.FixtureSlackClient(channels={SLACK_CHANNEL: SLACK_MESSAGES})
    )
    source = _register_live(client, owner, group["id"], "slack", SLACK_CHANNEL)

    synced = _sync(client, owner, group["id"], source["id"])
    assert synced.status_code == 200, synced.text
    assert synced.json()["added"] == 2
    chunks = _chunks(client, owner, group["id"], source["id"])
    assert [c["sender_ref"] for c in chunks] == ["U-ana", "U-cleo"]
    assert chunks[0]["text"] == "Book club: Thursday 7pm, Mara's place"


@pytest.mark.parametrize(
    "kind,external_id,words",
    [
        ("discord", DISCORD_CHANNEL, "No live Discord connection"),
        ("slack", SLACK_CHANNEL, "No live Slack connection"),
    ],
)
def test_unavailable_platforms_refuse_plainly_and_on_the_record(
    client, db_session, kind, external_id, words
):
    """No bot/app is connected here: the sync refuses in plain
    words AND the refusal is recorded on the sync state — the
    B-123 honesty, inherited unchanged by the new drivers."""
    from gpa.models.integrations import LiveSyncState

    owner = register(client)
    group = make_group(client, owner)
    source = _register_live(client, owner, group["id"], kind, external_id)

    refused = _sync(client, owner, group["id"], source["id"])
    assert refused.status_code == 422, refused.text
    assert words in refused.json()["detail"]

    state = (
        db_session.query(LiveSyncState)
        .filter(LiveSyncState.source_id == source["id"])
        .one()
    )
    assert words in state.last_error
    assert _chunks(client, owner, group["id"], source["id"]) == []


def test_discord_speakers_resolve_only_through_links(client):
    """The identity law rides the new kind unchanged: a linked
    Discord speaker resolves to the member; a same-named
    unlinked speaker stays a guest."""
    owner = register(client)
    group = make_group(client, owner)
    discord_service.set_client(
        discord_service.FixtureDiscordClient(
            channels={DISCORD_CHANNEL: DISCORD_MESSAGES}
        )
    )
    source = _register_live(client, owner, group["id"], "discord", DISCORD_CHANNEL)
    _sync(client, owner, group["id"], source["id"])

    # Invite a member whose display name is ALSO "Ana".
    client.post(
        f"/api/v1/groups/{group['id']}/invites",
        json={"email": "ana-member@example.org", "role": "member"},
        headers=bearer(owner),
    )
    member = register(client, email="ana-member@example.org", name="Ana")
    accepted = client.post(
        f"/api/v1/groups/{group['id']}/invites/accept", headers=bearer(member)
    )
    assert accepted.status_code == 200, accepted.text

    speakers = client.get(
        f"/api/v1/groups/{group['id']}/sources/{source['id']}/speakers",
        headers=bearer(owner),
    )
    assert speakers.status_code == 200, speakers.text
    by_id = {row["external_id"]: row for row in speakers.json()}
    # Unlinked, same name as a member: still a guest.
    assert by_id["discord-ana"]["guest"] is True
    assert by_id["discord-ana"]["member"] is None

    linked = client.post(
        f"/api/v1/groups/{group['id']}/account-links",
        json={
            "connector_kind": "discord",
            "external_user_id": "discord-ana",
            "external_name": "Ana",
            "user_id": member["user_id"],
        },
        headers=bearer(owner),
    )
    assert linked.status_code == 201, linked.text

    speakers = client.get(
        f"/api/v1/groups/{group['id']}/sources/{source['id']}/speakers",
        headers=bearer(owner),
    )
    by_id = {row["external_id"]: row for row in speakers.json()}
    assert by_id["discord-ana"]["guest"] is False
    assert by_id["discord-ana"]["member"]["user_id"] == member["user_id"]
    assert by_id["discord-ben"]["guest"] is True


@pytest.mark.parametrize("kind", ["discord", "slack"])
def test_platform_integrations_check_health_through_the_framework(client, kind):
    """B-127 unchanged: register → unconfigured without a
    token → the boundary's refusal recorded with a token."""
    owner = register(client)
    group = make_group(client, owner)
    created = client.post(
        f"/api/v1/groups/{group['id']}/integrations",
        json={"kind": kind, "name": f"{kind} connection", "config": {}},
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    integration_id = created.json()["id"]

    checked = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration_id}/health",
        headers=bearer(owner),
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "unconfigured"
    assert "bot_token" in checked.json()["last_error"]

    updated = client.patch(
        f"/api/v1/groups/{group['id']}/integrations/{integration_id}",
        json={"config": {"bot_token": "fixture-token-not-real"}},
        headers=bearer(owner),
    )
    assert updated.status_code == 200, updated.text
    checked = client.post(
        f"/api/v1/groups/{group['id']}/integrations/{integration_id}/health",
        headers=bearer(owner),
    )
    assert checked.json()["status"] == "error"
    assert f"No live {kind.capitalize()} connection" in checked.json()["last_error"]
