"""File ingest (roadmap B-025).

What this file is for: a member uploads a file — plain text, HTML,
a PDF with a text layer, Markdown, or an .ics calendar file — and
it becomes a source: the bytes in content-addressed storage (B-007)
with a ``FileAsset`` row describing them, and the readable text
extracted into ordered chunks for everything downstream.

The safety law of this slice: **content is sniffed, never trusted
by extension.** A file named ``notes.txt`` whose bytes begin with a
program header is an executable wearing a costume, and it is
rejected for what it IS. The virus-signature check is a named hook
(``virus_scan``): today it knows the standard EICAR test signature
and the executable magics; a real scanner plugs into the same hook
later without the ingest flow changing.

Extraction uses only the standard library, on purpose (D-46):
``html.parser`` for HTML, a small honest PDF text-layer reader for
PDFs (FlateDecode streams + Tj/TJ strings — scanned-image PDFs have
no text layer and are refused in plain words, because pretending
otherwise would invent content), and line unfolding for .ics.
"""

import hashlib
import re
import zlib
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.core import FileAsset
from gpa.models.groups import Group
from gpa.services import sources as source_service

# Executable magics: DOS/Windows MZ, Linux ELF, Mach-O (both
# endians, 32/64-bit). Bytes that start this way are programs.
_EXECUTABLE_MAGICS = (
    b"MZ",
    b"\x7fELF",
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
)
# The standard antivirus test signature (EICAR) — harmless itself,
# and exactly what the hook exists to catch the shape of.
_EICAR = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
# Zip containers (docx/xlsx arrive in a later office phase; here a
# zip is simply not one of this slice's readable kinds).
_ZIP_MAGIC = b"PK\x03\x04"


def virus_scan(data: bytes) -> str | None:
    """The virus-signature hook point (B-025).

    Returns a plain-words reason when the bytes look hostile, else
    None. Kept as one small function so a real scanning engine can
    replace (or stand behind) it without touching ingest.
    """
    if _EICAR in data:
        return "That file matches a known test-virus signature and was refused."
    for magic in _EXECUTABLE_MAGICS:
        if data.startswith(magic):
            return "That file is a program, not a readable document, and was refused."
    return None


def sniff_kind(data: bytes) -> tuple[str, str]:
    """Name what the bytes ARE: (kind, mime).

    Kinds: pdf / html / ics / markdown / text. Anything binary that
    is none of those is refused — the extension is never consulted.
    """
    if data.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if data.startswith(_ZIP_MAGIC):
        raise AppError(
            "validation_error",
            detail="That file is a zip-style container, which this upload cannot read yet.",
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise AppError(
            "validation_error",
            detail="That file is not readable text, a PDF, or a calendar file.",
        ) from None
    head = text.lstrip()[:512].lower()
    if head.startswith("<!doctype html") or head.startswith("<html") or "<html" in head:
        return "html", "text/html"
    if "begin:vcalendar" in head:
        return "ics", "text/calendar"
    return "text", "text/plain"


class _HtmlText(HTMLParser):
    """Collect readable text from HTML, one chunk per block.

    Block elements (paragraphs, headings, list items, table rows)
    close the current chunk; inline markup simply flows into it.
    Script and style content is not readable text and is skipped —
    it is code, not something the group wrote to each other.
    """

    _BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "div", "br"}
    _SKIP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._current: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self._current.append(data.strip())

    def _flush(self):
        text = " ".join(self._current).strip()
        if text:
            self.chunks.append(text)
        self._current = []

    def close(self):
        super().close()
        self._flush()


def _pdf_text(data: bytes) -> str:
    """Extract a PDF's text layer with the standard library only.

    Finds every stream, inflates the FlateDecode ones, and reads the
    literal strings shown by Tj / TJ operators. A PDF that is only
    scanned images has no such strings — the caller refuses it in
    plain words rather than inventing text.
    """
    pieces: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
        raw = match.group(1)
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            content = raw  # Uncompressed stream: read it as-is.
        for literal in re.findall(rb"\((?:\\.|[^\\()])*\)\s*(?:Tj|TJ|')", content):
            inner = literal[1 : literal.rindex(b")")]
            inner = inner.replace(b"\\(", b"(").replace(b"\\)", b")")
            inner = inner.replace(b"\\\\", b"\\")
            pieces.append(inner.decode("latin-1"))
        # TJ arrays carry kerning numbers between strings; the
        # strings themselves were caught above when followed by TJ.
    return "\n".join(p for p in pieces if p.strip())


def _text_chunks(text: str) -> list[dict]:
    """Split plain text / Markdown into paragraph chunks.

    Paragraphs (blank-line separated) are the unit; a single-paragraph
    file is one chunk; a wall of single lines stays line-per-chunk so
    ordering downstream stays honest.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) <= 1:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return [{"text": ln} for ln in lines] or [{"text": text.strip()}]
    return [{"text": p.replace("\n", " ")} for p in paragraphs]


def _ics_chunks(text: str) -> list[dict]:
    """One chunk per calendar event, in the group's own words:
    summary, start, and description as readable lines. Unfolding
    (RFC 5545: a leading space continues the previous line) first."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    chunks: list[dict] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.DOTALL | re.IGNORECASE):
        props: dict[str, str] = {}
        for line in block.splitlines():
            if ":" in line:
                name, _, value = line.partition(":")
                props[name.split(";")[0].strip().upper()] = value.strip()
        lines = []
        if props.get("SUMMARY"):
            lines.append(props["SUMMARY"])
        if props.get("DTSTART"):
            lines.append(f"Starts: {props['DTSTART']}")
        if props.get("LOCATION"):
            lines.append(f"Place: {props['LOCATION']}")
        if props.get("DESCRIPTION"):
            lines.append(props["DESCRIPTION"].replace("\\n", " "))
        if lines:
            chunks.append({"text": "\n".join(lines)})
    return chunks or _text_chunks(text)


def extract_chunks(kind: str, data: bytes) -> list[dict]:
    """Readable text → ordered chunks, per sniffed kind."""
    if kind == "pdf":
        text = _pdf_text(data)
        if not text.strip():
            raise AppError(
                "validation_error",
                detail="That PDF has no readable text layer (it may be scanned images).",
            )
        return _text_chunks(text)
    text = data.decode("utf-8")
    if kind == "html":
        parser = _HtmlText()
        parser.feed(text)
        parser.close()
        chunks = [{"text": c} for c in parser.chunks]
        if not chunks:
            raise AppError("validation_error", detail="That HTML file has no readable text.")
        return chunks
    if kind == "ics":
        return _ics_chunks(text)
    return _text_chunks(text)


_EXTENSION = {"pdf": "pdf", "html": "html", "ics": "ics", "text": "txt"}


def ingest_file(
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
    """Store one uploaded file and open it as a source (B-025).

    Order matters: size → virus hook → sniff → extract, and only
    then storage + rows, so a refused file leaves nothing behind.
    The FileAsset row and the source land in ONE transaction (the
    sources service commits), so the two can never disagree.
    """
    if not data:
        raise AppError("validation_error", detail="That upload was empty.")
    if len(data) > settings.max_upload_bytes:
        raise AppError(
            "validation_error",
            detail=f"That file is larger than the {settings.max_upload_bytes} byte limit.",
        )
    hostile = virus_scan(data)
    if hostile:
        raise AppError("validation_error", detail=hostile)
    kind, mime = sniff_kind(data)
    chunks = extract_chunks(kind, data)
    digest = hashlib.sha256(data).hexdigest()
    storage_key = storage.put_bytes(data, _EXTENSION.get(kind, "txt"))
    asset = FileAsset(
        group_id=group.id, storage_key=storage_key,
        filename=(filename or "upload")[:240], mime=mime,
        size=len(data), sha256=digest,
    )
    db.add(asset)
    db.flush()  # The asset id must exist before the source names it.
    return source_service.create_source(
        db, actor_label=actor_label, group=group, kind="file",
        title=title or filename or "Uploaded file",
        granted_scope=granted_scope, chunks=chunks,
        storage_key=storage_key, file_asset_id=asset.id,
    )
