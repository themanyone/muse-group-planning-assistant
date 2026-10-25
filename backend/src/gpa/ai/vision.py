"""The vision reading pipeline (roadmap B-063; Doc 07 §5).

One flyer in, one verified draft out — the picture twin of the
text extraction pipeline (ai/extraction.py), with the one
difference the medium demands: **provenance is a place, not a
quote.** Every field the model reads names the rectangle it was
read from (``region``: [x, y, w, h] as fractions of the image),
and the rectangle is *validated* the way a quote is verified: a
box that is missing, malformed, degenerate, or off the picture
marks the field ``supported: False`` — the draft keeps the value
(a reviewer may still use it) but it carries no evidence, exactly
like a hallucinated quote in text.

Everything else is shared discipline (Doc 07 §2): the model's
answer is untrusted until it passes the shape check (one repair
retry naming the fault, then a loud ModelError); dates are never
taken from the model's arithmetic — it returns the flyer's own
date/time phrases and ``ai/dates.py`` resolves them against the
photo's context (the source's creation instant) in the group's
timezone; ambiguity is returned as readings, never guessed away.

The same call also drafts the flyer's **transcript** and **alt
text** (B-065) and reports its **links** (B-066: QR codes the
model decoded, printed addresses it read) — all three ride the
one reading, because they are all "what does this picture say
and what is it".

This module is pure — no database, no storage. The service layer
(services/vision.py) feeds it image bytes and persists what it
returns; usage recording is an optional callback (B-047's
ledger) exactly as in text extraction.
"""

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Callable

from gpa.ai import promptlib
from gpa.ai.client import MetaModelClient, ModelError, ModelResult
from gpa.ai.dates import parse_time_phrase, resolve_date_phrase

PROMPT_NAME = "flyer_reading"
# The fields whose values are dates: their phrases resolve
# through the deterministic resolver, as in text (D-22).
DATE_FIELDS = {"starts", "ends"}

UsageRecorder = Callable[[ModelResult, str], None]


@dataclass
class VisionReading:
    """One flyer's verified reading, ready for the service to
    persist: the candidate draft (extraction-shaped, with region
    provenance in its fields), the transcript, the drafted alt
    text, and the links the poster offers."""

    draft: dict
    transcript: str = ""
    alt_text: str = ""
    links: list[dict] = dataclass_field(default_factory=list)
    field_provenance: list[dict] = dataclass_field(default_factory=list)
    confidence: float = 0.0
    prompt_name: str = PROMPT_NAME
    prompt_version: int = 0


def build_vision_prompt() -> str:
    """The exact prompt the pipeline sends — shared with tests,
    so a fixture recording is keyed by the very text production
    sends (the B-036 discipline)."""
    return promptlib.render_prompt(PROMPT_NAME)


def valid_region(region) -> bool:
    """Is this a rectangle a reviewer could tap? Four numbers,
    on the picture, with real area. Fractions may kiss the edges
    (a hair of float slack) but never leave the image."""
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        return False
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in region):
        return False
    x, y, w, h = (float(v) for v in region)
    return (
        0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and 0.0 < w <= 1.0
        and 0.0 < h <= 1.0
        and x + w <= 1.001
        and y + h <= 1.001
    )


def _shape_error(data: object, schema: dict) -> str | None:
    """Validate a vision answer's shape: {"items": [ {fields,
    transcript?, alt_text?, links?} ]} with every field carrying
    value/confidence/region. Hand-rolled like extraction's — the
    shapes are few, owned here, and the words feed the repair
    retry verbatim."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return "the answer must be an object with an 'items' list"
    allowed_fields = set(
        schema["properties"]["items"]["items"]["properties"]["fields"]["properties"]
    )
    for index, item in enumerate(data["items"]):
        if not isinstance(item, dict) or not isinstance(item.get("fields"), dict):
            return f"item {index} must be an object with a 'fields' object"
        for name, fdef in item["fields"].items():
            if name not in allowed_fields:
                return f"item {index} names unknown field '{name}'"
            if not isinstance(fdef, dict):
                return f"item {index} field '{name}' must be an object"
            for key in ("value", "confidence", "region"):
                if key not in fdef:
                    return f"item {index} field '{name}' is missing '{key}'"
            if not isinstance(fdef["confidence"], (int, float)):
                return f"item {index} field '{name}' confidence must be a number"
        links = item.get("links")
        if links is not None:
            if not isinstance(links, list):
                return f"item {index} 'links' must be a list"
            for link in links:
                if not isinstance(link, dict) or not isinstance(link.get("url"), str):
                    return f"item {index} has a link without a url"
    return None


def _process_item(
    item: dict,
    *,
    group_timezone: str,
    source_created_at: datetime | None,
) -> VisionReading:
    """Verify one raw vision item into a draft.

    Per field: the region is the evidence — valid box, supported;
    anything else, unsupported and region-less in the provenance
    (the claimed box still rides the draft entry, labelled by the
    verdict, so a reviewer sees what the model *thought* it saw).
    Date phrases resolve against the photo's context instant.
    """
    fields_out: dict[str, dict] = {}
    provenance: list[dict] = []
    supported_confidences: list[float] = []
    for name, fdef in item["fields"].items():
        value = fdef.get("value")
        confidence = float(fdef.get("confidence") or 0.0)
        region = fdef.get("region")
        supported = value is not None and valid_region(region)
        entry: dict = {
            "value": value,
            "confidence": confidence,
            # Pictures have no quotes; the key exists so the
            # review queue renders one field shape for every
            # pipeline (B-043), and it is honestly empty.
            "quote": "",
            "region": [float(v) for v in region] if valid_region(region) else region,
            "supported": supported,
        }
        if supported:
            supported_confidences.append(confidence)
        if name in DATE_FIELDS:
            date_phrase = fdef.get("date_phrase")
            time_phrase = fdef.get("time_phrase")
            entry["date_phrase"] = date_phrase
            entry["time_phrase"] = time_phrase
            if date_phrase:
                resolution = resolve_date_phrase(
                    date_phrase, sent_at=source_created_at, group_tz=group_timezone
                )
                entry["date_readings"] = [r.date.isoformat() for r in resolution.readings]
                entry["date_ambiguous"] = resolution.ambiguous
                entry["date_matched"] = resolution.matched
                entry["resolved_date"] = (
                    resolution.readings[0].date.isoformat()
                    if len(resolution.readings) == 1
                    else None
                )
            if time_phrase:
                tres = parse_time_phrase(time_phrase)
                entry["time_readings"] = [r.time.isoformat() for r in tres.readings]
                entry["time_ambiguous"] = tres.ambiguous
                entry["resolved_time"] = (
                    tres.readings[0].time.isoformat() if len(tres.readings) == 1 else None
                )
        fields_out[name] = entry
        provenance.append(
            {
                "field": name,
                "region": entry["region"] if supported else None,
                "supported": supported,
            }
        )
    confidence = (
        sum(supported_confidences) / len(supported_confidences)
        if supported_confidences
        else 0.0
    )
    draft: dict = {"kind": "event", "shared": False, "fields": fields_out}
    # Top-level mirrors, the same convenience shape text drafts
    # carry (the queue reads title/place straight off the draft;
    # minting reads "<field>_date" / "<field>_at").
    for name, entry in fields_out.items():
        if entry["value"] is not None:
            draft[name] = entry["value"]
        if entry.get("resolved_date"):
            draft[f"{name}_date"] = entry["resolved_date"]
            if entry.get("resolved_time"):
                draft[f"{name}_at"] = f"{entry['resolved_date']}T{entry['resolved_time']}"
    # Links (B-066): the addresses the poster itself offers — a
    # decoded QR code or a printed URL, each with its region when
    # the model could bound it. Duplicates collapse by URL; an
    # address is either on the poster or it is not in the list.
    links: list[dict] = []
    seen_urls: set[str] = set()
    for link in item.get("links") or []:
        url = str(link.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        kind = link.get("kind") if link.get("kind") in ("qr", "printed") else "printed"
        entry = {"url": url, "kind": kind}
        if valid_region(link.get("region")):
            entry["region"] = [float(v) for v in link["region"]]
        links.append(entry)
    draft["links"] = links
    return VisionReading(
        draft=draft,
        transcript=str(item.get("transcript") or ""),
        alt_text=str(item.get("alt_text") or ""),
        links=links,
        field_provenance=provenance,
        confidence=confidence,
    )


def read_flyer_image(
    client: MetaModelClient,
    *,
    image_bytes: bytes,
    group_timezone: str,
    source_created_at: datetime | None = None,
    record_usage: UsageRecorder | None = None,
) -> VisionReading:
    """Read one flyer photograph into a verified draft.

    A flyer is ONE poster, so a well-formed answer carries one
    item; an empty items list is a legitimate reading ("nothing
    readable here") and yields an empty draft — the flyer still
    lands on the wall, flagged, never silently dropped (B-070).
    A malformed answer gets exactly one repair retry naming the
    fault; a second failure raises ModelError, as in text.
    """
    template = promptlib.load_prompt(PROMPT_NAME)
    schema = template.schema or {}
    prompt = build_vision_prompt()
    result = client.read_image(prompt=prompt, image_bytes=image_bytes, schema=schema)
    if record_usage is not None:
        record_usage(result, PROMPT_NAME)
    error = _shape_error(result.data, schema)
    if error is not None:
        repair = (
            prompt
            + f"\n\nYour previous answer was not valid: {error}. "
            "Return ONLY valid JSON in the required shape."
        )
        result = client.read_image(prompt=repair, image_bytes=image_bytes, schema=schema)
        if record_usage is not None:
            record_usage(result, PROMPT_NAME)
        error = _shape_error(result.data, schema)
        if error is not None:
            raise ModelError(
                f"Flyer reading produced malformed output twice ({error}); "
                "refusing to draft from it."
            )
    items = result.data["items"]
    if not items:
        reading = VisionReading(draft={"kind": "event", "shared": False, "fields": {}, "links": []})
    else:
        reading = _process_item(
            items[0],
            group_timezone=group_timezone,
            source_created_at=source_created_at,
        )
    reading.prompt_name = PROMPT_NAME
    reading.prompt_version = template.version
    return reading
