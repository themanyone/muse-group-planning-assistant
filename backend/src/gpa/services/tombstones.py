"""Tombstones + undelete (roadmap B-080).

Deletion is a door that swings both ways for thirty days, and
then closes for good — stated plainly, never silently.

EVENTS soft-delete: the row stays, stamped ``deleted_at``,
invisible to every read (the events service treats it as
missing, and the calendar / feed / wall queries filter it).
Its change history, source links, RSVPs and discussion stay
exactly where they were, so a restore brings the event back
WHOLE. A Tombstone row carries the record: what it was, who
deleted it, and the day the window closes (``purge_after`` =
deleted + 30 days). After the window, restore is refused in
plain words even before the purge runs — the promise was
thirty days, not thirty days plus whenever someone tidies up.

SOURCES keep the B-023 law at full strength — deleting a
source removes its chunks and their provenance IMMEDIATELY
(candidates keep their text and lose their quotes, visibly).
What B-080 adds is a snapshot: before the removal, the source,
its chunks, its flyers and the provenance rows are serialised
into the Tombstone, so an organiser's restore can rebuild the
source with the SAME ids and lift the candidates'
``source_deleted_at`` stamps. Restoring never resurrects a
quote anywhere it was already relied on as gone — it rebuilds
the material a person deleted by accident, which is the only
honest job undelete has.

The purge is the window's other side: expired event
tombstones hard-delete the event (its dependent rows go with
it, children first), and expired tombstones are dropped —
sources' snapshots included, since a source's own rows are
already gone and the snapshot was the last copy.
"""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.core import Tombstone
from gpa.models.events import Event
from gpa.models.groups import Candidate, Group
from gpa.models.sources import Flyer, Provenance, Source, SourceChunk
from gpa.services import audit
from gpa.services import events as event_service
from gpa.services import sources as source_service
from gpa.models.identity import now_utc

WINDOW_DAYS = 30


def _window_end(moment: datetime) -> datetime:
    return moment + timedelta(days=WINDOW_DAYS)


def _open_tombstone(db: Session, group: Group, entity_type: str, entity_id: str):
    return (
        db.query(Tombstone)
        .filter(
            Tombstone.group_id == group.id,
            Tombstone.entity_type == entity_type,
            Tombstone.entity_id == entity_id,
        )
        .first()
    )


# ------------------------------------------------------------------
# Events
# ------------------------------------------------------------------


def delete_event(db: Session, *, actor_label: str, group: Group, event_id: str) -> None:
    """Soft-delete an event: stamp it, tombstone it, audit it.
    The event vanishes from every read at once; nothing it
    carries is touched."""
    event = event_service.get_event(db, group, event_id)
    moment = now_utc()
    event.deleted_at = moment
    db.add(
        Tombstone(
            group_id=group.id,
            entity_type="event",
            entity_id=event.id,
            title=event.title,
            snapshot_json="{}",
            deleted_by=actor_label,
            deleted_at=moment,
            purge_after=_window_end(moment),
        )
    )
    audit.record(
        db, actor=actor_label, action="event.deleted",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"title": event.title, "purge_after": _window_end(moment).isoformat()},
    )
    db.commit()


def restore_event(db: Session, *, actor_label: str, group: Group, event_id: str) -> Event:
    """Bring a deleted event back, whole, inside its window."""
    stone = _open_tombstone(db, group, "event", event_id)
    if stone is None:
        raise AppError("not_found", detail="No deleted event with that id.")
    if now_utc() > stone.purge_after:
        raise AppError(
            "validation_error",
            detail=(
                "That event was deleted more than 30 days ago and "
                "is gone for good — the restore window has closed."
            ),
        )
    event = db.get(Event, event_id)
    if event is None:  # pragma: no cover — the tombstone outlives only pre-purge
        raise AppError("not_found", detail="No deleted event with that id.")
    event.deleted_at = None
    db.delete(stone)
    audit.record(
        db, actor=actor_label, action="event.restored",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"title": event.title},
    )
    db.commit()
    db.refresh(event)
    return event


# ------------------------------------------------------------------
# Sources: snapshot on delete, rebuild on restore
# ------------------------------------------------------------------


def snapshot_source(db: Session, source: Source) -> str:
    """Serialise a source and everything the B-023 deletion is
    about to remove, so a restore can rebuild it identically."""
    chunks = source_service.list_chunks(db, source)
    chunk_ids = [chunk.id for chunk in chunks]
    provenance = (
        db.query(Provenance).filter(Provenance.chunk_id.in_(chunk_ids)).all()
        if chunk_ids
        else []
    )
    flyers = db.query(Flyer).filter(Flyer.source_id == source.id).all()
    return json.dumps(
        {
            "source": {
                "id": source.id, "group_id": source.group_id,
                "kind": source.kind, "title": source.title,
                "granted_by": source.granted_by,
                "granted_scope": source.granted_scope,
                "status": source.status, "stage": source.stage,
                "progress": source.progress,
                "storage_key": source.storage_key,
                "file_asset_id": source.file_asset_id,
                "url": source.url,
                "connector_kind": source.connector_kind,
                "external_id": source.external_id,
                "created_at": source.created_at.isoformat()
                if source.created_at else None,
            },
            "chunks": [
                {
                    "id": chunk.id, "source_id": chunk.source_id,
                    "position": chunk.position, "text": chunk.text,
                    "sender": chunk.sender,
                    "sent_at": chunk.sent_at.isoformat() if chunk.sent_at else None,
                    "timestamp_text": chunk.timestamp_text,
                    "region_json": chunk.region_json,
                }
                for chunk in chunks
            ],
            "provenance": [
                {
                    "id": row.id, "candidate_id": row.candidate_id,
                    "chunk_id": row.chunk_id, "field": row.field,
                    "quote": row.quote, "region_json": row.region_json,
                }
                for row in provenance
            ],
            "flyers": [
                {
                    "id": flyer.id, "group_id": flyer.group_id,
                    "source_id": flyer.source_id,
                    "file_asset_id": flyer.file_asset_id,
                    "parent_flyer_id": flyer.parent_flyer_id,
                    "region_json": flyer.region_json,
                    "thumbnail_key": flyer.thumbnail_key,
                    "width": flyer.width, "height": flyer.height,
                    "transcript": flyer.transcript,
                    "alt_text": flyer.alt_text,
                    "alt_text_status": flyer.alt_text_status,
                    "phash": flyer.phash,
                    "generated": flyer.generated,
                    "created_at": flyer.created_at.isoformat()
                    if flyer.created_at else None,
                }
                for flyer in flyers
            ],
        }
    )


def tombstone_source(db: Session, *, actor_label: str, group: Group, source: Source) -> None:
    """Write a source's Tombstone from its snapshot. Called by
    the sources service inside the deletion, BEFORE the removal
    (the snapshot must describe what still exists). Does not
    commit — the deletion's commit carries it."""
    moment = now_utc()
    db.add(
        Tombstone(
            group_id=group.id,
            entity_type="source",
            entity_id=source.id,
            title=source.title,
            snapshot_json=snapshot_source(db, source),
            deleted_by=actor_label,
            deleted_at=moment,
            purge_after=_window_end(moment),
        )
    )


def restore_source(db: Session, *, actor_label: str, group: Group, source_id: str) -> Source:
    """Rebuild a deleted source from its tombstone snapshot,
    with its original ids, and lift its candidates' stamps."""
    stone = _open_tombstone(db, group, "source", source_id)
    if stone is None:
        raise AppError("not_found", detail="No deleted source with that id.")
    if now_utc() > stone.purge_after:
        raise AppError(
            "validation_error",
            detail=(
                "That source was deleted more than 30 days ago and "
                "is gone for good — the restore window has closed."
            ),
        )
    if db.get(Source, source_id) is not None:
        raise AppError("conflict", detail="That source is already back.")
    snapshot = json.loads(stone.snapshot_json or "{}")

    def _stamp(raw):
        return datetime.fromisoformat(raw) if raw else None

    data = snapshot["source"]
    source = Source(
        id=data["id"], group_id=data["group_id"], kind=data["kind"],
        title=data["title"], granted_by=data["granted_by"],
        granted_scope=data["granted_scope"], status=data["status"],
        stage=data["stage"], progress=data["progress"],
        storage_key=data["storage_key"],
        file_asset_id=data["file_asset_id"],
        url=data["url"],
        connector_kind=data["connector_kind"],
        external_id=data["external_id"],
        created_at=_stamp(data["created_at"]),
    )
    db.add(source)
    db.flush()
    for chunk_data in snapshot.get("chunks", []):
        db.add(
            SourceChunk(
                id=chunk_data["id"], source_id=chunk_data["source_id"],
                position=chunk_data["position"], text=chunk_data["text"],
                sender=chunk_data["sender"],
                sent_at=_stamp(chunk_data["sent_at"]),
                timestamp_text=chunk_data["timestamp_text"],
                region_json=chunk_data["region_json"],
            )
        )
    db.flush()
    for row_data in snapshot.get("provenance", []):
        db.add(
            Provenance(
                id=row_data["id"], candidate_id=row_data["candidate_id"],
                chunk_id=row_data["chunk_id"], field=row_data["field"],
                quote=row_data["quote"], region_json=row_data["region_json"],
            )
        )
    for flyer_data in snapshot.get("flyers", []):
        if db.get(Flyer, flyer_data["id"]) is None:
            db.add(
                Flyer(
                    id=flyer_data["id"], group_id=flyer_data["group_id"],
                    source_id=flyer_data["source_id"],
                    file_asset_id=flyer_data["file_asset_id"],
                    parent_flyer_id=flyer_data["parent_flyer_id"],
                    region_json=flyer_data["region_json"],
                    thumbnail_key=flyer_data["thumbnail_key"],
                    width=flyer_data["width"], height=flyer_data["height"],
                    transcript=flyer_data["transcript"],
                    alt_text=flyer_data["alt_text"],
                    alt_text_status=flyer_data["alt_text_status"],
                    phash=flyer_data["phash"],
                    generated=flyer_data["generated"],
                    created_at=_stamp(flyer_data["created_at"]),
                )
            )
    # The candidates get their source back: the deletion stamp
    # was a statement of fact, and the fact has changed.
    db.query(Candidate).filter(Candidate.source_id == source_id).update(
        {Candidate.source_deleted_at: None}, synchronize_session=False
    )
    db.delete(stone)
    audit.record(
        db, actor=actor_label, action="source.restored",
        group_id=group.id, entity_type="source", entity_id=source_id,
        detail={"title": source.title},
    )
    db.commit()
    db.refresh(source)
    return source


# ------------------------------------------------------------------
# Listing + purge
# ------------------------------------------------------------------


def list_tombstones(db: Session, group: Group) -> list[Tombstone]:
    return (
        db.query(Tombstone)
        .filter(Tombstone.group_id == group.id)
        .order_by(Tombstone.deleted_at, Tombstone.id)
        .all()
    )


def purge_expired(db: Session, *, group: Group | None = None) -> dict:
    """The window's other side: hard-delete every expired
    tombstoned event (children first, then the event) and drop
    every expired tombstone. Source tombstones need no row
    work — the deletion already removed the source; the
    snapshot WAS the last copy, and dropping it is the purge.
    Returns plain counts for whoever runs it (a job, a test).
    """
    moment = now_utc()
    query = db.query(Tombstone).filter(Tombstone.purge_after <= moment)
    if group is not None:
        query = query.filter(Tombstone.group_id == group.id)
    stones = query.all()
    events_purged = 0
    for stone in stones:
        if stone.entity_type == "event":
            event = db.get(Event, stone.entity_id)
            if event is not None:
                event_service.hard_delete_event(db, event)
                events_purged += 1
        db.delete(stone)
    db.commit()
    return {"tombstones": len(stones), "events_purged": events_purged}
