"""Source retention (roadmap B-144).

A group may set a retention window (the settings pack's
``source_retention_days`` dial): raw sources older than the
window are HARD-removed by a nightly sweep — their chunks,
flyers, provenance and stored bytes go with them. This is a
privacy act, not housekeeping, and it differs from a
member's manual delete in the one way that matters: there is
NO tombstone and NO restore. What expiry keeps:

- the candidates the source produced (B-023's law — a
  candidate is the group's record; it keeps the source id
  and gains a ``source_deleted_at`` stamp);
- any event a person approved from them;
- an audit row per expired source, counts only — never the
  content that was removed.

The sweep is a job chain like the digest's (B-099's shape):
``ensure_scheduled`` anchors exactly one outstanding
``retention_sweep`` job per group (at 03:30 group-local),
and each run queues the next. A group whose window is unset
(NULL — the default) keeps its sources forever; retention is
opt-in, never a surprise.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.models.identity import now_utc
from gpa.models.core import FileAsset, Job
from gpa.models.groups import Candidate, Group
from gpa.models.sources import Flyer, Source, SourceChunk
from gpa.services import audit
from gpa.services import jobs as job_service

RETENTION_JOB_KIND = "retention_sweep"
SWEEP_TIME = time(3, 30)  # 03:30 group-local, nightly


def next_run_at(group: Group, *, after: datetime | None = None) -> datetime:
    """The next 03:30 in the group's home timezone, strictly
    after ``after`` — returned as a naive UTC datetime, the
    job table's convention."""
    after = after or now_utc()
    zone = ZoneInfo(group.home_timezone or "UTC")
    local_after = after.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    candidate = datetime.combine(local_after.date(), SWEEP_TIME, tzinfo=zone)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return candidate.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _job_group_id(job: Job) -> str | None:
    import json

    try:
        return json.loads(job.payload_json or "{}").get("group_id")
    except ValueError:
        return None


def ensure_scheduled(db: Session, *, group: Group) -> Job | None:
    """Anchor (or re-anchor) the group's retention chain:
    exactly one outstanding sweep job per group. Called when
    the retention dial is saved. An unset window anchors
    nothing (and cancels any queued run)."""
    outstanding = (
        db.query(Job)
        .filter(Job.kind == RETENTION_JOB_KIND, Job.status == "queued")
        .all()
    )
    for job in outstanding:
        if _job_group_id(job) == group.id:
            job.status = "cancelled"
    db.flush()
    if group.source_retention_days is None:
        return None
    return job_service.queue(
        db,
        kind=RETENTION_JOB_KIND,
        payload={"group_id": group.id},
        run_at=next_run_at(group),
    )


def expire_source(db: Session, *, storage, source: Source) -> dict:
    """Hard-remove ONE source (see the module docstring for
    what is kept). Storage objects die only when nothing else
    references them — content addressing means another
    group's identical file is the same object. Returns the
    counts for the sweep's audit row; the CALLER commits."""
    from gpa.models.integrations import LiveSyncState, WebhookEndpoint
    from gpa.models.sources import Provenance
    from gpa.models.sources import SourceChunk as Chunk

    keys: set[str] = set()
    if source.storage_key:
        keys.add(source.storage_key)
    flyers = db.query(Flyer).filter(Flyer.source_id == source.id).all()
    for flyer in flyers:
        if flyer.thumbnail_key:
            keys.add(flyer.thumbnail_key)
    asset = None
    if source.file_asset_id:
        asset = db.get(FileAsset, source.file_asset_id)
        if asset is not None and asset.storage_key:
            keys.add(asset.storage_key)

    chunk_ids = [
        row.id
        for row in db.query(Chunk).filter(Chunk.source_id == source.id)
    ]
    # B-023's law, mirrored: candidates stay, stamped; their
    # provenance pointed at chunks that are going away, and
    # provenance is the source's evidence — it goes too.
    db.query(Candidate).filter(Candidate.source_id == source.id).update(
        {"source_deleted_at": now_utc()}, synchronize_session=False
    )
    if chunk_ids:
        db.query(Provenance).filter(
            Provenance.chunk_id.in_(chunk_ids)
        ).delete(synchronize_session=False)
    db.query(Chunk).filter(Chunk.source_id == source.id).delete(
        synchronize_session=False
    )
    for flyer in flyers:
        db.delete(flyer)
    db.query(LiveSyncState).filter(
        LiveSyncState.source_id == source.id
    ).delete(synchronize_session=False)
    endpoints = (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.source_id == source.id)
        .all()
    )
    for endpoint in endpoints:
        from gpa.models.integrations import WebhookReceipt

        db.query(WebhookReceipt).filter(
            WebhookReceipt.endpoint_id == endpoint.id
        ).delete(synchronize_session=False)
        db.delete(endpoint)
    if asset is not None:
        db.delete(asset)
    db.delete(source)
    db.flush()

    from gpa.services import erasure as erasure_service

    referenced = erasure_service._referenced_keys(db)
    removed = 0
    for key in sorted(keys - referenced):
        if storage.exists(key):
            storage.delete(key)
            removed += 1
    return {"storage_objects_removed": removed}


def run_sweep(
    db: Session, *, storage, group: Group, now: datetime | None = None
) -> dict:
    """Expire every source of the group older than its
    window. Commits before returning (the handler's answer
    and the manual route's answer both ride on it)."""
    now = now or now_utc()
    if group.source_retention_days is None:
        return {"sources_expired": 0, "storage_objects_removed": 0}
    cutoff = now - timedelta(days=group.source_retention_days)
    expired = (
        db.query(Source)
        .filter(Source.group_id == group.id, Source.created_at < cutoff)
        .order_by(Source.created_at)
        .all()
    )
    totals = {"sources_expired": 0, "storage_objects_removed": 0}
    for source in expired:
        result = expire_source(db, storage=storage, source=source)
        totals["sources_expired"] += 1
        totals["storage_objects_removed"] += result["storage_objects_removed"]
        audit.record(
            db, actor="system:retention", action="source.expired",
            group_id=group.id, entity_type="source", entity_id=source.id,
            detail={"storage_objects_removed": result["storage_objects_removed"]},
        )
    db.commit()
    return totals


def handle_retention_sweep(db: Session, payload: dict) -> None:
    """The chain link: sweep this group, then queue tomorrow's
    run — but only while the group still wants a window."""
    group = db.get(Group, payload.get("group_id") or "")
    if group is None or group.source_retention_days is None:
        return
    from gpa.storage import get_storage

    run_sweep(db, storage=get_storage(), group=group)
    job_service.enqueue(
        db, _settings(), kind=RETENTION_JOB_KIND,
        payload={"group_id": group.id}, run_at=next_run_at(group),
    )
    db.commit()


def _settings():
    from gpa.config import get_settings

    return get_settings()


def register_handlers() -> None:
    job_service.register_handler(RETENTION_JOB_KIND, handle_retention_sweep)


register_handlers()
