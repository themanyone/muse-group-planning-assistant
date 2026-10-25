"""Voice-note ingest (roadmap B-031).

What this file is for: a member records a voice note — "the hall
is booked for seven, Ana brings the projector" — and uploads the
audio. The note is stored safely, opened as a ``voice`` source
whose single chunk says plainly that it awaits transcription,
and a ``transcribe`` job is enqueued for it. The handler
(services/transcribe.py) runs the audio through the AI adapter
(Phase 5, B-068) and replaces the marker chunk with the note's
real words; until that job lands, the wait is stated, never
dressed up as a transcript.

Audio is sniffed by content, as everywhere in Phase 2: WAV, MP3,
Ogg, FLAC, and M4A/MP4-audio containers are recognised by their
bytes; anything else — including a program renamed ``.mp3`` — is
refused before storage.
"""

import hashlib

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.core import FileAsset, Job
from gpa.models.groups import Group
from gpa.services import files as file_service
from gpa.services import jobs as job_service
from gpa.services import sources as source_service
from gpa.services.transcribe import JOB_KIND as TRANSCRIBE_KIND

# The chunk text a voice source carries until its transcript
# exists: plain words a person (and the review queue) can read,
# in place of words nobody has heard yet.
AWAITING_CHUNK = (
    "Voice note received — its words arrive with the transcription "
    "job. The audio itself is stored with this source."
)


def sniff_audio(data: bytes) -> tuple[str, str]:
    """Name the audio format the bytes ARE: (mime, extension)."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if data[:4] == b"OggS":
        return "audio/ogg", "ogg"
    if data[:4] == b"fLaC":
        return "audio/flac", "flac"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg", "mp3"
    if data[4:8] == b"ftyp":
        return "audio/mp4", "m4a"
    raise AppError(
        "validation_error",
        detail="That file is not a WAV, MP3, Ogg, FLAC, or M4A voice note.",
    )


def ingest_voice(
    db: Session,
    *,
    settings: Settings,
    storage,
    actor_label: str,
    group: Group,
    filename: str,
    data: bytes,
    title: str = "",
    granted_scope: str = "",
) -> tuple[object, Job]:
    """Store one voice note, open its source, enqueue transcription.

    Returns (source, job) — the job is part of the acceptance:
    its row is how "transcription is coming" stays visible
    (GET /api/v1/jobs/{id}) instead of a promise in a log line.
    """
    if not data:
        raise AppError("validation_error", detail="That upload was empty.")
    if len(data) > settings.max_upload_bytes:
        raise AppError(
            "validation_error",
            detail=f"That file is larger than the {settings.max_upload_bytes} byte limit.",
        )
    hostile = file_service.virus_scan(data)
    if hostile:
        raise AppError("validation_error", detail=hostile)
    mime, ext = sniff_audio(data)
    file_service.enforce_upload_quota(db, group, len(data))
    storage_key = storage.put_bytes(data, ext)
    asset = FileAsset(
        group_id=group.id, storage_key=storage_key,
        filename=(filename or "voice-note")[:240], mime=mime,
        size=len(data), sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(asset)
    db.flush()
    source = source_service.create_source(
        db, actor_label=actor_label, group=group, kind="voice",
        title=title or filename or "Voice note",
        granted_scope=granted_scope,
        chunks=[{"text": AWAITING_CHUNK}],
        status="awaiting_transcription", stage="awaiting_transcription",
        storage_key=storage_key, file_asset_id=asset.id,
    )
    job = job_service.enqueue(
        db, settings, kind=TRANSCRIBE_KIND,
        payload={"source_id": source.id, "group_id": group.id},
    )
    return source, job
