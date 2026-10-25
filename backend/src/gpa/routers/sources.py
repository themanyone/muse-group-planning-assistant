"""Source routes (roadmap B-023, B-024).

Sources and their chunks, nested under their group. Every handler
authorises through gpa.deps — ``require_group_role`` for role gates,
``require_scope`` for credential gates — and defers every rule to
services/sources.py (paste parsing lives in services/paste.py).
Routers never touch a model directly (B-017).
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.schemas.sources import (
    ChunkResponse,
    EmailInRequest,
    InboundAddressResponse,
    LinkIngestRequest,
    LiveSourceRequest,
    PasteIngestRequest,
    SourceCreateRequest,
    SourceDetailResponse,
    SourceResponse,
    VoiceIngestResponse,
)
from gpa.services import paste as paste_service
from gpa.services import sources as source_service

router = APIRouter(prefix="/api/v1/groups", tags=["sources"])


def _source_response(db: Session, source) -> SourceResponse:
    """Shape a source for list views (with its chunk count)."""
    return SourceResponse(
        id=source.id, group_id=source.group_id, kind=source.kind,
        title=source.title, granted_by=source.granted_by,
        granted_scope=source.granted_scope, status=source.status,
        stage=source.stage, progress=source.progress,
        chunk_count=source_service.chunk_count(db, source),
        file_asset_id=source.file_asset_id,
        url=source.url, connector_kind=source.connector_kind,
        external_id=source.external_id,
        created_at=source.created_at.isoformat(),
    )


def _detail_response(db: Session, source) -> SourceDetailResponse:
    """Shape a source with all of its chunks, in order — plus its
    flyer record when the source is an image (B-027)."""
    from gpa.schemas.sources import FlyerResponse
    from gpa.services import images as image_service

    base = _source_response(db, source)
    chunks = [
        ChunkResponse(
            id=c.id, position=c.position, text=c.text, sender=c.sender,
            sender_ref=c.sender_ref,
            sent_at=c.sent_at.isoformat() if c.sent_at else None,
            timestamp_text=c.timestamp_text, region_json=c.region_json,
        )
        for c in source_service.list_chunks(db, source)
    ]
    flyer_row = image_service.get_flyer_for_source(db, source.id)
    flyer = (
        FlyerResponse(
            id=flyer_row.id, source_id=flyer_row.source_id,
            width=flyer_row.width, height=flyer_row.height,
            thumbnail_key=flyer_row.thumbnail_key,
            alt_text=flyer_row.alt_text,
            alt_text_status=flyer_row.alt_text_status,
            transcript=flyer_row.transcript,
            parent_flyer_id=flyer_row.parent_flyer_id,
            region_json=flyer_row.region_json,
            generated=flyer_row.generated,
        )
        if flyer_row is not None
        else None
    )
    return SourceDetailResponse(**base.model_dump(), chunks=chunks, flyer=flyer)


@router.post("/{group_id}/sources", response_model=SourceDetailResponse, status_code=201)
def create_source(
    body: SourceCreateRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Create a text-native source with its chunks (member+).

    The permission record's ``granted_by`` is the caller — filled
    here from the principal, never trusted from the request body.
    """
    source = source_service.create_source(
        db, actor_label=principal.label, group=context.group,
        kind=body.kind, title=body.title, granted_scope=body.granted_scope,
        chunks=[c.model_dump() for c in body.chunks],
    )
    return _detail_response(db, source)


@router.post(
    "/{group_id}/sources/paste", response_model=SourceDetailResponse, status_code=201
)
def paste_source(
    body: PasteIngestRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Paste a chat export or plain text; it is parsed into chunks
    by the speaker-line parser (B-024) and stored as one source."""
    chunks = paste_service.parse_paste(body.text)
    source = source_service.create_source(
        db, actor_label=principal.label, group=context.group,
        kind="chat_paste", title=body.title, granted_scope=body.granted_scope,
        chunks=chunks,
    )
    return _detail_response(db, source)


@router.get("/{group_id}/sources", response_model=list[SourceResponse])
def list_sources(
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """Every source in the group, oldest first (member+)."""
    return [_source_response(db, s) for s in source_service.list_sources(db, context.group)]


@router.get("/{group_id}/sources/{source_id}", response_model=SourceDetailResponse)
def read_source(
    source_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """One source with its chunks, in order (member+)."""
    source = source_service.get_source(db, context.group, source_id)
    return _detail_response(db, source)


@router.delete("/{group_id}/sources/{source_id}", status_code=204)
def delete_source(
    source_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Delete a source (organiser+). Its chunks and provenance go
    with it; anything drafted from it stays, as a tombstone."""
    source_service.delete_source(
        db, actor_label=principal.label, group=context.group, source_id=source_id
    )
    return None


@router.post("/{group_id}/sources/{source_id}/restore")
def restore_source(
    source_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Undelete a source inside its 30-day window (B-080): the
    tombstone snapshot rebuilds it with its original ids —
    chunks, provenance and flyers — and its candidates lose
    their source-deleted stamps."""
    from gpa.services import tombstones as tombstone_service

    source = tombstone_service.restore_source(
        db, actor_label=principal.label, group=context.group, source_id=source_id
    )
    return _detail_response(db, source)


# --- File upload (B-025) ---------------------------------------------------
# Imported here so the upload route mounts with the sources router;
# the handler itself stays thin and defers to services/files.py.


@router.post(
    "/{group_id}/sources/upload", response_model=SourceDetailResponse, status_code=201
)
async def upload_source(
    request: Request,
    filename: str = "",
    title: str = "",
    granted_scope: str = "",
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Upload one file as a source (member+).

    The file's BYTES are the request body (no multipart form): the
    frontend sends the file with ``filename`` as a query parameter.
    Content is sniffed, never trusted by extension (B-025).
    """
    from gpa.services import files as file_service

    data = await request.body()
    settings = request.app.state.settings
    storage = request.app.state.storage
    source = file_service.ingest_file(
        db, settings=settings, storage=storage,
        actor_label=principal.label, group=context.group,
        filename=filename, data=data,
        title=title, granted_scope=granted_scope,
    )
    return _detail_response(db, source)


# --- Newsletter upload (B-026) ----------------------------------------------
# Same raw-body shape as the file upload; the newsletter service
# chunks by section instead of by block, and keeps links whole.


@router.post(
    "/{group_id}/sources/newsletter", response_model=SourceDetailResponse, status_code=201
)
async def upload_newsletter(
    request: Request,
    filename: str = "",
    title: str = "",
    granted_scope: str = "",
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Upload one HTML newsletter as a source (member+).

    Chunks come back as the newsletter's sections, in order —
    twelve events in the email are twelve chunks here.
    """
    from gpa.services import newsletter as newsletter_service

    data = await request.body()
    settings = request.app.state.settings
    storage = request.app.state.storage
    source = newsletter_service.ingest_newsletter(
        db, settings=settings, storage=storage,
        actor_label=principal.label, group=context.group,
        filename=filename, data=data,
        title=title, granted_scope=granted_scope,
    )
    return _detail_response(db, source)


# --- Image upload: flyers and board photos (B-027, B-028) -------------------
# Same raw-body shape as the other uploads. ``board=true`` marks
# the upload as a whole noticeboard: ONE source, status
# awaiting_segmentation, one placeholder chunk (B-028).


@router.post(
    "/{group_id}/sources/image", response_model=SourceDetailResponse, status_code=201
)
async def upload_image(
    request: Request,
    filename: str = "",
    title: str = "",
    granted_scope: str = "",
    board: bool = False,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Upload one photo as a flyer (or board) source (member+).

    The response's ``flyer`` carries the picture's true upright
    dimensions and its alt-text state — which starts, honestly,
    as ``awaiting_description``.
    """
    from gpa.services import images as image_service

    data = await request.body()
    settings = request.app.state.settings
    storage = request.app.state.storage
    source = image_service.ingest_image(
        db, settings=settings, storage=storage,
        actor_label=principal.label, group=context.group,
        filename=filename, data=data,
        title=title, granted_scope=granted_scope, board_photo=board,
    )
    return _detail_response(db, source)


# --- Link ingest (B-029) ------------------------------------------------------
# The fetch itself lives behind services/linkfetch.py's guards;
# this handler only hands the URL over and shapes the archive.


@router.post(
    "/{group_id}/sources/link", response_model=SourceDetailResponse, status_code=201
)
def ingest_link(
    body: LinkIngestRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Fetch a pasted link and archive its text as a source.

    Private, local, and metadata addresses are refused by the
    fetch guard (422 ``fetch_blocked``) before a single byte is
    requested; a page that cannot be read archives nothing.
    """
    from gpa.services import links as link_service

    settings = request.app.state.settings
    storage = request.app.state.storage
    source = link_service.ingest_link(
        db, settings=settings, storage=storage,
        actor_label=principal.label, group=context.group,
        url=body.url, title=body.title, granted_scope=body.granted_scope,
    )
    return _detail_response(db, source)


# --- Email-in (B-030) ---------------------------------------------------------
# Opening the channel is an admin act; receiving is the token's
# own authority — no user session rides on a delivered email.


@router.post(
    "/{group_id}/inbound-address",
    response_model=InboundAddressResponse,
    status_code=201,
)
def create_inbound_address(
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Open this group's email-in channel (admin+).

    The response carries the full token and the address ONCE;
    only a hash is kept. Mail to the address becomes a source.
    """
    from gpa.services import emailin as emailin_service

    row, token, address = emailin_service.create_inbound_address(
        db, actor_label=principal.label, group=context.group
    )
    return InboundAddressResponse(
        id=row.id, group_id=row.group_id, token_prefix=row.token_prefix,
        created_at=row.created_at.isoformat(), token=token, address=address,
    )


# --- Ingest status stream (B-033) ---------------------------------------------
# GET a source already reports stage + progress (the detail
# response above). This stream is the live companion: a snapshot
# on connect, then job.progress frames as the group's jobs move.


@router.get("/{group_id}/stream")
def group_stream(
    request: Request,
    context: GroupContext = Depends(require_group_role("member")),
):
    """Server-sent events for the group's job-driven work.

    Backed by the Job table (services/stream.py) — what a client
    sees here is the durable record, never an in-memory rumour.
    """
    from fastapi.responses import StreamingResponse

    from gpa.services import stream as stream_service

    database = request.app.state.db
    events = stream_service.group_events(
        database.session, context.group.id, user_id=context.principal.user_id
    )
    return StreamingResponse(events, media_type="text/event-stream")


# --- Voice notes (B-031) + live-chat registration (B-032) ---------------------


@router.post(
    "/{group_id}/sources/voice", response_model=VoiceIngestResponse, status_code=201
)
async def ingest_voice(
    request: Request,
    filename: str = "",
    title: str = "",
    granted_scope: str = "",
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Upload one voice note (raw body; see the file route's note).

    The audio is stored, a ``voice`` source opens in
    awaiting-transcription, and a transcription job is enqueued;
    the response names the job so its status stays visible.
    """
    from gpa.services import transcribe as _transcribe  # noqa: F401 — registers the job handler
    from gpa.services import voice as voice_service

    settings = request.app.state.settings
    storage = request.app.state.storage
    data = await request.body()
    source, job = voice_service.ingest_voice(
        db, settings=settings, storage=storage,
        actor_label=principal.label, group=context.group,
        filename=filename, data=data,
        title=title, granted_scope=granted_scope,
    )
    detail = _detail_response(db, source)
    return VoiceIngestResponse(
        **detail.model_dump(),
        transcription_job_id=job.id, transcription_status=job.status,
    )


@router.post(
    "/{group_id}/sources/live", response_model=SourceDetailResponse, status_code=201
)
def register_live_source(
    body: LiveSourceRequest,
    context: GroupContext = Depends(require_group_role("member")),
    principal: Principal = Depends(require_scope("draft")),
    db: Session = Depends(get_db),
):
    """Register a live-chat source under a person's grant (B-032).

    The registration is the permission record — connector,
    conversation, scope, and the granting member — created
    before any receiver exists. A seat cannot grant it, and a
    scope cannot be empty: services/live.py holds both rules.
    """
    from gpa.services import live as live_service

    source = live_service.register_live_source(
        db, actor_label=principal.label, actor_is_seat=principal.is_seat,
        group=context.group, connector_kind=body.connector_kind,
        external_id=body.external_id, granted_scope=body.granted_scope,
        title=body.title,
    )
    return _detail_response(db, source)


email_router = APIRouter(prefix="/api/v1/email-in", tags=["email-in"])


@email_router.post("/{token}", response_model=SourceDetailResponse, status_code=201)
def receive_email(
    token: str,
    body: EmailInRequest,
    db: Session = Depends(get_db),
):
    """The webhook-shaped receiver: one delivered email → a source.

    The token in the path is the whole credential. A forged
    token is refused (401) and logged — see services/emailin.py.
    (Provider signature verification joins in Phase 12, where the
    provider itself is chosen.)
    """
    from gpa.services import emailin as emailin_service

    source = emailin_service.receive_email(
        db, token=token, sender=body.sender, subject=body.subject,
        text=body.text, html=body.html,
    )
    return _detail_response(db, source)
