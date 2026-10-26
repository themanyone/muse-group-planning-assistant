"""Core tables shared by every domain (roadmap B-007/B-008/B-019).

Jobs (the durable background-work queue), the append-only audit log,
and file assets (the record of what the storage layer holds).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc


class Job(Base):
    """One unit of background work (B-008).

    A job is a ROW before it is work: enqueue commits the row, the
    worker claims it with an update (so two workers can never run
    the same job), failures retry with exponential backoff, and
    after ``max_attempts`` the job is dead-lettered — visible, never
    silently dropped.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # Work-reported progress, 0–100 (B-033): handlers may advance
    # it while they run; the chassis sets 100 when a job completes.
    # The stream speaks changes to this number as job.progress.
    progress: Mapped[int] = mapped_column(Integer, default=0)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str] = mapped_column(Text, default="")
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Tombstone(Base):
    """The record of a deletion (Phase 6, B-080).

    Deleting an event or a source leaves this row for 30 days:
    what went, who removed it, and when restoration stops being
    possible. For a SOURCE — whose chunks and provenance the
    B-023 law removes at once — ``snapshot_json`` carries the
    whole record (source row, chunks, flyers, provenance) so
    undelete rebuilds it exactly; for an EVENT the row itself
    stays (stamped ``deleted_at``), and the snapshot is just
    the title for the recently-deleted list. After
    ``purge_after``, the purge removes the tombstone and the
    deletion is final — for events, the row goes with it.
    """

    __tablename__ = "tombstones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # "event" | "source"
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    deleted_by: Mapped[str] = mapped_column(String(80))  # audit label
    deleted_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    purge_after: Mapped[datetime] = mapped_column(DateTime)


class AuditLog(Base):
    """Append-only audit record (B-019).

    Rows describe an action by an actor (a user or a seat) on an
    entity, with a JSON detail payload. Rows are never updated or
    deleted by application code — they must survive deletion of the
    entity they describe, so entity references are plain strings,
    not foreign keys.
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(80))  # "user:<id>" | "seat:<id>"
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(48), default="")
    entity_id: Mapped[str] = mapped_column(String(36), default="")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    # The B-170 hash chain: prev_hash names the row before
    # this one, row_hash is the keyed hash of this row's
    # contents + prev_hash. NULL on rows written before the
    # chain existed; verification starts at the first
    # hashed row and says so.
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditChainHead(Base):
    """The audit chain's tip (B-170): exactly one row
    (id=1), extended inside each writer's transaction so
    the chain cannot fork under WAL's serialized writers."""

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FileAsset(Base):
    """A file held by the storage layer (B-007).

    The bytes live under a content-addressed key; this row is the
    human-facing record (name, type, size) that points at them.
    """

    __tablename__ = "file_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id"), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(96), index=True)
    filename: Mapped[str] = mapped_column(String(240), default="")
    mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
