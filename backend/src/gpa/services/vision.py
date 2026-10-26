"""Vision runs: flyer reading + board segmentation (roadmap
B-063, B-064, B-065, B-066, B-069, B-071).

The service half of the vision pipelines. Reading is a JOB, never
a request-thread act (B-069): a person asks, a durable job runs,
and its drafts land in the review queue stamped with the prompt
name + version that made them and "system:vision" as their author
(the requester is on the job payload and the audit — the B-045
discipline, kept identical for pictures).

One job, two shapes of work:

- a **flyer** source → the reading pipeline (ai/vision.py) →
  transcript + alt text on the Flyer row (B-065), links on the
  draft (B-066), one event candidate with region provenance;
- a **board** source → segmentation (ai/segmentation.py — SAM
  when configured, the vision-boxes fallback otherwise, the
  run-time fallback if a configured SAM refuses) → one child
  flyer per poster → the same reading per child.

Partial success is a first-class state (B-069): a board whose
fourth poster defeats the reader still yields three drafts; the
failed child keeps stage ``read_failed``, the job's progress and
the audit say "3 of 4", and nothing is silently dropped. A board
where NOTHING could be read fails its job visibly instead — a
completed lie is worse than a failed job.

Duplicate posters (B-071): a freshly read flyer whose perceptual
hash lands within DUPLICATE_MAX_DISTANCE bits of an earlier
flyer's AND whose drafted event matches it (same title or same
resolved date) earns the ``possible_duplicate`` flag and a
``duplicate_of`` pointer on its draft — a merge SUGGESTION in the
queue. The machine proposes; a human merges (B-043).
"""

import io
import json
import re

from PIL import Image
from sqlalchemy.orm import Session

from gpa.ai import client as ai_client
from gpa.ai import segmentation as segmentation_module
from gpa.ai import vision as vision_pipeline
from gpa.ai.client import ModelError
from gpa.config import Settings, get_settings
from gpa.errors import AppError
from gpa.models.core import FileAsset, Job
from gpa.models.groups import Candidate, Group
from gpa.models.sources import Flyer, Provenance, Source, SourceChunk
from gpa.internal.client import call_internal
from gpa.services import audit, images as image_service, jobs as job_service
from gpa.services import quality, sources as source_service, usage as usage_service
from gpa.storage import get_storage

JOB_KIND = "vision"
MACHINE_AUTHOR = "system:vision"
READABLE_KINDS = ("flyer", "board_photo")
# Two photographs of one poster, hashed by services/images.py,
# differ in a handful of bits; two different posters differ in
# dozens. Six bits of slack catches re-photographs (a brightness
# shift flips a few borderline pixels) without marrying
# strangers. The flag only ever PROPOSES.
DUPLICATE_MAX_DISTANCE = 6
# The placeholder every undescribed flyer carries (B-027): a
# drafted alt text must beat it — and a filename — to count as a
# description under the accessibility law (B-065).
ALT_PLACEHOLDER = "awaiting description"
MIN_ALT_LENGTH = 20

# Printed web addresses in a transcript (B-066's deterministic
# half): the vision model reports the codes it decoded; the plain
# addresses printed as text are also findable by eye, so they are
# found by regex — our own code, no model trust required.
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\])\"']+")


def request_vision_read(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    source_id: str,
) -> Job:
    """Queue a vision job for one flyer/board source (organiser+
    at the door). Returns the job; the run is the worker's."""
    source = source_service.get_source(db, group, source_id)
    if source.kind not in READABLE_KINDS:
        raise AppError(
            "validation_error",
            detail=(
                f"A {source.kind} source has no picture to read — vision "
                "reads flyer and board-photo sources."
            ),
        )
    data = call_internal(
        db, caller="extraction", operation="ai.submit_job",
        payload={
            "kind": JOB_KIND,
            "payload": {
                "group_id": group.id,
                "source_id": source.id,
                "requested_by": actor_label,
            },
        },
    )
    job = db.get(Job, data["job_id"])
    # The job's id joins its payload (enqueue commits first — the
    # id only exists afterwards) so the run can advance its own
    # progress row and stamp its candidates (B-069, as B-045).
    payload = job_service.payload_of(job)
    payload["job_id"] = job.id
    job_service.store_payload(job, payload)
    db.commit()
    audit.record(
        db, actor=actor_label, action="vision.requested",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"job_id": job.id, "kind": source.kind},
    )
    db.commit()
    return job


def is_genuine_alt(text: str, *, filename: str = "") -> bool:
    """The accessibility law's test (B-065): a description is
    genuine when it is real words about the picture — long enough
    to say something, not the placeholder, not a filename."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) < MIN_ALT_LENGTH:
        return False
    if cleaned.lower() == ALT_PLACEHOLDER:
        return False
    if filename and cleaned == filename:
        return False
    return True


def _printed_links(transcript: str) -> list[dict]:
    """Web addresses printed as text in the transcript (B-066)."""
    links: list[dict] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(transcript or ""):
        url = match.group(0).rstrip(".,;")
        if not url.startswith("http"):
            url = "https://" + url
        if url not in seen:
            seen.add(url)
            links.append({"url": url, "kind": "printed"})
    return links


def _siblings(db: Session, group: Group, *, exclude_id: str | None) -> list:
    """The group's other live event candidates, for the quality
    pass (B-044) — the same view extraction computes against."""
    rows = (
        db.query(Candidate)
        .filter(
            Candidate.group_id == group.id,
            Candidate.kind == "event",
            Candidate.status.in_(("pending", "edited", "approved")),
        )
        .all()
    )
    out = []
    for other in rows:
        if exclude_id is not None and other.id == exclude_id:
            continue
        try:
            draft = json.loads(other.draft_json or "{}")
        except json.JSONDecodeError:
            draft = {}
        out.append((other.source_id, draft if isinstance(draft, dict) else {}))
    return out


def _duplicate_suggestion(
    db: Session, *, group: Group, flyer: Flyer, draft: dict
) -> dict | None:
    """B-071: is this poster already in the group, as another
    flyer with a matching drafted event? Returns the suggestion
    payload for the closest match, or None.

    Both halves must agree — the pictures must hash near AND the
    events must match (same title, or the same resolved date) —
    because either alone is common: groups reuse a template, and
    Saturdays repeat weekly.
    """
    if not flyer.phash:
        return None
    title = " ".join(str(draft.get("title") or "").lower().split())
    starts_date = draft.get("starts_date")
    others = (
        db.query(Flyer)
        .filter(Flyer.group_id == group.id, Flyer.id != flyer.id)
        .all()
    )
    best: tuple[int, Flyer] | None = None
    for other in others:
        distance = image_service.hash_distance(flyer.phash, other.phash)
        if distance is None or distance > DUPLICATE_MAX_DISTANCE:
            continue
        if best is None or distance < best[0]:
            best = (distance, other)
    if best is None:
        return None
    distance, other = best
    candidates = (
        db.query(Candidate)
        .filter(
            Candidate.group_id == group.id,
            Candidate.source_id == other.source_id,
            Candidate.kind == "event",
            Candidate.status.in_(("pending", "edited", "approved")),
        )
        .order_by(Candidate.created_at.desc())
        .all()
    )
    for candidate in candidates:
        try:
            other_draft = json.loads(candidate.draft_json or "{}")
        except json.JSONDecodeError:
            continue
        other_title = " ".join(str(other_draft.get("title") or "").lower().split())
        same_event = (title and other_title and title == other_title) or (
            starts_date and other_draft.get("starts_date") == starts_date
        )
        if same_event:
            return {
                "flyer_id": other.id,
                "source_id": other.source_id,
                "candidate_id": candidate.id,
                "event_id": candidate.resulting_entity_id,
                "hash_distance": distance,
            }
    return None


def _read_one_flyer(
    db: Session,
    *,
    storage,
    group: Group,
    source: Source,
    flyer: Flyer,
    requested_by: str,
    job_id: str | None,
    client,
) -> int:
    """Read one flyer photograph into the review queue.

    Returns the number of candidates created (0 or 1 — a flyer is
    one poster). ModelError propagates: the caller decides
    whether that is a failed job (single flyer) or one failed
    poster of several (board).
    """
    if not source.storage_key:
        raise ModelError(f"flyer source {source.id} has no stored image")
    image_bytes = storage.get_bytes(source.storage_key)

    def record(result, prompt_name):
        """Ledger one call in this run's transaction (B-047)."""
        usage_service.record_usage(
            db, result=result, group_id=group.id, source_id=source.id,
            operation=f"vision:{prompt_name}", prompt_name=prompt_name,
            prompt_version=None,
        )

    reading = vision_pipeline.read_flyer_image(
        client,
        image_bytes=image_bytes,
        group_timezone=group.home_timezone or "UTC",
        source_created_at=source.created_at,
        record_usage=record,
    )

    # --- B-065: the flyer itself learns what it says and what
    # it is. The transcript always lands (it is the poster's own
    # words); the alt text lands only when the draft is a genuine
    # description AND no description exists yet — a person's own
    # words (status already complete) are never overwritten by a
    # machine draft.
    # Sealed at rest like its chunk twin below (B-167's field
    # set, extended by the B-180 drill — see sources.seal_flyer_transcript).
    flyer.transcript = source_service.seal_flyer_transcript(
        db, source.group_id, reading.transcript
    )
    asset = db.get(FileAsset, flyer.file_asset_id) if flyer.file_asset_id else None
    if flyer.alt_text_status != "complete" and is_genuine_alt(
        reading.alt_text, filename=asset.filename if asset else ""
    ):
        flyer.alt_text = " ".join(reading.alt_text.split())[:500]
        flyer.alt_text_status = "complete"

    # --- The transcript becomes the flyer's chunk: the citable
    # unit every provenance row points at. A re-read replaces the
    # words (the new reading is the current truth about the
    # poster); the chunk's region is the poster's box on its
    # board, or the whole frame for a lone flyer.
    region_box = None
    if flyer.region_json:
        try:
            region_box = json.loads(flyer.region_json).get("box")
        except (json.JSONDecodeError, AttributeError):
            region_box = None
    chunks = source_service.list_chunks(db, source)
    if chunks:
        chunk = chunks[0]
        chunk.text = source_service.seal_chunk_text(db, source.group_id, reading.transcript)
        chunk.region_json = json.dumps({"box": region_box or [0, 0, 1, 1]})
    else:
        chunk = SourceChunk(
            source_id=source.id, position=0,
            text=source_service.seal_chunk_text(db, source.group_id, reading.transcript),
            region_json=json.dumps({"box": region_box or [0, 0, 1, 1]}),
        )
        db.add(chunk)
        db.flush()

    # --- Nothing readable is a RESULT, not a draft: a reading
    # whose fields carry no values at all (the mock driver's
    # schema-shaped shrug, a genuinely blank photograph) mints
    # no candidate. The flyer keeps its transcript and its
    # flagged alt state and stays visible on the wall (B-070);
    # the queue is for drafts a person can judge. The check runs
    # before supersession, so a blank re-read never eats an
    # earlier real draft.
    fields = reading.draft.get("fields") or {}
    if not any(
        entry.get("value") not in (None, "")
        for entry in fields.values()
        if isinstance(entry, dict)
    ):
        return 0

    # --- A re-read supersedes this source's undecided vision
    # drafts (the B-045 rule): decided rows are history and are
    # never touched; drafts are never edited in place.
    stale = (
        db.query(Candidate)
        .filter(
            Candidate.group_id == group.id,
            Candidate.source_id == source.id,
            Candidate.created_by == MACHINE_AUTHOR,
            Candidate.status.in_(("pending", "edited")),
        )
        .all()
    )
    for old in stale:
        old.status = "superseded"

    # --- B-066: the poster's links = the codes the model decoded
    # plus the addresses printed as text in its own transcript.
    draft = reading.draft
    merged_links: list[dict] = []
    seen_urls: set[str] = set()
    for link in list(draft.get("links") or []) + _printed_links(reading.transcript):
        if link["url"] not in seen_urls:
            seen_urls.add(link["url"])
            merged_links.append(link)
    draft["links"] = merged_links

    candidate = Candidate(
        group_id=group.id,
        kind="event",
        title=str(draft.get("title") or "")[:240],
        created_by=MACHINE_AUTHOR,
        status="pending",
        source_id=source.id,
        draft_json=json.dumps(draft),
        confidence=reading.confidence,
        prompt_name=reading.prompt_name,
        prompt_version=reading.prompt_version,
        job_id=job_id,
        flags_json="[]",
    )
    db.add(candidate)
    db.flush()  # the id exists before provenance points at it
    for row in reading.field_provenance:
        if row["supported"] and row["region"] is not None:
            db.add(
                Provenance(
                    candidate_id=candidate.id,
                    chunk_id=chunk.id,
                    field=row["field"],
                    quote="",
                    region_json=json.dumps(row["region"]),
                )
            )

    # --- Flags: the server's quality vocabulary (B-044) plus the
    # duplicate suggestion (B-071), merged order-stable.
    computed = quality.compute_flags(
        draft, siblings=_siblings(db, group, exclude_id=candidate.id),
        own_source_id=source.id,
    )
    flags = list(dict.fromkeys(computed))
    suggestion = _duplicate_suggestion(db, group=group, flyer=flyer, draft=draft)
    if suggestion is not None:
        draft["duplicate_of"] = suggestion
        candidate.draft_json = json.dumps(draft)
        flags = list(dict.fromkeys(flags + ["possible_duplicate"]))
    candidate.flags_json = json.dumps(flags)

    # A conflict is a two-sided fact (B-044): neighbours the new
    # draft now contradicts earn their flag too.
    from gpa.services import candidates as candidate_service

    for other in (
        db.query(Candidate)
        .filter(
            Candidate.group_id == group.id,
            Candidate.kind == "event",
            Candidate.status.in_(("pending", "edited")),
            Candidate.id != candidate.id,
        )
        .all()
    ):
        candidate_service.refresh_flags(db, other)

    source.stage = "done"
    source.progress = 100
    audit.record(
        db, actor=requested_by, action="vision.read",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={
            "flyer_id": flyer.id,
            "candidate_id": candidate.id,
            "supported_fields": sum(
                1 for row in reading.field_provenance if row["supported"]
            ),
            "alt_text_status": flyer.alt_text_status,
            "duplicate_suspected": suggestion is not None,
        },
    )
    db.commit()
    return 1


def _crop_child(
    db: Session,
    *,
    storage,
    group: Group,
    board_source: Source,
    board_flyer: Flyer,
    board_image: Image.Image,
    region,
    index: int,
) -> tuple[Source, Flyer]:
    """Cut one poster out of the board and open it as a child
    flyer source (B-064). The child's permission record is the
    board's — the grant covered the photograph; the child IS the
    photograph, in part."""
    width, height = board_image.size
    box = region.box
    x0 = max(0, int(box[0] * width))
    y0 = max(0, int(box[1] * height))
    x1 = min(width, int((box[0] + box[2]) * width))
    y1 = min(height, int((box[1] + box[3]) * height))
    crop = board_image.crop((x0, y0, x1, y1))
    encoded, mime, ext = image_service._encode(crop, "PNG")
    thumb = crop.copy()
    thumb.thumbnail((image_service.THUMBNAIL_DIMENSION, image_service.THUMBNAIL_DIMENSION))
    thumb_buffer = io.BytesIO()
    thumb.convert("RGB").save(thumb_buffer, "JPEG", quality=80)
    thumbnail_key = storage.put_bytes(thumb_buffer.getvalue(), "jpg")
    storage_key = storage.put_bytes(encoded, ext)
    import hashlib

    asset = FileAsset(
        group_id=group.id, storage_key=storage_key,
        filename=f"board-poster-{index + 1}.{ext}", mime=mime,
        size=len(encoded), sha256=hashlib.sha256(encoded).hexdigest(),
    )
    db.add(asset)
    db.flush()
    title = f"{board_source.title or 'Board photo'} — poster {index + 1}"
    if region.label:
        title = f"{title} ({region.label})"
    child_source = source_service.create_source(
        db, actor_label=board_source.granted_by, group=group, kind="flyer",
        title=title[:240], granted_scope=board_source.granted_scope,
        chunks=[], status="ready", stage="awaiting_reading", progress=0,
        storage_key=storage_key, file_asset_id=asset.id,
    )
    child = Flyer(
        group_id=group.id, source_id=child_source.id, file_asset_id=asset.id,
        parent_flyer_id=board_flyer.id,
        region_json=json.dumps({"box": box, "label": region.label}),
        thumbnail_key=thumbnail_key, width=crop.size[0], height=crop.size[1],
        phash=image_service.perceptual_hash(crop),
    )
    db.add(child)
    db.commit()
    return child_source, child


def _segment_board(
    db: Session,
    *,
    settings: Settings,
    storage,
    group: Group,
    source: Source,
    flyer: Flyer,
    requested_by: str,
    job_id: str | None,
    client,
    segmenter=None,
) -> dict:
    """Cut a board into child flyers and read each one (B-064 +
    B-069). Progress rides the job row; failures are counted,
    never absorbed silently."""
    if not source.storage_key:
        raise ModelError(f"board source {source.id} has no stored image")
    image_bytes = storage.get_bytes(source.storage_key)

    # Driver choice (B-064): the configured segmenter, with the
    # decided fall-back — a SAM that is unavailable or refuses
    # hands the board to the vision driver, and the audit names
    # the driver that actually cut.
    active = segmenter or segmentation_module.build_segmenter(settings, client)
    driver_used = active.driver_name
    try:
        regions = active.segment(image_bytes=image_bytes)
    except ModelError:
        if driver_used == segmentation_module.VisionBoxSegmenter.driver_name:
            raise
        fallback = segmentation_module.VisionBoxSegmenter(client)
        regions = fallback.segment(image_bytes=image_bytes)
        driver_used = "vision (fallback: SAM refused)"

    # Children are cut once: a re-run of the job re-reads the
    # existing children rather than multiplying posters.
    children = (
        db.query(Flyer)
        .filter(Flyer.parent_flyer_id == flyer.id)
        .order_by(Flyer.created_at, Flyer.id)
        .all()
    )
    if not children:
        board_image = Image.open(io.BytesIO(image_bytes))
        board_image.load()
        pairs = [
            _crop_child(
                db, storage=storage, group=group, board_source=source,
                board_flyer=flyer, board_image=board_image, region=region,
                index=index,
            )
            for index, region in enumerate(regions)
        ]
        children = [child for _src, child in pairs]
        child_sources = {child.id: src for src, child in pairs}
    else:
        child_sources = {
            child.id: db.get(Source, child.source_id) for child in children
        }

    # The board's placeholder chunk (B-028) now tells the truth
    # about what was cut and by whom — its place in the order is
    # kept; its content grows up.
    for chunk in source_service.list_chunks(db, source):
        chunk.region_json = json.dumps(
            {
                "regions": [child.region_json for child in children],
                "segmenter": driver_used,
            }
        )
    audit.record(
        db, actor=requested_by, action="vision.segmented",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"regions": len(children), "segmenter": driver_used},
    )
    db.commit()

    job = db.get(Job, job_id) if job_id else None
    read_ok = 0
    failed = 0
    candidates_made = 0
    total = len(children)
    for position, child in enumerate(children):
        child_source = child_sources[child.id]
        try:
            candidates_made += _read_one_flyer(
                db, storage=storage, group=group, source=child_source,
                flyer=child, requested_by=requested_by, job_id=job_id,
                client=client,
            )
            read_ok += 1
        except ModelError as exc:
            # One ruined poster must not ruin the board (B-069):
            # the child keeps its failure as its stage, the count
            # is audited, and the run continues.
            db.rollback()
            child = db.get(Flyer, child.id)
            child_source = db.get(Source, child.source_id)
            child_source.stage = "read_failed"
            child_source.progress = 0
            audit.record(
                db, actor=requested_by, action="vision.read_failed",
                group_id=group.id, entity_type="source",
                entity_id=child_source.id,
                detail={"flyer_id": child.id, "error": str(exc)[:300]},
            )
            db.commit()
            failed += 1
        if job is not None:
            job.progress = int(100 * (position + 1) / total) if total else 100
            db.commit()

    source.status = "ready"
    source.stage = "segmented"
    source.progress = int(100 * read_ok / total) if total else 100
    db.commit()
    if total and read_ok == 0:
        # Nothing read at all is not a partial success — it is a
        # failed job, and the chassis should say so (retries,
        # dead-letter, visibility).
        raise ModelError(
            f"Segmentation found {total} poster(s) but none could be read."
        )
    if failed:
        audit.record(
            db, actor=requested_by, action="vision.partial",
            group_id=group.id, entity_type="source", entity_id=source.id,
            detail={"read": read_ok, "failed": failed, "total": total},
        )
        db.commit()
    return {
        "segmented": total, "read": read_ok, "failed": failed,
        "candidates": candidates_made, "segmenter": driver_used,
    }


def run_vision_read(
    db: Session,
    *,
    settings: Settings,
    group: Group,
    source: Source,
    requested_by: str,
    job_id: str | None,
    segmenter=None,
) -> dict:
    """Run one vision job's work. Returns the run summary."""
    flyer = image_service.get_flyer_for_source(db, source.id)
    if flyer is None:
        raise RuntimeError(f"vision job names source {source.id} with no flyer row")
    storage = get_storage()
    client = ai_client.get_client()
    if source.kind == "board_photo":
        summary = _segment_board(
            db, settings=settings, storage=storage, group=group,
            source=source, flyer=flyer, requested_by=requested_by,
            job_id=job_id, client=client, segmenter=segmenter,
        )
    else:
        made = _read_one_flyer(
            db, storage=storage, group=group, source=source, flyer=flyer,
            requested_by=requested_by, job_id=job_id, client=client,
        )
        summary = {"read": 1, "failed": 0, "candidates": made}
    audit.record(
        db, actor=requested_by, action="vision.completed",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail=summary,
    )
    db.commit()
    return summary


def handle_vision(db: Session, payload: dict) -> None:
    """The job handler (B-069). A single flyer's ModelError fails
    the job visibly (the chassis retries, then dead-letters); a
    board absorbs per-poster failures as partial success inside
    run_vision_read and only fails when nothing could be read."""
    group = db.get(Group, payload.get("group_id", ""))
    source = db.get(Source, payload.get("source_id", ""))
    if group is None or source is None or source.group_id != group.id:
        raise RuntimeError("vision job names a missing group or source")
    run_vision_read(
        db, settings=get_settings(), group=group, source=source,
        requested_by=payload.get("requested_by", MACHINE_AUTHOR),
        job_id=payload.get("job_id"),
    )


def register_handlers() -> None:
    """Offer the vision handler to the job chassis (B-008)."""
    job_service.register_handler(JOB_KIND, handle_vision)


register_handlers()
