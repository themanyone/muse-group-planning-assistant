"""Ingest status + progress stream tests (roadmap B-033).

A source's GET reports its stage and progress; the group stream
speaks the changes: a snapshot on connect, then job.progress
frames as job-driven work moves. The generator is tested
directly (it IS the stream's engine — the HTTP route is a thin
StreamingResponse over it) plus one true HTTP smoke test.
"""

import io
import wave

from conftest import bearer, make_group, register
from gpa.services import jobs as job_service
from gpa.services import stream as stream_service
from gpa.services import transcribe as _transcribe  # noqa: F401


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 40)
    return buffer.getvalue()


def _upload_voice(client, owner, group):
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/voice?filename=note.wav",
        content=_wav_bytes(),
        headers={**bearer(owner), "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_source_get_reports_stage_and_progress(client):
    owner = register(client)
    group = make_group(client, owner)
    body = _upload_voice(client, owner, group)
    fetched = client.get(
        f"/api/v1/groups/{group['id']}/sources/{body['id']}", headers=bearer(owner)
    )
    assert fetched.json()["stage"] == "awaiting_transcription"
    assert fetched.json()["progress"] == 100  # The ingest itself is complete.


def test_stream_snapshot_then_progress_events(client, db_session, app):
    owner = register(client)
    group = make_group(client, owner)
    body = _upload_voice(client, owner, group)
    factory = app.state.db.session

    events = stream_service.group_events(
        factory, group["id"], poll_seconds=0.01, max_polls=1
    )
    first = next(events)
    assert first.startswith("event: snapshot")
    assert body["transcription_job_id"] in first
    assert '"queued"' in first

    # The job completes; the next frames must carry the change.
    assert job_service.process_one(db_session, app.state.settings, "w") is True
    frames = list(events)
    progress = [f for f in frames if f.startswith("event: job.progress")]
    assert progress, frames
    assert '"done"' in progress[-1]
    assert body["id"] in progress[-1]  # The event names its source.


def test_stream_http_smoke_snapshot_arrives(client, monkeypatch):
    """Route-wiring smoke: the endpoint serves the generator's
    frames as text/event-stream. The LIVE generator is unbounded
    by design (it runs until disconnect), so the smoke bounds it
    at the service seam to a single snapshot — the engine itself
    is covered un-monkeypatched by the tests above."""
    owner = register(client)
    group = make_group(client, owner)
    _upload_voice(client, owner, group)

    real_events = stream_service.group_events

    def bounded(factory, group_id, **kwargs):
        yield from real_events(factory, group_id, poll_seconds=0.01, max_polls=0)

    monkeypatch.setattr(stream_service, "group_events", bounded)
    response = client.get(
        f"/api/v1/groups/{group['id']}/stream", headers=bearer(owner)
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert response.text.startswith("event: snapshot")
    assert '"jobs"' in response.text
