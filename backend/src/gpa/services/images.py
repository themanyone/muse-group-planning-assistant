"""Image ingest: flyers and board photos (roadmap B-027, B-028).

What this file is for: a member photographs a paper flyer — or a
whole noticeboard — and uploads the picture. What the group gets
back is a source whose image has been made honest: EXIF
orientation applied (a sideways phone photo reads upright),
size normalised to a sane maximum, a thumbnail generated, and a
``Flyer`` row whose alt text starts as ``awaiting description``.
An image nobody has described yet is a VISIBLE defect:
``alt_text_status`` says so in every API response until Phase 5's
vision pass (or a person) writes a real description.

A board photo is ONE source (B-028). It is not twelve fake
sources and it does not pretend its posters are already separate
flyers: it carries one placeholder chunk whose ``region_json``
says a region box belongs there, and its status/stage sit at
``awaiting_segmentation`` until Phase 5 cuts the regions. The
placeholder keeps the board's place in the chunk order honest —
order is law in this codebase, and "nothing here yet" is a fact
worth recording, not a gap worth hiding.

Content is sniffed by magic bytes, exactly as in file ingest
(B-025): a program renamed ``flyer.jpg`` is refused for what it
is. The stored asset is the NORMALISED image (orientation
applied, capped size) — the bytes downstream phases will read —
in its sniffed format when it carries transparency, else JPEG.
"""

import hashlib
import io
import json

from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.core import FileAsset
from gpa.models.groups import Group
from gpa.models.sources import Flyer
from gpa.services import files as file_service
from gpa.services import sources as source_service

# The largest dimension a normalised image may keep. Flyers are
# read by the vision pass and shown on screens; beyond this,
# pixels cost storage and time without adding legibility.
MAX_DIMENSION = 2400
THUMBNAIL_DIMENSION = 320

# Magic bytes per format: (prefix, format name, mime, extension).
_MAGICS = (
    (b"\x89PNG\r\n\x1a\n", "PNG", "image/png", "png"),
    (b"\xff\xd8\xff", "JPEG", "image/jpeg", "jpg"),
    (b"GIF87a", "GIF", "image/gif", "gif"),
    (b"GIF89a", "GIF", "image/gif", "gif"),
)


def sniff_image(data: bytes) -> tuple[str, str, str]:
    """Name the image format the bytes ARE: (PIL format, mime, ext).

    WebP is recognised by its RIFF container header. Anything else
    is refused — the filename's extension is never consulted.
    """
    for magic, fmt, mime, ext in _MAGICS:
        if data.startswith(magic):
            return fmt, mime, ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP", "image/webp", "webp"
    raise AppError(
        "validation_error",
        detail="That file is not a PNG, JPEG, GIF, or WebP image.",
    )


def _encode(image: Image.Image, pil_format: str) -> tuple[bytes, str, str]:
    """Encode a normalised image: (bytes, mime, extension).

    Transparency survives only in formats that carry it; anything
    else is stored as JPEG, the flyer photograph's natural home.
    """
    buffer = io.BytesIO()
    if pil_format in ("PNG", "WEBP") and image.mode in ("RGBA", "LA", "PA"):
        image.save(buffer, pil_format)
        mime = "image/png" if pil_format == "PNG" else "image/webp"
        return buffer.getvalue(), mime, pil_format.lower()
    if pil_format == "GIF":
        image.save(buffer, "GIF")
        return buffer.getvalue(), "image/gif", "gif"
    image.convert("RGB").save(buffer, "JPEG", quality=90)
    return buffer.getvalue(), "image/jpeg", "jpg"


def _normalise(data: bytes) -> tuple[Image.Image, tuple[int, int]]:
    """Open an image, apply its EXIF orientation, cap its size.

    Returns the normalised image and its true (width, height) —
    "true" meaning as a person sees it, which is the whole point:
    orientation 6 on a sideways phone photo swaps the dimensions,
    and the swap is what "reads upright" means in a test.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise AppError(
            "validation_error", detail="That image could not be read."
        ) from None
    image = ImageOps.exif_transpose(image)
    if max(image.size) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    return image, image.size


def get_flyer_for_source(db: Session, source_id: str) -> Flyer | None:
    """The flyer row belonging to a source, when it has one."""
    return db.query(Flyer).filter(Flyer.source_id == source_id).one_or_none()


# --- Perceptual hash (Phase 5, B-071) --------------------------------------
#
# An average hash, written here from the idea itself (D-46): shrink
# the picture to 8x8 greys, take the mean, and keep one bit per
# pixel — brighter than the mean or not. Two photographs of the
# same poster differ in bytes, lighting, and small shifts, but
# their 64 bits land close; two different posters land far apart.
# The hash is a *suggestion engine* only: nearness proposes a
# duplicate to a human, it never merges anything by itself.

AHASH_SIZE = 8


def perceptual_hash(image: Image.Image) -> str:
    """The image's 64-bit average hash, as 16 hex characters."""
    small = image.convert("L").resize(
        (AHASH_SIZE, AHASH_SIZE), Image.Resampling.LANCZOS
    )
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | (1 if pixel >= mean else 0)
    return f"{value:016x}"


def hash_distance(a: str, b: str) -> int | None:
    """Bits differing between two hashes (the Hamming distance),
    or None when either side is unhashed — an unknown hash is not
    a near hash, and must never read as a match."""
    if not a or not b or len(a) != 16 or len(b) != 16:
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None


def ingest_image(
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
    board_photo: bool = False,
):
    """Store one photo and open it as a flyer/board source.

    Same discipline as every ingest: size → virus hook → sniff →
    normalise, and only then storage + rows. Asset + source land
    in one transaction (inside create_source, as everywhere); the
    Flyer row follows in a second commit because it must name the
    source it describes — if that commit ever failed, the source
    would show a missing flyer (a visible defect), never a lie.
    Board photos differ only in kind, status/stage, and the one
    placeholder chunk that holds their regions' place (B-028).
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
    # Refuses non-images by content, not by name. The sniffed
    # format is threaded into _encode explicitly: EXIF transposing
    # returns a copy that no longer knows its original format.
    pil_format, _, _ = sniff_image(data)
    image, (width, height) = _normalise(data)
    encoded, mime, ext = _encode(image, pil_format)

    # The thumbnail is a small JPEG whatever the source format:
    # it exists to be glanced at in lists, not to be read closely.
    thumb = image.copy()
    thumb.thumbnail((THUMBNAIL_DIMENSION, THUMBNAIL_DIMENSION))
    thumb_buffer = io.BytesIO()
    thumb.convert("RGB").save(thumb_buffer, "JPEG", quality=80)
    thumbnail_key = storage.put_bytes(thumb_buffer.getvalue(), "jpg")

    storage_key = storage.put_bytes(encoded, ext)
    asset = FileAsset(
        group_id=group.id, storage_key=storage_key,
        filename=(filename or "photo")[:240], mime=mime,
        size=len(encoded), sha256=hashlib.sha256(encoded).hexdigest(),
    )
    db.add(asset)
    db.flush()  # The asset id must exist before the source names it.

    kind = "board_photo" if board_photo else "flyer"
    chunks: list[dict] = []
    status, stage = "ready", "done"
    if board_photo:
        status = stage = "awaiting_segmentation"
        # One placeholder chunk: the regions Phase 5 will cut are
        # not known yet, but the board's content slot exists from
        # today, marked as a placeholder in its region_json.
        chunks = [{"text": "", "region_json": json.dumps({"placeholder": True})}]
    source = source_service.create_source(
        db, actor_label=actor_label, group=group, kind=kind,
        title=title or filename or ("Board photo" if board_photo else "Flyer photo"),
        granted_scope=granted_scope, chunks=chunks, status=status,
        stage=stage, storage_key=storage_key, file_asset_id=asset.id,
    )
    flyer = Flyer(
        group_id=group.id, source_id=source.id, file_asset_id=asset.id,
        thumbnail_key=thumbnail_key, width=width, height=height,
        # The duplicate detector's raw material (B-071), computed
        # from the normalised image so re-photographed posters
        # hash near their first upload.
        phash=perceptual_hash(image),
    )
    db.add(flyer)
    db.commit()
    return source
