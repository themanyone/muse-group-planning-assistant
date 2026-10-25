"""The extraction pipeline (roadmap B-040, B-041, B-042).

One pipeline, parameterised by KIND: events, tasks, decisions,
questions. The shape of the work is always the same (Doc 07 §3):

    chunks -> windows -> prompt -> model (complete_json)
           -> schema shape check (one repair retry, then fail
              loudly) -> per-field quote verification
           -> deterministic date resolution (D-22)
           -> coverage check -> candidate drafts

The verification is the product's spine: **every field must show
its work.** A field's quote is searched (whitespace-normalised)
in the chunks; a quote that appears nowhere marks the field
``supported: False`` and leaves it without provenance, so a
hallucinated quote is *caught*, not published. Dates are never
taken from the model's arithmetic: the model returns the raw
phrase, ``ai/dates.py`` resolves it against the provenance
chunk's sent-at in the group's timezone, and ambiguous readings
are kept as readings for a human to choose between.

This module is pure — no database session, no storage. The
service layer (B-045) feeds it chunks and persists what it
returns; usage recording is an optional callback so every model
call can be ledgered (B-047) without the pipeline knowing about
rows.
"""

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from gpa.ai import promptlib
from gpa.ai.client import MetaModelClient, ModelError, ModelResult
from gpa.ai.dates import parse_time_phrase, resolve_date_phrase

if TYPE_CHECKING:  # annotations only — no import cycle at runtime
    from gpa.models.sources import SourceChunk

# A window is bounded both by characters (model context) and by
# chunk count (a window of forty one-line messages is plenty).
MAX_WINDOW_CHARS = 12_000
MAX_WINDOW_CHUNKS = 40

# Which prompt + which fields are dates, per kind. The pipeline is
# one; the kinds differ only in this table (Doc 07, worked example
# "one pipeline, three more schemas").
KIND_CONFIGS: dict[str, dict] = {
    "event": {"prompt": "extraction", "date_fields": {"starts", "ends"}},
    "task": {"prompt": "extraction_tasks", "date_fields": {"due"}},
    "decision": {"prompt": "extraction_decisions", "date_fields": set()},
    "question": {"prompt": "extraction_questions", "date_fields": set()},
    # Phase 6 (B-077): words about an EXISTING event — a move, a
    # new place, a cancellation — draft as a change candidate
    # against that event, never as a new event. The service
    # links the candidate to its target (services/extraction);
    # approval applies it (services/merging).
    "event_change": {
        "prompt": "extraction_changes", "date_fields": {"starts", "ends"},
    },
}

# Called after every model call so the service can write the
# usage ledger row inside its own transaction (B-047).
UsageRecorder = Callable[[ModelResult, str], None]


@dataclass
class ExtractedCandidate:
    """One verified draft, ready for the service to persist."""

    kind: str
    draft: dict
    confidence: float
    field_provenance: list[dict] = dataclass_field(default_factory=list)
    flags: list[str] = dataclass_field(default_factory=list)
    prompt_name: str = ""
    prompt_version: int = 0


def _normalise(text: str) -> str:
    """Whitespace-normalised form for quote matching: a quote
    split across a line wrap in the source still matches."""
    return " ".join(text.split())


def chunk_windows(
    chunks: list["SourceChunk"],
    *,
    max_chars: int = MAX_WINDOW_CHARS,
    max_chunks: int = MAX_WINDOW_CHUNKS,
) -> list[list["SourceChunk"]]:
    """Split ordered chunks into model-sized windows.

    Boundaries fall between chunks, never inside one — a chunk is
    the atomic unit of provenance, and a quote can only be
    verified against whole chunks.
    """
    windows: list[list["SourceChunk"]] = []
    current: list["SourceChunk"] = []
    size = 0
    for chunk in chunks:
        length = len(chunk.text)
        if current and (size + length > max_chars or len(current) >= max_chunks):
            windows.append(current)
            current, size = [], 0
        current.append(chunk)
        size += length
    if current:
        windows.append(current)
    return windows


def render_chunks(chunks: list["SourceChunk"]) -> str:
    """The exact chunk block placed into a prompt — shared by the
    pipeline and by tests, so a fixture recording is keyed by the
    very text production sends."""
    blocks = []
    for chunk in chunks:
        sender = chunk.sender or "unknown sender"
        stamp = chunk.sent_at.isoformat() if chunk.sent_at else "no timestamp"
        blocks.append(f"[chunk {chunk.position} · {sender} · {stamp}]\n{chunk.text}")
    return "\n\n".join(blocks)


def build_prompt(prompt_name: str, chunks: list["SourceChunk"], group_timezone: str) -> str:
    """Render the full prompt for one window of chunks."""
    return promptlib.render_prompt(
        prompt_name, CHUNKS=render_chunks(chunks), GROUP_TIMEZONE=group_timezone
    )


def _shape_error(data: object, schema: dict) -> str | None:
    """Validate model output against the practical shape contract:
    {"items": [{"fields": {name: {value, confidence, quote}}}]}.

    Deliberately hand-rolled (no JSON-Schema engine dependency):
    the shapes are few, owned by this codebase, and the error
    words feed the repair retry verbatim.
    """
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
            for key in ("value", "confidence", "quote"):
                if key not in fdef:
                    return f"item {index} field '{name}' is missing '{key}'"
            if not isinstance(fdef["confidence"], (int, float)):
                return f"item {index} field '{name}' confidence must be a number"
    return None


def _find_quote(quote: str, chunks: list["SourceChunk"]) -> "SourceChunk | None":
    """The chunk a quote provably came from, or None.

    An empty quote proves nothing and matches nothing.
    """
    needle = _normalise(quote)
    if not needle:
        return None
    for chunk in chunks:
        if needle in _normalise(chunk.text):
            return chunk
    return None


def _process_item(
    item: dict,
    *,
    kind: str,
    config: dict,
    chunks: list["SourceChunk"],
    group_timezone: str,
    source_created_at: datetime | None,
) -> ExtractedCandidate:
    """Verify one raw model item into a candidate draft.

    Per field: quote verification, then (for date fields)
    deterministic resolution against the provenance chunk's
    sent-at. The draft's "fields" map is the review queue's whole
    world: value, confidence, quote, supported, and — for dates —
    the raw phrases plus every reading the resolver returned.
    """
    fields_out: dict[str, dict] = {}
    provenance: list[dict] = []
    supported_confidences: list[float] = []
    for name, fdef in item["fields"].items():
        value = fdef.get("value")
        confidence = float(fdef.get("confidence") or 0.0)
        quote = fdef.get("quote") or ""
        match = _find_quote(quote, chunks) if value is not None else None
        supported = match is not None
        entry: dict = {
            "value": value,
            "confidence": confidence,
            "quote": quote,
            "supported": supported,
        }
        if match is not None:
            entry["chunk_position"] = match.position
            entry["sent_at"] = match.sent_at.isoformat() if match.sent_at else None
            supported_confidences.append(confidence)
        if name in config["date_fields"]:
            date_phrase = fdef.get("date_phrase")
            time_phrase = fdef.get("time_phrase")
            entry["date_phrase"] = date_phrase
            entry["time_phrase"] = time_phrase
            anchor_at = (match.sent_at if match is not None else None) or source_created_at
            if date_phrase:
                resolution = resolve_date_phrase(
                    date_phrase, sent_at=anchor_at, group_tz=group_timezone
                )
                entry["date_readings"] = [
                    r.date.isoformat() for r in resolution.readings
                ]
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
                "chunk_id": match.id if match is not None else None,
                "chunk_position": match.position if match is not None else None,
                "quote": quote,
                "supported": supported,
                "sent_at": (
                    match.sent_at.isoformat()
                    if match is not None and match.sent_at
                    else None
                ),
            }
        )
    confidence = (
        sum(supported_confidences) / len(supported_confidences)
        if supported_confidences
        else 0.0
    )
    draft: dict = {"kind": kind, "shared": bool(item.get("shared")), "fields": fields_out}
    # Top-level mirrors (the Candidate model's documented draft
    # shape): the frontend reads title/place/text straight off the
    # draft, and a resolved date lands as "<field>_date" (plus
    # "<field>_at" when a single time resolved too). The "fields"
    # map stays the full evidence; mirrors are conveniences only.
    for name, entry in fields_out.items():
        if entry["value"] is not None:
            draft[name] = entry["value"]
        if entry.get("resolved_date"):
            draft[f"{name}_date"] = entry["resolved_date"]
            if entry.get("resolved_time"):
                draft[f"{name}_at"] = f"{entry['resolved_date']}T{entry['resolved_time']}"
    return ExtractedCandidate(
        kind=kind, draft=draft, confidence=confidence, field_provenance=provenance
    )


def _apply_coverage_flags(candidates: list[ExtractedCandidate]) -> None:
    """B-041's coverage check: a chunk claimed by more than one
    candidate, with none of them flagged shared, is suspicious —
    likely one event split in two by the model. Flag every
    claimant; a reviewer merges (B-043) if they are one thing."""
    claims: dict[int, list[int]] = {}
    for index, candidate in enumerate(candidates):
        positions = {
            row["chunk_position"]
            for row in candidate.field_provenance
            if row["supported"] and row["chunk_position"] is not None
        }
        for position in positions:
            claims.setdefault(position, []).append(index)
    for _position, claimants in claims.items():
        if len(claimants) > 1 and not any(
            candidates[i].draft.get("shared") for i in claimants
        ):
            for i in claimants:
                if "shared_chunk_unflagged" not in candidates[i].flags:
                    candidates[i].flags.append("shared_chunk_unflagged")


def extract_kind(
    client: MetaModelClient,
    *,
    kind: str,
    chunks: list["SourceChunk"],
    group_timezone: str,
    source_created_at: datetime | None = None,
    record_usage: UsageRecorder | None = None,
) -> list[ExtractedCandidate]:
    """Run one kind's extraction over a source's chunks.

    Long sources are windowed (B-040); candidates from all windows
    are coverage-checked together (B-041). A malformed model
    answer gets exactly one repair retry naming the fault; a
    second failure raises ModelError — extraction jobs fail
    visibly, never silently (B-045 builds on this).
    """
    config = KIND_CONFIGS[kind]
    template = promptlib.load_prompt(config["prompt"])
    schema = template.schema or {}
    candidates: list[ExtractedCandidate] = []
    for window in chunk_windows(chunks):
        prompt = build_prompt(config["prompt"], window, group_timezone)
        result = client.complete_json(prompt=prompt, schema=schema, tier="wonder")
        if record_usage is not None:
            record_usage(result, config["prompt"])
        error = _shape_error(result.data, schema)
        if error is not None:
            repair = (
                prompt
                + f"\n\nYour previous answer was not valid: {error}. "
                "Return ONLY valid JSON in the required shape."
            )
            result = client.complete_json(prompt=repair, schema=schema, tier="wonder")
            if record_usage is not None:
                record_usage(result, config["prompt"])
            error = _shape_error(result.data, schema)
            if error is not None:
                raise ModelError(
                    f"Extraction for kind '{kind}' produced malformed output "
                    f"twice ({error}); refusing to draft from it."
                )
        for item in result.data["items"]:
            candidates.append(
                _process_item(
                    item,
                    kind=kind,
                    config=config,
                    chunks=window,
                    group_timezone=group_timezone,
                    source_created_at=source_created_at,
                )
            )
    _apply_coverage_flags(candidates)
    for candidate in candidates:
        candidate.prompt_name = config["prompt"]
        candidate.prompt_version = template.version
    return candidates
