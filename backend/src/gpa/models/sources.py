"""Source tables (roadmap Phase 2, B-023).

A *source* is everything a group says, posts, emails, photographs,
or records, brought into the system by a named member who granted
permission for it to be here. The source is the raw material every
later AI step draws from — and nothing else: extraction (Phase 3)
may only quote what a chunk here actually says.

Three tables:

- ``Source`` — the container: its kind, its title, and the
  *permission record* (``granted_by`` / ``granted_scope`` /
  ``created_at``). The permission record is part of the row itself,
  not an afterthought: a source that cannot say who granted it and
  with what scope must never exist (the B-032 rule, enforced from
  the very first source kind onward).
- ``SourceChunk`` — the source's content, in order: one chat
  message, one newsletter section, one page. ``position`` is the
  ordering law; chunks are always read back sorted by it.
- ``Provenance`` — the thread from a drafted candidate field back
  to the exact chunk (and quote) that supports it. Provenance rows
  belong to the source's chunks: delete the source and its
  provenance goes with it — but the candidate itself survives as a
  tombstone (it keeps its drafted text, loses its quote). An
  approved thing is never silently deleted by removing the material
  it was drafted from (B-023 acceptance).
- ``Flyer`` — an image source's picture record (B-027/B-028):
  upright dimensions, a thumbnail, and the alt-text state that
  keeps an undescribed image a visible defect.
- ``InboundAddress`` — a group's email-in token, hashed at rest
  (B-030); the receiver route resolves it to a group.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc

# Every way material can arrive (roadmap §4). Each kind gets its own
# ingest slice in Phase 2; the core accepts all of them from day one
# so no later slice needs a schema change to name its kind.
# "manual" (Phase 4, B-049) is the direct-entry record: when an
# organiser types an event in by hand, the entry itself is recorded
# as a source of this kind — direct entry is material too, with a
# person as its provenance.
SOURCE_KINDS: tuple[str, ...] = (
    "chat_paste",
    "chat_live",
    "newsletter",
    "flyer",
    "board_photo",
    "email",
    "link",
    "voice",
    "file",
    "manual",
)


class Source(Base):
    """One brought-in body of material, with its permission record."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    # --- The permission record (who / what scope; when = created_at).
    # granted_by is an audit label ("user:<id>" | "seat:<id>"): the
    # member who connected or pasted this source. granted_scope says
    # what they granted — e.g. "group-history", "this-thread-only" —
    # in the granting flow's own words, stored verbatim.
    granted_by: Mapped[str] = mapped_column(String(80))
    granted_scope: Mapped[str] = mapped_column(String(240), default="")
    # Ingest state, in plain words the frontend can show verbatim:
    # "ready" for text kinds; later kinds use their own states
    # (e.g. board photos sit at "awaiting_segmentation", B-028).
    status: Mapped[str] = mapped_column(String(32), default="ready")
    # Where the ingest pipeline is, and how far through it the
    # source has got (B-033): ``stage`` names the step ("done",
    # "awaiting_segmentation", "awaiting_transcription",
    # "registered"), ``progress`` is the honest 0-100 the frontend
    # renders as a bar. Synchronous ingests land at done/100; a
    # stage short of done says exactly what the source waits for.
    stage: Mapped[str] = mapped_column(String(48), default="done")
    progress: Mapped[int] = mapped_column(Integer, default=100)
    # When the source's raw bytes live in storage (file/image/voice
    # kinds), the content-addressed key and the FileAsset row that
    # describes them. Text-native sources leave both empty.
    storage_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    file_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("file_assets.id"), nullable=True
    )
    # For link sources (B-029): the URL the content was fetched
    # from (after redirects). The archived chunks are the history;
    # this is only the label that says where they came from.
    url: Mapped[str] = mapped_column(String(2000), default="")
    # For live-chat sources (B-032): which connector the source is
    # bound to, and the conversation's id in that system. The
    # registration IS the permission record; the receiver that
    # fills the source with chunks arrives in Phase 12.
    connector_kind: Mapped[str] = mapped_column(String(48), default="")
    external_id: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class SourceChunk(Base):
    """One ordered piece of a source's content.

    ``sender`` and the timestamps are kept in two forms on purpose:
    ``sent_at`` is the parsed instant when the ingest could read one
    unambiguously, and ``timestamp_text`` is the raw words the export
    carried ("Yesterday at 6:30 PM") — relative words are only ever
    resolved by the named date resolver (Phase 3, D-22), never by a
    guess at ingest time. ``region_json`` holds a flyer/board region
    box (``{"x":…,"y":…,"w":…,"h":…}``) for image chunks; text chunks
    leave it empty.
    """

    __tablename__ = "source_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    sender: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timestamp_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Provenance(Base):
    """One candidate field's supporting evidence (B-023; grown in
    B-040 and B-063).

    The row says: this field of this candidate is supported by this
    exact chunk. For text, the support is a *quote* — the exact
    words. For a picture (Phase 5), the support is a *place*:
    ``region_json`` carries the bounding box ``[x, y, w, h]`` (as
    fractions of the image) the field was read from, the chunk is
    the flyer's transcript chunk, and the quote stays empty —
    provenance for vision is a rectangle a reviewer can tap, not
    words nobody printed. Deleting the source deletes its chunks
    and therefore these rows (service-enforced, with the chunk
    foreign key cascading as backstop) — the candidate row itself
    is never touched by a source deletion.
    """

    __tablename__ = "provenance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id"), index=True
    )
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("source_chunks.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(64), default="")
    quote: Mapped[str] = mapped_column(Text, default="")
    # The region box for vision provenance (B-063), JSON text
    # "[x, y, w, h]"; NULL for every text-provenance row.
    region_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Flyer(Base):
    """An image source's picture record (B-027/B-028, roadmap §4).

    One row per flyer or board photo: the normalised image's place
    in storage (via its FileAsset), its true upright dimensions, a
    thumbnail key, and the description state. ``alt_text`` starts
    as the literal placeholder "awaiting description" and
    ``alt_text_status`` stays ``awaiting_description`` until a real
    description exists (Phase 5's vision pass, or a member's edit):
    an undescribed image is a visible defect in the API, never a
    silent one — every image must carry real alt text.

    Board photos (B-028): the board is ONE source and ONE flyer;
    when Phase 5 segments it, each poster region becomes a child
    flyer whose ``parent_flyer_id`` points here and whose
    ``region_json`` carries the box it was cut from.
    """

    __tablename__ = "flyers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    file_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("file_assets.id"), nullable=True
    )
    parent_flyer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    region_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_key: Mapped[str] = mapped_column(String(96), default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    # What the picture says, once the vision pass reads it (Phase 5).
    transcript: Mapped[str] = mapped_column(Text, default="")
    alt_text: Mapped[str] = mapped_column(String(500), default="awaiting description")
    # awaiting_description | complete — the visible-defect flag.
    # "complete" is earned only by a genuine description (the
    # vision pass's drafted one, or a person's own words, B-065);
    # anything less keeps the defect visible.
    alt_text_status: Mapped[str] = mapped_column(
        String(24), default="awaiting_description"
    )
    # The poster's perceptual hash (Phase 5, B-071): a 64-bit
    # average hash as 16 hex chars, computed at ingest from the
    # normalised image. Two photographs of the same poster hash
    # near-identically even when the bytes differ — that nearness
    # is what the duplicate detector measures. Empty = not hashed
    # (rows that predate Phase 5), which never matches anything.
    phash: Mapped[str] = mapped_column(String(16), default="")
    # An AI-generated image says so, forever (Phase 5, B-067):
    # generated pictures are never passed off as photographs, in
    # any projection, because the fact is a column — not a label
    # a caller could forget to copy.
    generated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class InboundAddress(Base):
    """A group's email-in token (B-030).

    The token is shown once at creation and stored hashed — the
    same discipline as API keys (B-014). The webhook-shaped
    receiver route resolves a presented token to its group;
    anything else is refused and logged. The real mail provider is
    wired in Phase 12; this record is the part that must exist
    first, because the permission it represents must never be an
    afterthought.
    """

    __tablename__ = "inbound_addresses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), default="")
    created_by: Mapped[str] = mapped_column(String(80))  # audit label
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
