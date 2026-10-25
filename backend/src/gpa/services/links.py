"""Link ingest (roadmap B-029).

What this file is for: a member pastes a link — an event page, an
article — and the assistant archives what the page SAYS as a
source's chunks. The archive is the point: a page that later
changes (or vanishes, or was quietly edited) cannot rewrite what
the group read. The source records the final URL it was fetched
from; the chunks are frozen at fetch time.

Every byte arrives through services/linkfetch.py, the one guarded
road out — this module contains no fetching of its own, on
purpose: an unguarded fetch written here by habit is exactly the
hole B-029 exists to close. Readable text comes out of the same
extractor file ingest uses (B-025), so a page and an uploaded
HTML file are chunked by one rule.
"""

import hashlib

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.core import FileAsset
from gpa.models.groups import Group
from gpa.services import files as file_service
from gpa.services import linkfetch
from gpa.services import sources as source_service


def ingest_link(
    db: Session,
    *,
    settings: Settings,
    storage,
    actor_label: str,
    group: Group,
    url: str,
    title: str = "",
    granted_scope: str = "",
    transport=None,
    resolver=None,
):
    """Fetch one pasted link the guarded way and archive it.

    ``transport``/``resolver`` are the linkfetch seams, passed
    through untouched — tests drive a fake internet through them;
    production callers leave them None and get the real guards.
    A refused or failed fetch creates NO source: nothing about a
    page we could not honestly read enters the group's material.
    """
    final_url, body, content_type = linkfetch.fetch(
        url,
        timeout=settings.link_fetch_timeout_seconds,
        max_bytes=settings.link_fetch_max_bytes,
        transport=transport,
        resolver=resolver,
    )
    kind = "html" if "text/html" in content_type else "text"
    chunks = file_service.extract_chunks(kind, body)
    if not chunks:
        raise AppError("validation_error", detail="That page has no readable text.")
    storage_key = storage.put_bytes(body, "html" if kind == "html" else "txt")
    asset = FileAsset(
        group_id=group.id, storage_key=storage_key,
        filename="link-archive", mime=content_type,
        size=len(body), sha256=hashlib.sha256(body).hexdigest(),
    )
    db.add(asset)
    db.flush()
    return source_service.create_source(
        db, actor_label=actor_label, group=group, kind="link",
        title=title or final_url, granted_scope=granted_scope,
        chunks=chunks, storage_key=storage_key, file_asset_id=asset.id,
        url=final_url,
    )
