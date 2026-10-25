"""Newsletter ingest (roadmap B-026).

What this file is for: an HTML email — the group's newsletter —
becomes a source whose chunks are its SECTIONS, in order. A
newsletter with twelve events stays twelve sections: the section
structure is the meaning, so headings (h1/h2/h3) and horizontal
rules open new chunks, and everything else flows into the current
one.

Links are kept WITH their anchor text, rendered inline as
``anchor text (https://…)`` — a bare URL with no words, or words
with no URL, would each lose half of what the organiser wrote.
Images cannot be read as text, so each becomes its alt text when
the sender provided one, or the marked placeholder
``[image: no description]`` when they did not. The placeholder is
deliberate and visible (the alt-text law: an undescribed image is
a defect you can see, never a silent gap).

Like file ingest (B-025), the order is size → virus hook → parse,
and only then storage + rows in one transaction, so a refused
newsletter leaves nothing behind. The raw HTML is stored via
B-007, so the archived original — not a re-render — is what later
phases can go back to.
"""

import hashlib
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.core import FileAsset
from gpa.models.groups import Group
from gpa.services import files as file_service
from gpa.services import sources as source_service

# Tags that open a new section chunk when they START.
_SECTION_TAGS = {"h1", "h2", "h3", "hr"}
# Block tags that end a line of text inside a section.
_BLOCK_TAGS = {"p", "div", "li", "tr", "blockquote"}
# Content that is code or metadata, not the group's words. The
# <head> (its <title> above all) is the email's envelope, not one
# of its sections.
_SKIP_TAGS = {"script", "style", "head"}


class _NewsletterParser(HTMLParser):
    """Walk one HTML email, collecting section chunks.

    State: the lines of the section being built, plus — while an
    ``<a>`` is open — its href and the anchor words collected so
    far, so the closed link can be rendered with both halves.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[str] = []
        self._lines: list[str] = []
        # Word fragments of the line being built; inline tags split
        # text into fragments that only join when the line ends.
        self._words: list[str] = []
        self._skip_depth = 0
        self._link_href: str | None = None
        self._link_words: list[str] = []

    # -- section / line plumbing ------------------------------------
    def _end_line(self) -> None:
        """Close the current text line, if it has any words."""
        # Lines accumulate as pre-split fragments; joining happens
        # when a line ends so inline tags never glue words together.
        # (Fragments live in _words; see handle_data.)
        words = " ".join(self._words).strip()
        if words:
            self._lines.append(words)
        self._words = []

    def _end_section(self) -> None:
        """Close the current section into a chunk, if non-empty."""
        self._end_line()
        text = "\n".join(self._lines).strip()
        if text:
            self.sections.append(text)
        self._lines = []

    # -- HTMLParser hooks --------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attr = dict(attrs)
        if tag in _SECTION_TAGS:
            self._end_section()
        elif tag in _BLOCK_TAGS or tag == "br":
            self._end_line()
        elif tag == "a":
            self._link_href = attr.get("href")
            self._link_words = []
        elif tag == "img":
            alt = (attr.get("alt") or "").strip()
            marker = f"[image: {alt}]" if alt else "[image: no description]"
            if self._link_href is not None:
                self._link_words.append(marker)
            else:
                self._words.append(marker)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._link_href is not None:
            anchor = " ".join(self._link_words).strip()
            rendered = f"{anchor} ({self._link_href})" if anchor else self._link_href
            self._words.append(rendered)
            self._link_href = None
            self._link_words = []
        elif tag in _BLOCK_TAGS or tag in _SECTION_TAGS:
            self._end_line()

    def handle_data(self, data):
        if self._skip_depth:
            return
        if data.strip():
            if self._link_href is not None:
                self._link_words.append(data.strip())
            else:
                self._words.append(data.strip())

    def close(self):
        super().close()
        self._end_section()


def parse_newsletter_html(html: str) -> list[dict]:
    """HTML email → ordered section chunk dicts (sources-core shape)."""
    parser = _NewsletterParser()
    parser.feed(html)
    parser.close()
    return [{"text": section} for section in parser.sections]


def ingest_newsletter(
    db: Session,
    *,
    settings: Settings,
    storage,
    actor_label: str,
    group: Group,
    filename: str,
    data: bytes,
    title: str = "",
    granted_scope: str = "",
):
    """Store one HTML newsletter and open it as a source (B-026).

    Same discipline as file ingest: checks first, rows last, one
    transaction — a refused newsletter leaves nothing behind.
    """
    if not data:
        raise AppError("validation_error", detail="That upload was empty.")
    if len(data) > settings.max_upload_bytes:
        raise AppError(
            "validation_error",
            detail=f"That file is larger than the {settings.max_upload_bytes} byte limit.",
        )
    hostile = file_service.virus_scan(data)
    if hostile:
        raise AppError("validation_error", detail=hostile)
    try:
        html = data.decode("utf-8")
    except UnicodeDecodeError:
        raise AppError(
            "validation_error", detail="That newsletter is not readable UTF-8 HTML."
        ) from None
    if "<" not in html:
        raise AppError(
            "validation_error",
            detail="That newsletter has no HTML in it — use the file upload for plain text.",
        )
    chunks = parse_newsletter_html(html)
    if not chunks:
        raise AppError("validation_error", detail="That newsletter has no readable sections.")
    file_service.enforce_upload_quota(db, group, len(data))
    digest = hashlib.sha256(data).hexdigest()
    storage_key = storage.put_bytes(data, "html")
    asset = FileAsset(
        group_id=group.id, storage_key=storage_key,
        filename=(filename or "newsletter.html")[:240], mime="text/html",
        size=len(data), sha256=digest,
    )
    db.add(asset)
    db.flush()  # The asset id must exist before the source names it.
    return source_service.create_source(
        db, actor_label=actor_label, group=group, kind="newsletter",
        title=title or filename or "Newsletter",
        granted_scope=granted_scope, chunks=chunks,
        storage_key=storage_key, file_asset_id=asset.id,
    )
