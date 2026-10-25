"""Voice-note and live-chat source tests (roadmap B-031/B-032).

Voice: the audio is stored, the source waits honestly for
transcription, and the job that will carry its words is visible
by id. Live: the registration IS the permission record — no
granting person or no scope, no source.
"""

import io
import wave

from conftest import bearer, make_group, register
from gpa.services import jobs as job_service
from gpa.services import transcribe as _transcribe  # noqa: F401 — registers the stub handler


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 80)
    return buffer.getvalue()


def test_voice_note_is_stored_and_its_job_is_visible(client, db_session, app):
    owner = register(client)
    group = make_group(client, owner)
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/voice?filename=note.wav",
        content=_wav_bytes(),
        headers={**bearer(owner), "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "voice"
    assert body["status"] == "awaiting_transcription"
    assert body["stage"] == "awaiting_transcription"
    assert "transcription job" in body["chunks"][0]["text"]
    job_id = body["transcription_job_id"]
    assert body["transcription_status"] == "queued"

    # Job status is visible through the jobs door…
    job = client.get(f"/api/v1/jobs/{job_id}", headers=bearer(owner))
    assert job.status_code == 200, job.text
    assert job.json()["kind"] == "transcribe"
    # …and the handler completes it. Superseded by Phase 5
    # (B-068): this once asserted the source still waited after
    # the job ran, because the handler was an honest stub. The
    # handler is real now — through the adapter — and on this
    # app's mock driver the note's words are the mock's own
    # honestly-labelled canned transcript; the golden words are
    # proven in tests/test_transcription.py.
    assert job_service.process_one(db_session, app.state.settings, "test-worker") is True
    done = client.get(f"/api/v1/jobs/{job_id}", headers=bearer(owner))
    assert done.json()["status"] == "done"
    source = client.get(
        f"/api/v1/groups/{group['id']}/sources/{body['id']}", headers=bearer(owner)
    )
    assert source.json()["status"] == "ready"
    assert source.json()["stage"] == "done"
    assert "mock driver does not listen" in source.json()["chunks"][0]["text"]


def test_voice_rejects_a_disguised_program(client):
    owner = register(client)
    group = make_group(client, owner)
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/voice?filename=song.mp3",
        content=b"MZ" + b"\x00" * 64,
        headers={**bearer(owner), "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 422


def test_live_registration_records_the_grant(client):
    owner = register(client)
    group = make_group(client, owner)
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/live",
        json={
            "connector_kind": "messenger",
            "external_id": "thread-123",
            "granted_scope": "read messages from Oct 26 to Nov 4",
        },
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "chat_live"
    assert body["status"] == "registered"
    assert body["connector_kind"] == "messenger"
    assert body["external_id"] == "thread-123"
    assert body["granted_by"] == f"user:{owner['user_id']}"
    assert body["granted_scope"] == "read messages from Oct 26 to Nov 4"
    assert body["chunks"] == []


def test_live_registration_without_scope_does_not_exist(client):
    owner = register(client)
    group = make_group(client, owner)
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/live",
        json={"connector_kind": "messenger", "external_id": "thread-123"},
        headers=bearer(owner),
    )
    assert response.status_code == 422
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert listed.json() == []


def test_a_seat_cannot_grant_live_access(client):
    owner = register(client)
    group = make_group(client, owner)
    seat = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={"name": "Helper", "role": "member", "scopes": ["read", "draft"]},
        headers=bearer(owner),
    )
    assert seat.status_code == 201, seat.text
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/live",
        json={
            "connector_kind": "messenger",
            "external_id": "thread-123",
            "granted_scope": "everything",
        },
        headers={"X-Api-Key": seat.json()["key"]},
    )
    assert response.status_code == 403
