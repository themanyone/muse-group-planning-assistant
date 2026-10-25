"""Transcription tests (roadmap B-068).

The golden voice note: audio in → the adapter's transcript →
speaker-labelled chunks on a normal text source → the ordinary
extraction pipeline drafts from it downstream. The driver is
the scripted test double behind the adapter; no real speech
endpoint exists in this environment, and none is faked — the
boundary is the adapter, exactly as with vision.
"""

import io
import wave

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.services import jobs as job_service
from gpa.services import sources as source_service
from vision_world import ScriptedVisionDriver, run_one_job

TRANSCRIPT = (
    "Ana: The hall is booked for Saturday at seven\n"
    "Ben: I will bring the projector\n"
    "Ana: And the quiz is postponed to November"
)

VOICE_EVENT_ANSWER = {
    "items": [
        {
            "fields": {
                "title": {"value": "Hall booking", "confidence": 0.9,
                          "quote": "The hall is booked for Saturday at seven"},
                "starts": {"value": "Saturday at seven", "confidence": 0.85,
                           "quote": "booked for Saturday at seven",
                           "date_phrase": "Saturday", "time_phrase": "seven"},
                "place": {"value": "the hall", "confidence": 0.8,
                          "quote": "The hall is booked"},
            }
        }
    ]
}


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 80)
    return buffer.getvalue()


def _upload_note(client, headers, group_id, data: bytes) -> dict:
    response = client.post(
        f"/api/v1/groups/{group_id}/sources/voice?filename=note.wav",
        content=data,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_golden_voice_note_becomes_a_text_source(client, app, db_session):
    driver = ScriptedVisionDriver()
    ai_client.set_client(driver)
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    headers = bearer(owner)
    data = _wav_bytes()
    driver.transcripts[len(data)] = TRANSCRIPT

    note = _upload_note(client, headers, group["id"], data)
    assert note["status"] == "awaiting_transcription"
    assert run_one_job(app, db_session) is True

    after = client.get(
        f"/api/v1/groups/{group['id']}/sources/{note['id']}", headers=headers
    ).json()
    assert after["status"] == "ready"
    assert after["stage"] == "done"
    assert [(c["sender"], c["text"]) for c in after["chunks"]] == [
        ("Ana", "The hall is booked for Saturday at seven"),
        ("Ben", "I will bring the projector"),
        ("Ana", "And the quiz is postponed to November"),
    ]


def test_transcribed_words_are_extractable_downstream(client, app, db_session):
    """B-068's acceptance tail: the transcribed source feeds the
    ordinary extraction pipeline — quotes verify against the
    transcript chunks, and a candidate lands in the queue."""
    driver = ScriptedVisionDriver()
    ai_client.set_client(driver)
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    headers = bearer(owner)
    data = _wav_bytes()
    driver.transcripts[len(data)] = TRANSCRIPT
    note = _upload_note(client, headers, group["id"], data)
    assert run_one_job(app, db_session) is True

    # Swap the adapter to a fixture driver recorded against the
    # transcript chunks the handler just wrote — the same road
    # the golden chat takes (tests/extraction_world.py).
    from gpa.models.groups import Group
    from gpa.models.sources import Source

    group_row = db_session.get(Group, group["id"])
    source_row = db_session.get(Source, note["id"])
    chunks = source_service.list_chunks(db_session, source_row)
    fixture = FixtureDriver()
    schema = promptlib.load_prompt("extraction").schema
    fixture.add_json(
        build_prompt("extraction", chunks, "America/New_York"),
        schema, VOICE_EVENT_ANSWER,
    )
    ai_client.set_client(fixture)

    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/{note['id']}/extract",
        json={"kinds": ["event"]}, headers=headers,
    )
    assert response.status_code == 202, response.text
    assert job_service.process_one(db_session, app.state.settings, "test-worker") is True
    queue = client.get(
        f"/api/v1/groups/{group['id']}/candidates", headers=headers
    ).json()
    (candidate,) = queue
    assert candidate["title"] == "Hall booking"
    detail = client.get(
        f"/api/v1/groups/{group['id']}/candidates/{candidate['id']}",
        headers=headers,
    ).json()
    provenance = {row["field"]: row for row in detail["provenance"]}
    assert provenance["title"]["supported"] is True
    assert provenance["title"]["quote"] == (
        "The hall is booked for Saturday at seven"
    )
    assert detail["draft"]["starts_date"]  # the resolver did its work


def test_a_failed_transcription_keeps_the_note_waiting(client, app, db_session):
    """No scripted transcript for this audio → the driver raises,
    the job fails, and the note stays honestly awaiting —
    visible, retryable, never half-transcribed."""
    driver = ScriptedVisionDriver()  # nothing registered
    ai_client.set_client(driver)
    owner = register(client)
    group = make_group(client, owner)
    headers = bearer(owner)
    note = _upload_note(client, headers, group["id"], _wav_bytes())
    # One attempt only, to keep the failure quick and final:
    from gpa.models.core import Job

    job = db_session.get(Job, note["transcription_job_id"])
    job.max_attempts = 1
    db_session.commit()
    assert run_one_job(app, db_session) is True
    done = client.get(
        f"/api/v1/jobs/{note['transcription_job_id']}", headers=headers
    ).json()
    assert done["status"] == "dead"
    after = client.get(
        f"/api/v1/groups/{group['id']}/sources/{note['id']}", headers=headers
    ).json()
    assert after["status"] == "awaiting_transcription"
