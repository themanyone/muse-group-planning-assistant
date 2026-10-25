"""The model client protocol (roadmap B-035, decision D-18).

What this file is for: the ONE interface between this product and
any language/vision model. Pipelines call four methods —
``complete_text``, ``complete_json``, ``read_image``, ``transcribe``
— each taking a model *tier* (``spark`` for volume work, ``wonder``
for hard reasoning) and returning a ``ModelResult``: the output,
the model id that produced it, and the usage it cost. Behind the
protocol sit the drivers (mock / fixture / live); which one is
active is a *configuration* choice (``GPA_AI_DRIVER``), so swapping
drivers is a config change in tests and deployments alike — never
a code change.

No vendor SDK exists behind this door either: the live driver
speaks plain HTTP via httpx. Domain code imports this module and
the prompt library — nothing else AI-shaped.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# The two tiers every method accepts (roadmap B-035). Spark does
# the volume work; wonder is spent where tangles justify it.
TIER_SPARK = "spark"
TIER_WONDER = "wonder"
TIERS: tuple[str, ...] = (TIER_SPARK, TIER_WONDER)


class ModelError(Exception):
    """A model call failed in a way the caller must see.

    Drivers raise this (never a vendor exception): unreachable API,
    a non-retryable status, an unparseable JSON body. The pipeline
    treats it as "this call produced nothing trustworthy" — one
    repair retry, then the job fails visibly (Doc 07 §2).
    """


class FixtureMissError(ModelError):
    """The fixture driver has no recording for this prompt hash.

    A miss is a test-setup fact, stated loudly with the hash, so a
    recording can be made — the fixture driver never invents a
    plausible-looking answer to fill the gap.
    """


@dataclass
class ModelResult:
    """One model call's outcome: output + who made it + its cost.

    ``data`` holds the parsed JSON for complete_json/read_image
    calls; ``text`` always holds the raw text the model returned.
    Token counts and ``cost_micros`` (micro-units of currency, so
    the ledger stays integer-exact) feed the AiUsage rows (B-037).
    """

    text: str
    model_id: str
    tier: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    data: Any = None
    raw: dict | None = field(default=None, repr=False)


@runtime_checkable
class MetaModelClient(Protocol):
    """The protocol every driver implements (roadmap B-035)."""

    def complete_text(self, *, prompt: str, tier: str = TIER_SPARK) -> ModelResult:
        """Free-text completion: wording, summaries, answers."""
        ...

    def complete_json(
        self, *, prompt: str, schema: dict, tier: str = TIER_SPARK
    ) -> ModelResult:
        """Structured completion: ``result.data`` is the parsed
        JSON, expected to follow ``schema`` (the pipeline still
        validates — model output is untrusted input, Doc 07 §2)."""
        ...

    def read_image(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        schema: dict | None = None,
        tier: str = TIER_WONDER,
    ) -> ModelResult:
        """Vision reading: a flyer/board image plus instructions."""
        ...

    def transcribe(
        self, *, audio_bytes: bytes, mime: str = "audio/wav", tier: str = TIER_SPARK
    ) -> ModelResult:
        """Speech to text: ``result.text`` is the transcript."""
        ...

    def generate_image(self, *, prompt: str, tier: str = TIER_WONDER) -> ModelResult:
        """Image generation (B-067): ``result.data`` is
        ``{"image_bytes": <bytes>, "format": "png"}``. A generated
        image is labelled generated at every layer above this one
        — the adapter only owes the bytes and the honesty."""
        ...


def estimate_tokens(text: str) -> int:
    """A rough token estimate (≈4 chars/token), used where a driver
    cannot report real usage. An estimate is labelled as such by
    the drivers that use it — the ledger's exact rows come from the
    live API's own usage counts."""
    return max(1, len(text) // 4)


def cost_micros_for(
    *, tier: str, input_tokens: int, output_tokens: int,
    price_spark_per_1k: int, price_wonder_per_1k: int,
) -> int:
    """Integer-exact cost of one call at the configured prices."""
    price = price_wonder_per_1k if tier == TIER_WONDER else price_spark_per_1k
    return (input_tokens + output_tokens) * price // 1000


# --- The registry: how processes share one configured client ------
#
# The API process and the worker both need the driver the settings
# name, and job handlers receive only (db, payload) — so the client
# lives here, built once per process from settings. ``create_app``
# and the worker entry point both call ``set_client`` at startup;
# tests may set their own (e.g. a fixture driver with recordings).

_client: MetaModelClient | None = None


def set_client(client: MetaModelClient | None) -> None:
    """Install the process-wide client (or clear it with None)."""
    global _client
    _client = client


def get_client() -> MetaModelClient:
    """The process-wide client, building the configured default
    (from environment settings) on first use."""
    global _client
    if _client is None:
        from gpa.config import get_settings

        _client = build_client(get_settings())
    return _client


def build_client(settings) -> MetaModelClient:
    """Build the driver ``settings.ai_driver`` names (B-035/B-036).

    This factory is the whole "swapping drivers is a config change"
    acceptance: every caller goes through it or through the
    registry it feeds.
    """
    driver = (settings.ai_driver or "mock").lower()
    if driver == "mock":
        from gpa.ai.drivers_mock import MockDriver

        return MockDriver(
            model_spark=settings.ai_model_spark, model_wonder=settings.ai_model_wonder
        )
    if driver == "fixture":
        from pathlib import Path

        from gpa.ai.drivers_fixture import FixtureDriver

        directory = Path(settings.ai_fixtures_dir) if settings.ai_fixtures_dir else None
        return FixtureDriver(
            directory=directory,
            model_spark=settings.ai_model_spark, model_wonder=settings.ai_model_wonder,
        )
    if driver == "live":
        from gpa.ai.drivers_live import LiveDriver

        return LiveDriver(settings)
    raise ModelError(
        f"Unknown GPA_AI_DRIVER '{settings.ai_driver}' — expected mock, fixture, or live."
    )
