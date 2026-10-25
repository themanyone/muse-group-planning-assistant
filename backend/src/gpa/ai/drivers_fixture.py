"""The fixture driver (roadmap B-036).

Recorded responses, keyed by *prompt hash*: for a given rendered
prompt (plus, for structured calls, the canonical schema and the
media hash), the driver replays exactly what was recorded — the
golden test suite and the offline demo therefore exercise the real
pipeline against known model behaviour, with no network and no
API key.

Recordings come from two places:

- a directory of ``<key>.json`` files, each holding
  ``{"text": ..., "data": ..., "usage": {...}}`` (any part may be
  absent — ``data`` for JSON calls, ``text`` for the rest);
- ``add_*`` calls in code (tests record what a case needs, using
  the same prompt the pipeline renders — see ai/extraction.py's
  ``build_prompt``, shared by both, so the hash cannot drift).

A lookup with no recording raises ``FixtureMissError`` naming the
key. The driver never falls back to inventing an answer: a silent
invention would turn a missing recording into a false pass.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from gpa.ai.client import FixtureMissError, ModelResult, estimate_tokens


def _canonical(value: Any) -> str:
    """Stable JSON text for hashing: sorted keys, tight separators."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_key(prompt: str, *, schema: dict | None = None, media: bytes | None = None) -> str:
    """The recording key for one call: hash of prompt (+ schema /
    media when the call carries them)."""
    digest = hashlib.sha256()
    digest.update(prompt.encode("utf-8"))
    if schema is not None:
        digest.update(b"\n--schema--\n")
        digest.update(_canonical(schema).encode("utf-8"))
    if media is not None:
        digest.update(b"\n--media--\n")
        digest.update(hashlib.sha256(media).hexdigest().encode("ascii"))
    return digest.hexdigest()


class FixtureDriver:
    """A MetaModelClient that replays recorded responses."""

    driver_name = "fixture"

    def __init__(
        self,
        *,
        directory: Path | None = None,
        model_spark: str = "muse-spark",
        model_wonder: str = "muse-wonder",
    ):
        self._models = {"spark": model_spark, "wonder": model_wonder}
        self._recordings: dict[str, dict] = {}
        directory = directory or (Path(__file__).parent / "fixtures")
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                key = payload.get("key") or path.stem
                self._recordings[key] = payload

    # --- Recording (tests and the recording workflow) ------------

    def add_json(self, prompt: str, schema: dict, data: Any, *, usage: dict | None = None) -> str:
        """Record a structured response for one prompt+schema."""
        key = prompt_key(prompt, schema=schema)
        self._recordings[key] = {"data": data, "usage": usage or {}}
        return key

    def add_text(self, prompt: str, text: str, *, usage: dict | None = None) -> str:
        """Record a free-text response for one prompt."""
        key = prompt_key(prompt)
        self._recordings[key] = {"text": text, "usage": usage or {}}
        return key

    def add_image(
        self, prompt: str, image_bytes: bytes, data: Any, *,
        schema: dict | None = None, usage: dict | None = None,
    ) -> str:
        """Record a vision response for one prompt+image."""
        key = prompt_key(prompt, schema=schema, media=image_bytes)
        self._recordings[key] = {"data": data, "usage": usage or {}}
        return key

    def add_transcript(self, audio_bytes: bytes, text: str, *, usage: dict | None = None) -> str:
        """Record a transcript for one audio payload."""
        key = prompt_key("", media=audio_bytes)
        self._recordings[key] = {"text": text, "usage": usage or {}}
        return key

    def add_image_generation(self, prompt: str, image_bytes: bytes, *, usage: dict | None = None) -> str:
        """Record a generated image for one prompt (B-067). The
        bytes ride base64 in the recording so a fixture file can
        carry them too."""
        import base64

        key = prompt_key(prompt)
        self._recordings[key] = {
            "image_b64": base64.b64encode(image_bytes).decode("ascii"),
            "usage": usage or {},
        }
        return key

    # --- The protocol ---------------------------------------------

    def _replay(self, key: str, *, prompt: str, tier: str, want_data: bool) -> ModelResult:
        payload = self._recordings.get(key)
        if payload is None:
            raise FixtureMissError(
                f"No fixture recording for prompt hash {key}. "
                "Record one (FixtureDriver.add_*) or choose another driver."
            )
        usage = payload.get("usage") or {}
        data = payload.get("data")
        text = payload.get("text")
        if text is None and data is not None:
            text = json.dumps(data)
        return ModelResult(
            text=text or "",
            data=data if want_data else None,
            model_id=f"{self._models.get(tier, self._models['spark'])} (fixture)",
            tier=tier,
            input_tokens=int(usage.get("input_tokens", estimate_tokens(prompt))),
            output_tokens=int(usage.get("output_tokens", estimate_tokens(text or ""))),
            cost_micros=int(usage.get("cost_micros", 0)),
        )

    def complete_text(self, *, prompt: str, tier: str = "spark") -> ModelResult:
        return self._replay(prompt_key(prompt), prompt=prompt, tier=tier, want_data=False)

    def complete_json(self, *, prompt: str, schema: dict, tier: str = "spark") -> ModelResult:
        return self._replay(
            prompt_key(prompt, schema=schema), prompt=prompt, tier=tier, want_data=True
        )

    def read_image(
        self, *, prompt: str, image_bytes: bytes, schema: dict | None = None,
        tier: str = "wonder",
    ) -> ModelResult:
        return self._replay(
            prompt_key(prompt, schema=schema, media=image_bytes),
            prompt=prompt, tier=tier, want_data=True,
        )

    def transcribe(self, *, audio_bytes: bytes, mime: str = "audio/wav", tier: str = "spark") -> ModelResult:
        return self._replay(
            prompt_key("", media=audio_bytes), prompt="", tier=tier, want_data=False
        )

    def generate_image(self, *, prompt: str, tier: str = "wonder") -> ModelResult:
        import base64

        key = prompt_key(prompt)
        payload = self._recordings.get(key)
        if payload is None or "image_b64" not in payload:
            raise FixtureMissError(
                f"No fixture recording for generated-image prompt hash {key}. "
                "Record one (FixtureDriver.add_image_generation) or choose another driver."
            )
        usage = payload.get("usage") or {}
        return ModelResult(
            text="",
            data={
                "image_bytes": base64.b64decode(payload["image_b64"]),
                "format": "png",
            },
            model_id=f"{self._models.get(tier, self._models['spark'])} (fixture)",
            tier=tier,
            input_tokens=int(usage.get("input_tokens", estimate_tokens(prompt))),
            output_tokens=int(usage.get("output_tokens", 0)),
            cost_micros=int(usage.get("cost_micros", 0)),
        )
