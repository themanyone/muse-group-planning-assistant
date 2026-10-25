"""Source services (roadmap B-023).

The sources core: create / list / get / delete for sources and
their ordered chunks, the permission record carried on every
creation, and the deletion law — deleting a source removes its
chunks and their provenance, and never touches a candidate or any
entity drafted from it (the tombstone rule: the drafted thing keeps
its text, loses its quote).

Every mutation is audited in the same transaction (B-019) and
committed before returning (commit-before-answer, B-003).
"""

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Candidate, Group
from gpa.models.identity import now_utc
from gpa.models.sources import SOURCE_KINDS, Provenance, Source, SourceChunk
from gpa.services import audit


def _validate_kind(kind: str) -> str:
    """Only the roadmap's named kinds may name a source."""
    if kind not in SOURCE_KINDS:
        raise AppError(
            "validation_error",
            detail=f"Source kind must be one of: {', '.join(SOURCE_KINDS)}.",
        )
    return kind


def create_source(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    kind: str,
    title: str = "",
    granted_scope: str = "",
    chunks: list[dict] | None = None,
    status: str = "ready",
    stage: str = "done",
    progress: int = 100,
    storage_key: str | None = None,
    file_asset_id: str | None = None,
    url: str = "",
    connector_kind: str = "",
    external_id: str = "",
) -> Source:
    """Create a source and its chunks, in order, in one transaction.

    ``chunks`` items are dicts with ``text`` and optional ``sender``,
    ``sent_at`` (a datetime or None), ``timestamp_text`` and
    ``region_json``. Positions are assigned here, sequentially, so
    callers hand over an ordered list and never number chunks
    themselves. The permission record (granted_by = the caller,
    granted_scope verbatim) is written on the same row.
    """
    _validate_kind(kind)
    source = Source(
        group_id=group.id,
        kind=kind,
        title=title,
        granted_by=actor_label,
        granted_scope=granted_scope,
        status=status,
        stage=stage,
        progress=progress,
        storage_key=storage_key,
        file_asset_id=file_asset_id,
        url=url,
        connector_kind=connector_kind,
        external_id=external_id,
    )
    db.add(source)
    db.flush()  # The source id must exist before chunks point at it.
    for position, chunk in enumerate(chunks or []):
        db.add(
            SourceChunk(
                source_id=source.id,
                position=position,
                text=chunk.get("text", ""),
                sender=chunk.get("sender"),
                sent_at=chunk.get("sent_at"),
                timestamp_text=chunk.get("timestamp_text"),
                region_json=chunk.get("region_json"),
            )
        )
    audit.record(
        db, actor=actor_label, action="source.created",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"kind": kind, "chunks": len(chunks or [])},
    )
    db.commit()
    return source


def get_source(db: Session, group: Group, source_id: str) -> Source:
    """Fetch one source inside its group, or a clean 404.

    Scoping by group here (not just by id) means a source id from
    another group is simply "not found" — ids never leak across
    group walls.
    """
    source = db.get(Source, source_id)
    if source is None or source.group_id != group.id:
        raise AppError("not_found", detail="That source could not be found.")
    return source


def list_sources(db: Session, group: Group) -> list[Source]:
    """All of a group's sources, oldest first."""
    return (
        db.query(Source)
        .filter(Source.group_id == group.id)
        .order_by(Source.created_at, Source.id)
        .all()
    )


def list_chunks(db: Session, source: Source) -> list[SourceChunk]:
    """A source's chunks in their one true order: by position."""
    return (
        db.query(SourceChunk)
        .filter(SourceChunk.source_id == source.id)
        .order_by(SourceChunk.position)
        .all()
    )


def chunk_count(db: Session, source: Source) -> int:
    """How many chunks a source holds (list views show this)."""
    return db.query(SourceChunk).filter(SourceChunk.source_id == source.id).count()


def add_provenance(
    db: Session, *, candidate_id: str, chunk: SourceChunk, field: str, quote: str
) -> Provenance:
    """Record that one candidate field is supported by one quote.

    Phase 3's extraction engine is the main writer; the core owns
    the row so the deletion law has exactly one home. Commits with
    the caller's flow — this helper does NOT commit: extraction
    writes many rows per candidate and commits once. Callers that
    use it standalone must commit themselves.
    """
    row = Provenance(
        candidate_id=candidate_id, chunk_id=chunk.id, field=field, quote=quote
    )
    db.add(row)
    db.flush()
    return row


def delete_source(db: Session, *, actor_label: str, group: Group, source_id: str) -> None:
    """Delete a source, its chunks, and their provenance — only that.

    The tombstone rule (B-023): candidates drafted from this source
    are NOT deleted and NOT edited. Their provenance rows go (the
    quotes pointed at chunks that no longer exist), so a candidate
    keeps its drafted text and visibly loses its supporting quotes.
    Approved entities in later phases follow the same law through
    their own link rows.
    """
    source = get_source(db, group, source_id)
    # Phase 6 (B-080): snapshot the source into a Tombstone
    # BEFORE anything is removed, so an organiser can restore
    # it inside 30 days. The removal below is unchanged — the
    # B-023 law stands at full strength; the snapshot only
    # makes the accident recoverable.
    from gpa.services import tombstones as tombstone_service

    tombstone_service.tombstone_source(
        db, actor_label=actor_label, group=group, source=source
    )
    # Tombstone stamp: candidates that name this source keep their
    # source_id and gain the moment the material went away (the
    # Candidate columns are plain strings/dates, never a cascade —
    # see models/groups.py). The candidates themselves are untouched.
    db.query(Candidate).filter(Candidate.source_id == source.id).update(
        {Candidate.source_deleted_at: now_utc()}, synchronize_session=False
    )
    chunk_ids = [
        row.id
        for row in db.query(SourceChunk).filter(SourceChunk.source_id == source.id).all()
    ]
    if chunk_ids:
        # Provenance first: it points at the chunks we are removing.
        db.query(Provenance).filter(Provenance.chunk_id.in_(chunk_ids)).delete(
            synchronize_session=False
        )
        db.query(SourceChunk).filter(SourceChunk.source_id == source.id).delete(
            synchronize_session=False
        )
    db.delete(source)
    audit.record(
        db, actor=actor_label, action="source.deleted",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"kind": source.kind, "chunks_removed": len(chunk_ids)},
    )
    db.commit()
