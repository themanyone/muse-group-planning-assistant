"""Messenger source, read path (roadmap B-125) — the fallback
IS the acceptance test.

Platform access for a live Messenger integration was never
granted to this project. So this slice's truths are:

- a Messenger live source can be REGISTERED (B-032's
  permission record — the group's grant is real even though
  the platform's is absent);
- SYNCING it refuses in plain words, names the working road,
  and records the refusal on the source's sync state;
- the fallback — a Messenger export through paste ingest
  (B-024) — lands as the same speaker chunks every other
  source produces. Nothing anywhere fakes a live connection.
"""

from conftest import bearer, make_group, register
from gpa.services import messenger as messenger_service

# A Messenger copy-paste export, in the shape B-024's parser
# reads: name line, message lines, timestamp line.
MESSENGER_EXPORT = "\n".join(
    [
        "Ana Reyes",
        "Club night is on! this Saturday at the hall, doors 7pm",
        "Oct 21, 2026, 11:02 PM",
        "Ben Osei",
        "I can bring the projector",
        "Oct 21, 2026, 11:03 PM",
    ]
)


def test_messenger_access_state_is_plain_and_honest():
    state = messenger_service.access_state()
    assert state["access"] == "not_granted"
    assert state["live_sync"] is False
    assert "paste" in state["fallback"]


def test_messenger_sync_refuses_and_records_it(client, db_session):
    owner = register(client)
    group = make_group(client, owner)
    live = client.post(
        f"/api/v1/groups/{group['id']}/sources/live",
        json={
            "connector_kind": "messenger",
            "external_id": "messenger-group-123",
            "granted_scope": "group-history",
            "title": "Messenger group",
        },
        headers=bearer(owner),
    )
    assert live.status_code == 201, live.text  # registration is a record

    refused = client.post(
        f"/api/v1/groups/{group['id']}/sources/{live.json()['id']}/sync",
        headers=bearer(owner),
    )
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert "has not been granted" in detail
    assert "paste" in detail  # the refusal names the working road

    from gpa.models.integrations import LiveSyncState

    state = (
        db_session.query(LiveSyncState)
        .filter(LiveSyncState.source_id == live.json()["id"])
        .one()
    )
    assert "has not been granted" in state.last_error
    assert state.messages_seen == 0  # nothing was faked in


def test_the_fallback_paste_ingest_lands_as_speaker_chunks(client):
    owner = register(client)
    group = make_group(client, owner)
    pasted = client.post(
        f"/api/v1/groups/{group['id']}/sources/paste",
        json={"title": "Messenger export", "text": MESSENGER_EXPORT},
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text
    chunks = pasted.json()["chunks"]
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Ana Reyes", "Club night is on! this Saturday at the hall, doors 7pm"),
        ("Ben Osei", "I can bring the projector"),
    ]
    # The export's own timestamps came along as instants —
    # the fallback loses nothing the live road would have had.
    assert chunks[0]["sent_at"] is not None
