"""Transcription job — the real driver (roadmap B-031 → B-068).

Phase 2's handler was an honest stub: it verified the note and
left it ``awaiting_transcription``, because a job that pretended
to transcribe would be a lie told in the database. Phase 5 keeps
the shape and replaces the body: the audio goes through the AI
adapter's ``transcribe`` (ai/client.py — fixture and mock drivers
in tests and the offline demo; no real endpoint exists in this
environment, and none is faked), and the words that come back
become the source's chunks — a normal text source from that
moment on, extractable by the ordinary pipeline (B-040).

Chunking honours the paste discipline (B-024): a line that opens
with a speaker's name ("Ana: …") files the name as the chunk's
sender and the words as its text; a line without one joins the
previous chunk; nothing is dropped. Speaker labels exist only
where the driver offers them in its text — the handler invents
none.
"""

import re

from sqlalchemy.orm import Session

from gpa.ai import client as ai_client
from gpa.models.core import FileAsset
from gpa.models.groups import Group
from gpa.models.sources import Source, SourceChunk
from gpa.services import audit, jobs as job_service, sources as source_service, usage as usage_service
from gpa.storage import get_storage

JOB_KIND = "transcribe"

# "Ana: the hall is booked" / "Ben Smith: yes" — a short name, a
# colon, then words. Deliberately conservative: a line of prose
# with a colon deep inside it is not a speaker label.
_SPEAKER_LINE = re.compile(r"^([^:\[\]{}]{1,60}):\s+(\S.*)$")


def transcript_chunks(transcript: str) -> list[dict]:
    """Split a transcript into ordered chunk dicts (sender +
    text), the sources core's shape. Nothing is dropped: a line
    that names no speaker joins the previous chunk, and a
    transcript that opens mid-thought starts a senderless chunk.
    """
    chunks: list[dict] = []
    for raw_line in (transcript or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _SPEAKER_LINE.match(line)
        if match:
            chunks.append({"sender": match.group(1).strip(), "text": match.group(2)})
        elif chunks:
            chunks[-1]["text"] += "\n" + line
        else:
            chunks.append({"sender": None, "text": line})
    return chunks


def handle_transcribe(db: Session, payload: dict) -> None:
    """Transcribe one voice note through the adapter (B-068).

    A missing source or asset is a genuine failure (the job
    retries, then dead-letters, visibly). A driver failure is the
    same — ModelError propagates and the note stays honestly
    ``awaiting_transcription`` until a run succeeds.
    """
    source = db.get(Source, payload.get("source_id", ""))
    if source is None or source.kind != "voice":
        raise RuntimeError("transcribe job names no voice source")
    if not source.storage_key:
        raise RuntimeError("voice source has no stored audio")
    group = db.get(Group, payload.get("group_id", "")) if payload.get("group_id") else None
    asset = db.get(FileAsset, source.file_asset_id) if source.file_asset_id else None
    audio_bytes = get_storage().get_bytes(source.storage_key)
    result = ai_client.get_client().transcribe(
        audio_bytes=audio_bytes, mime=asset.mime if asset else "audio/wav"
    )
    usage_service.record_usage(
        db, result=result, group_id=group.id if group else None,
        source_id=source.id, operation="transcribe",
        prompt_name="", prompt_version=None,
    )
    chunks = transcript_chunks(result.text)
    # The note's placeholder chunk (B-031's plain-words marker)
    # is replaced by its real words — replaced, not kept: the
    # marker said "words arrive", and now they have.
    db.query(SourceChunk).filter(SourceChunk.source_id == source.id).delete()
    for position, chunk in enumerate(chunks):
        db.add(
            SourceChunk(
                source_id=source.id, position=position,
                # Sealed at rest (B-167), like every chunk.
                text=source_service.seal_chunk_text(db, source.group_id, chunk["text"]),
                sender=source_service.seal_chunk_sender(db, source.group_id, chunk["sender"]),
            )
        )
    source.status = "ready"
    source.stage = "done"
    source.progress = 100
    audit.record(
        db, actor="system:transcription", action="transcription.completed",
        group_id=group.id if group else None,
        entity_type="source", entity_id=source.id,
        detail={"chunks": len(chunks), "model_id": result.model_id},
    )
    db.commit()


def register_handlers() -> None:
    """Offer the transcribe handler to the job chassis (B-008)."""
    job_service.register_handler(JOB_KIND, handle_transcribe)


# Registration happens on import: any process that loads this
# module (the API via its router, the standalone worker) can run
# the job. Importing it twice is harmless — the registry is a dict.
register_handlers()
