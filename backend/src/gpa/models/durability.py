"""Durability tables (roadmap B-171): draft autosaves and
resumable upload sessions."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc


class DraftAutosave(Base):
    """One editor's server-side autosave of a draft in flight.

    Keyed by (group, user, target): a candidate or event
    being edited. ``payload_json`` is the editor's current
    draft object; ``version`` rises on every accepted
    autosave. A save naming a stale ``base_version`` does not
    overwrite — it lands as a CONFLICT copy (a second row
    with ``is_conflict`` true, kept beside the live one) and
    the writer is told: your words are safe, and so are the
    other editor's."""

    __tablename__ = "draft_autosaves"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    target_kind: Mapped[str] = mapped_column(String(24))  # "candidate" | "event"
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_conflict: Mapped[bool] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )


class UploadSession(Base):
    """One resumable chunked upload (B-171).

    Parts rest as files under the storage root's ``uploads/``
    area (never in the content store) until completion
    assembles them in order, verifies the whole-file hash,
    and hands the bytes to the ordinary ingest path. The
    row is the resume state: a client (or a restarted
    server) reads which parts have landed and sends only
    the missing ones — parts are accepted in any order, and
    each part's size is verified against its position."""

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    filename: Mapped[str] = mapped_column(String(240), default="upload")
    total_bytes: Mapped[int] = mapped_column(Integer)
    chunk_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    parts_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="open")
    source_id: Mapped[str] = mapped_column(String(36), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
