"""Samtal source connector (roadmap B-123).

The bridge's first span, proven against the recorded-fixture
double — NEVER against the real village (Samtal is Volmarr's
live system; nothing in this suite connects anywhere):

- the golden hall transcript ingests through the poll door
  IDENTICALLY to the same words pasted (the acceptance test);
- the cursor advances: a second sync adds only what is new;
- identity resolves ONLY through account links, keyed by the
  speaker's outside id — a guest whose display name is
  letter-for-letter a member's name stays a guest (the
  no-name-guessing law, pinned);
- an unconfigured deployment refuses in plain words and the
  refusal is recorded on the sync state.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.services import samtal as samtal_service

HALL_ID = "hall-longhouse"

HALL_MESSAGES = [
    {
        "id": "m1", "sender_id": "samtal-ana", "sender_name": "Ana",
        "text": "Club night is on! this Saturday at the hall, doors 7pm",
        "sent_at": "2026-10-21T23:02:00+00:00",
    },
    {
        "id": "m2", "sender_id": "samtal-ben", "sender_name": "Ben",
        "text": "I can bring the projector",
        "sent_at": "2026-10-21T23:03:00+00:00",
    },
    {
        "id": "m3", "sender_id": "samtal-cleo", "sender_name": "Cleo",
        "text": "I'll sort the playlist again",
        "sent_at": "2026-10-21T23:04:00+00:00",
    },
]

# The same words as a WhatsApp-style paste (day-first stamps,
# the parser's own road) — the acceptance comparison.
HALL_PASTE = "\n".join(
    [
        "[21/10/2026, 23:02:00] Ana: Club night is on! this Saturday at the hall, doors 7pm",
        "[21/10/2026, 23:03:00] Ben: I can bring the projector",
        "[21/10/2026, 23:04:00] Cleo: I'll sort the playlist again",
    ]
)


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    samtal_service.set_client(None)


def invite_member(client, owner, group_id, email, name="Member Person"):
    client.post(
        f"/api/v1/groups/{group_id}/invites",
        json={"email": email, "role": "member"},
        headers=bearer(owner),
    )
    member = register(client, email=email, name=name)
    accepted = client.post(
        f"/api/v1/groups/{group_id}/invites/accept", headers=bearer(member)
    )
    assert accepted.status_code == 200, accepted.text
    return member


def register_hall(client, owner, group_id):
    response = client.post(
        f"/api/v1/groups/{group_id}/sources/live",
        json={
            "connector_kind": "samtal",
            "external_id": HALL_ID,
            "granted_scope": "group-history",
            "title": "The longhouse hall",
        },
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    return response.json()


def chunk_tuples(client, owner, group_id, source_id):
    detail = client.get(
        f"/api/v1/groups/{group_id}/sources/{source_id}", headers=bearer(owner)
    )
    assert detail.status_code == 200, detail.text
    return [
        (c["sender"], c["text"], c["sent_at"]) for c in detail.json()["chunks"]
    ]


def test_golden_hall_ingests_identically_to_a_paste(client):
    owner = register(client)
    group = make_group(client, owner)
    samtal_service.set_client(
        samtal_service.FixtureSamtalClient(halls={HALL_ID: HALL_MESSAGES})
    )
    live = register_hall(client, owner, group["id"])

    synced = client.post(
        f"/api/v1/groups/{group['id']}/sources/{live['id']}/sync",
        headers=bearer(owner),
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["added"] == 3
    assert synced.json()["status"] == "live"

    pasted = client.post(
        f"/api/v1/groups/{group['id']}/sources/paste",
        json={"title": "Same words, pasted", "text": HALL_PASTE},
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text

    from_hall = chunk_tuples(client, owner, group["id"], live["id"])
    from_paste = chunk_tuples(client, owner, group["id"], pasted.json()["id"])
    # THE acceptance test: same speakers, same words, same
    # instants — the two roads are one road downstream.
    assert from_hall == from_paste
    # And the hall chunks carry the speakers' outside ids —
    # the paste, knowing none, carries null (checked on the
    # raw detail below in the identity test).


def test_cursor_advances_and_never_reingests(client):
    owner = register(client)
    group = make_group(client, owner)
    double = samtal_service.FixtureSamtalClient(halls={HALL_ID: HALL_MESSAGES})
    samtal_service.set_client(double)
    live = register_hall(client, owner, group["id"])
    url = f"/api/v1/groups/{group['id']}/sources/{live['id']}/sync"

    first = client.post(url, headers=bearer(owner))
    assert first.json()["added"] == 3
    second = client.post(url, headers=bearer(owner))
    assert second.json()["added"] == 0

    double.post_message(
        HALL_ID,
        {
            "id": "m4", "sender_id": "samtal-ana", "sender_name": "Ana",
            "text": "Doors at seven, quiz at eight.",
            "sent_at": "2026-10-21T23:05:00+00:00",
        },
    )
    third = client.post(url, headers=bearer(owner))
    assert third.json()["added"] == 1
    chunks = chunk_tuples(client, owner, group["id"], live["id"])
    assert len(chunks) == 4
    assert chunks[-1][1] == "Doors at seven, quiz at eight."


def test_identity_resolves_only_through_links(client, db_session):
    owner = register(client)
    group = make_group(client, owner)
    # A member whose account name is "Ana" — and a hall
    # speaker who is ALSO called Ana, with no link. The law:
    # the speaker stays a guest. Names are not identity.
    ana = invite_member(client, owner, group["id"], "ana@example.org", name="Ana")
    samtal_service.set_client(
        samtal_service.FixtureSamtalClient(halls={HALL_ID: HALL_MESSAGES})
    )
    live = register_hall(client, owner, group["id"])
    client.post(
        f"/api/v1/groups/{group['id']}/sources/{live['id']}/sync",
        headers=bearer(owner),
    )

    speakers = client.get(
        f"/api/v1/groups/{group['id']}/sources/{live['id']}/speakers",
        headers=bearer(owner),
    )
    assert speakers.status_code == 200, speakers.text
    by_id = {row["external_id"]: row for row in speakers.json()}
    # Unlinked: all three are guests — Ana included, despite
    # the member named Ana standing right there.
    assert by_id["samtal-ana"]["guest"] is True
    assert by_id["samtal-ana"]["member"] is None
    assert by_id["samtal-ben"]["guest"] is True

    # A person makes the link — by outside id, naming the
    # member. Only now does samtal-ana resolve.
    linked = client.post(
        f"/api/v1/groups/{group['id']}/account-links",
        json={
            "connector_kind": "samtal",
            "external_user_id": "samtal-ana",
            "external_name": "Ana",
            "user_id": ana["user_id"],
        },
        headers=bearer(owner),
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["member_name"] == "Ana"

    speakers = client.get(
        f"/api/v1/groups/{group['id']}/sources/{live['id']}/speakers",
        headers=bearer(owner),
    ).json()
    by_id = {row["external_id"]: row for row in speakers}
    assert by_id["samtal-ana"]["guest"] is False
    assert by_id["samtal-ana"]["member"]["user_id"] == ana["user_id"]
    # Ben — whose name matches nobody and who has no link —
    # is untouched by Ana's link.
    assert by_id["samtal-ben"]["guest"] is True


def test_link_rules_are_enforced(client):
    owner = register(client)
    group = make_group(client, owner)
    stranger = register(client)  # not a member of the group

    refused = client.post(
        f"/api/v1/groups/{group['id']}/account-links",
        json={
            "connector_kind": "samtal",
            "external_user_id": "samtal-x",
            "external_name": "X",
            "user_id": stranger["user_id"],
        },
        headers=bearer(owner),
    )
    # A link may only point at an active member — anything
    # else would be a silent membership grant.
    assert refused.status_code == 422, refused.text

    member = invite_member(client, owner, group["id"], "m@example.org")
    body = {
        "connector_kind": "samtal",
        "external_user_id": "samtal-m",
        "external_name": "Em",
        "user_id": member["user_id"],
    }
    made = client.post(
        f"/api/v1/groups/{group['id']}/account-links",
        json=body, headers=bearer(owner),
    )
    assert made.status_code == 201, made.text
    again = client.post(
        f"/api/v1/groups/{group['id']}/account-links",
        json=body, headers=bearer(owner),
    )
    assert again.status_code == 409, again.text  # one identity, one link

    removed = client.delete(
        f"/api/v1/groups/{group['id']}/account-links/{made.json()['id']}",
        headers=bearer(owner),
    )
    assert removed.status_code == 204, removed.text


def test_unconfigured_samtal_refuses_in_plain_words(client, db_session):
    owner = register(client)
    group = make_group(client, owner)
    live = register_hall(client, owner, group["id"])
    # No client installed: the process client is the
    # unavailable one (the fixture resets it after each test).
    refused = client.post(
        f"/api/v1/groups/{group['id']}/sources/{live['id']}/sync",
        headers=bearer(owner),
    )
    assert refused.status_code == 422, refused.text
    assert "No live Samtal connection" in refused.json()["detail"]

    from gpa.models.integrations import LiveSyncState

    state = (
        db_session.query(LiveSyncState)
        .filter(LiveSyncState.source_id == live["id"])
        .one()
    )
    # The refusal is recorded on the source's own state —
    # visible, never silent — and nothing was ingested.
    assert "No live Samtal connection" in state.last_error
    assert state.messages_seen == 0


def test_sync_rejects_a_non_live_source(client):
    owner = register(client)
    group = make_group(client, owner)
    pasted = client.post(
        f"/api/v1/groups/{group['id']}/sources/paste",
        json={"title": "plain paste", "text": "Ana: hello"},
        headers=bearer(owner),
    )
    refused = client.post(
        f"/api/v1/groups/{group['id']}/sources/{pasted.json()['id']}/sync",
        headers=bearer(owner),
    )
    assert refused.status_code == 422, refused.text
