"""Source schemas: sources and their chunks (Phase 2, B-023+).

The create shape carries the permission record with it — a caller
cannot create a source without saying who grants it (the caller's
own label, filled by the router from the principal) and what scope
was granted. Chunk input is how text-native ingests (paste, B-024)
hand ordered content to the core.
"""

from pydantic import BaseModel, Field


class ChunkIn(BaseModel):
    """One chunk of content handed to the sources core."""

    text: str = Field(default="", max_length=100_000)
    sender: str | None = Field(default=None, max_length=160)
    # The raw timestamp words from the export, kept verbatim; parsing
    # relative dates is the date resolver's job (Phase 3), not ingest's.
    timestamp_text: str | None = Field(default=None, max_length=120)


class ChunkResponse(BaseModel):
    """One chunk as read back, in source order."""

    id: str
    position: int
    text: str
    sender: str | None = None
    # The speaker's id in the source system, when the ingest
    # knows one (Phase 11 live connectors). Account links key
    # on THIS — never on the display name in ``sender``.
    sender_ref: str | None = None
    sent_at: str | None = None
    timestamp_text: str | None = None
    # A board-photo region box / placeholder marker (B-028), JSON
    # text exactly as stored; null for ordinary text chunks.
    region_json: str | None = None


class FlyerResponse(BaseModel):
    """An image source's picture record (B-027/B-028).

    ``alt_text_status`` is the visible-defect flag: it reads
    ``awaiting_description`` — and ``alt_text`` is the literal
    placeholder — until a real description of the image exists
    (Phase 5: the vision pass's drafted description, or a
    person's own words via the flyer PATCH).
    """

    id: str
    source_id: str
    width: int
    height: int
    thumbnail_key: str = ""
    alt_text: str
    alt_text_status: str
    transcript: str = ""
    # Board children (Phase 5, B-064): the board flyer this
    # poster was cut from, and the box it was cut from (JSON
    # text, exactly as stored); both empty for a lone flyer.
    parent_flyer_id: str | None = None
    region_json: str | None = None
    # An AI-generated image says so, forever (B-067).
    generated: bool = False


class SourceCreateRequest(BaseModel):
    """Create a text-native source with its chunks (B-023)."""

    kind: str = Field(max_length=24)
    title: str = Field(default="", max_length=240)
    # What the granting member allowed, in their flow's own words.
    granted_scope: str = Field(default="", max_length=240)
    chunks: list[ChunkIn] = Field(default_factory=list)


class SourceResponse(BaseModel):
    """A source as lists show it: the container + permission record."""

    id: str
    group_id: str
    kind: str
    title: str
    granted_by: str
    granted_scope: str
    status: str
    # Ingest stage + 0-100 progress (B-033): the frontend renders
    # an honest progress bar from these two fields alone.
    stage: str = "done"
    progress: int = 100
    chunk_count: int
    # No storage_key here, deliberately (B-141, D-17): the
    # content address of a stored file is an internal locator,
    # never API surface — no route serves bytes by key, and the
    # key is not the frontend's business. file_asset_id stays:
    # an opaque id that names the asset record, nothing more.
    file_asset_id: str | None = None
    # Where a link source's content was fetched from (B-029);
    # empty for every other kind.
    url: str = ""
    # The live-chat binding (B-032); empty for every other kind.
    connector_kind: str = ""
    external_id: str = ""
    created_at: str


class SourceDetailResponse(SourceResponse):
    """A source with its chunks, in order (B-023)."""

    chunks: list[ChunkResponse] = Field(default_factory=list)
    # The picture record, when this source is an image (B-027).
    flyer: FlyerResponse | None = None


class PasteIngestRequest(BaseModel):
    """Paste a chat export or plain text as a source (B-024)."""

    title: str = Field(default="", max_length=240)
    text: str = Field(min_length=1, max_length=2_000_000)
    granted_scope: str = Field(default="", max_length=240)


class LinkIngestRequest(BaseModel):
    """Paste a link to be fetched and archived (B-029)."""

    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=240)
    granted_scope: str = Field(default="", max_length=240)


class InboundAddressResponse(BaseModel):
    """A group's email-in channel (B-030).

    ``token`` and ``address`` appear ONLY in the creation
    response — the one time the secret exists outside the hash.
    """

    id: str
    group_id: str
    token_prefix: str
    created_at: str
    token: str | None = None
    address: str | None = None


class EmailInRequest(BaseModel):
    """One delivered email, in the webhook shape providers share
    (B-030). The real provider adapter lands in Phase 12."""

    model_config = {"populate_by_name": True}

    sender: str = Field(alias="from", max_length=320)
    subject: str = Field(default="", max_length=500)
    text: str = Field(default="", max_length=2_000_000)
    html: str = Field(default="", max_length=4_000_000)


class LiveSourceRequest(BaseModel):
    """Register a live-chat source (B-032): connector, the
    conversation's id there, and — never optional — the scope
    the granting member gave."""

    connector_kind: str = Field(min_length=1, max_length=48)
    external_id: str = Field(min_length=1, max_length=240)
    granted_scope: str = Field(default="", max_length=240)
    title: str = Field(default="", max_length=240)


class VoiceIngestResponse(SourceDetailResponse):
    """A voice source plus its transcription job (B-031): the
    job id is how the note's pending words stay visible."""

    transcription_job_id: str
    transcription_status: str


class VisionReadAcceptedResponse(BaseModel):
    """202: the reading is a job (Phase 5, B-069); poll the job
    or watch the stream — the same acceptance shape as an
    extraction request (B-045)."""

    job_id: str
    source_id: str
