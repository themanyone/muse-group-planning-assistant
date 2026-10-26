"""Resumable chunked uploads (roadmap B-171).

The one-shot upload (B-025) dies with its connection; a big
flyer on a bad network starts over forever. This service is
the resumable road over the same ingest:

* ``begin`` declares the file (name, total size, chunk size,
  whole-file SHA-256) and opens a session;
* ``put_part`` accepts parts IN ANY ORDER — each part's size
  is verified against its position (offset law: part *i*
  covers bytes [i·chunk, min((i+1)·chunk, total)) ) — and
  rests as a file under the storage root's ``uploads/`` area;
* ``status`` reports received and missing parts (the resume
  contract: send only what's missing — after a kill, after a
  restart, from a fresh client);
* ``complete`` assembles the parts in order, verifies the
  whole-file hash, and hands the bytes to the ordinary
  ingest — quota, sniffing, and extraction all apply exactly
  as for a one-shot upload. The hand-off dispatches on the
  SNIFFED kind, as the one-shot roads do: image bytes go to
  the B-027 image ingest (a flyer can arrive resumably —
  before the B-180 drill, completion sent every file to the
  text/PDF ingest, which refused PNGs outright, so no flyer
  could ever complete this road), everything else to B-025.
  A hash mismatch refuses completion and leaves the session
  open for repair.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.durability import UploadSession
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.services import files as file_service
from gpa.services import images as image_service


def part_count(row: UploadSession) -> int:
    return -(-row.total_bytes // row.chunk_bytes)  # ceil


def expected_part_size(row: UploadSession, index: int) -> int:
    start = index * row.chunk_bytes
    return min(row.chunk_bytes, row.total_bytes - start)


def received_parts(row: UploadSession) -> list[int]:
    return sorted(int(i) for i in json.loads(row.parts_json or "[]"))


def _parts_dir(settings: Settings, row: UploadSession) -> Path:
    return Path(settings.storage_dir) / "uploads" / row.id


def status_dict(row: UploadSession) -> dict:
    received = received_parts(row)
    total_parts = part_count(row)
    return {
        "upload_id": row.id,
        "filename": row.filename,
        "total_bytes": row.total_bytes,
        "chunk_bytes": row.chunk_bytes,
        "parts_total": total_parts,
        "parts_received": received,
        "parts_missing": [i for i in range(total_parts) if i not in received],
        "status": row.status,
        "source_id": row.source_id or None,
    }


def begin(
    db: Session,
    *,
    user_id: str,
    group: Group,
    filename: str,
    total_bytes: int,
    chunk_bytes: int,
    sha256: str,
) -> UploadSession:
    if total_bytes <= 0:
        raise AppError("validation_error", detail="An upload must declare a positive size.")
    if chunk_bytes <= 0:
        raise AppError("validation_error", detail="A chunk size must be positive.")
    if len(sha256) != 64:
        raise AppError("validation_error", detail="An upload must declare its SHA-256.")
    row = UploadSession(
        group_id=group.id, user_id=user_id, filename=(filename or "upload")[:240],
        total_bytes=total_bytes, chunk_bytes=chunk_bytes, sha256=sha256.lower(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(db: Session, group: Group, upload_id: str) -> UploadSession:
    row = db.get(UploadSession, upload_id)
    if row is None or row.group_id != group.id:
        raise AppError("not_found", detail="That upload could not be found.")
    return row


def put_part(
    db: Session, settings: Settings, row: UploadSession, index: int, data: bytes
) -> dict:
    if row.status != "open":
        raise AppError("upload_incomplete", detail="That upload is already completed.")
    total_parts = part_count(row)
    if index < 0 or index >= total_parts:
        raise AppError(
            "upload_part_size",
            detail=f"Part {index} is outside this upload's {total_parts} parts.",
        )
    expected = expected_part_size(row, index)
    if len(data) != expected:
        raise AppError(
            "upload_part_size",
            detail=(
                f"Part {index} must be {expected} bytes at its position; "
                f"it arrived as {len(data)}."
            ),
        )
    parts_dir = _parts_dir(settings, row)
    parts_dir.mkdir(parents=True, exist_ok=True)
    target = parts_dir / f"{index}.part"
    fd, tmp = tempfile.mkstemp(dir=str(parts_dir))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    received = set(received_parts(row))
    received.add(index)
    row.parts_json = json.dumps(sorted(received))
    db.commit()
    db.refresh(row)
    return status_dict(row)


def complete(
    db: Session,
    *,
    settings: Settings,
    storage,
    actor_label: str,
    group: Group,
    row: UploadSession,
):
    """Assemble, verify, ingest. Returns the created source."""
    if row.status == "completed" and row.source_id:
        from gpa.models.sources import Source

        return db.get(Source, row.source_id)
    received = received_parts(row)
    if len(received) != part_count(row):
        raise AppError(
            "upload_incomplete",
            detail=f"That upload is missing parts: "
            f"{[i for i in range(part_count(row)) if i not in received]}.",
        )
    parts_dir = _parts_dir(settings, row)
    blob = bytearray()
    for index in range(part_count(row)):
        blob += (parts_dir / f"{index}.part").read_bytes()
    data = bytes(blob)
    if len(data) != row.total_bytes or hashlib.sha256(data).hexdigest() != row.sha256:
        raise AppError(
            "upload_hash_mismatch",
            detail="The assembled file does not match the declared SHA-256; "
            "the session stays open so the bad part can be re-sent.",
        )
    # Dispatch on the sniffed kind (the B-180 drill's find):
    # image bytes are a flyer, and the file ingest honestly
    # refuses them — the image ingest is their one-shot road,
    # so it is this road's completion too. The sniff refuses
    # nothing here: a non-image simply falls through to files.
    try:
        image_service.sniff_image(data)
        is_image = True
    except AppError:
        is_image = False
    if is_image:
        source = image_service.ingest_image(
            db, settings=settings, storage=storage, actor_label=actor_label,
            group=group, filename=row.filename, data=data,
        )
    else:
        source = file_service.ingest_file(
            db, settings=settings, storage=storage, actor_label=actor_label,
            group=group, filename=row.filename, data=data,
        )
    row.status = "completed"
    row.source_id = source.id
    row.completed_at = now_utc()
    db.commit()
    # The parts have done their work; the content store holds
    # the file now. A failure to clean is litter, not loss.
    for index in range(part_count(row)):
        try:
            (parts_dir / f"{index}.part").unlink()
        except OSError:
            pass
    try:
        parts_dir.rmdir()
    except OSError:
        pass
    return source


__all__ = [
    "begin",
    "complete",
    "expected_part_size",
    "get_session",
    "part_count",
    "put_part",
    "received_parts",
    "status_dict",
]
