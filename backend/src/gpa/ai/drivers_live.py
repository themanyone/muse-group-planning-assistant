"""The live driver (roadmap B-037).

The Meta Model API over plain httpx — no vendor SDK. What this
driver owes the rest of the system:

- **Secrets from the environment only.** The API key arrives via
  ``GPA_AI_API_KEY`` (Settings) and is sent as a Bearer header; it
  is never logged, never stored, never in a fixture.
- **Retries with jitter on 429/5xx only.** A rate-limit or a
  server error earns another attempt (exponential backoff plus
  random jitter, so a fleet of workers does not retry in
  lockstep). Any other failure — a 4xx, a malformed body — fails
  at once: retrying it would only pay twice for the same answer.
- **A succeeded call is never repeated.** The retry loop only
  surrounds the request itself; once a 2xx body is in hand the
  result is returned, and parsing happens outside the loop. A paid
  call that succeeded is therefore never duplicated.
- **Usage + cost per call.** The API's own token counts are read
  from the response and priced at the configured per-tier rates
  into integer micro-units, ready for the AiUsage ledger (the
  pipeline writes the row; this driver does the arithmetic).

The wire shape is the API's chat-completions shape, isolated in
``_chat`` so an endpoint change touches one function. Tests drive
this driver through a stubbed httpx transport — the real network
is never touched by the suite.
"""

import base64
import json
import random
import time
from typing import Callable

import httpx

from gpa.ai.client import ModelError, ModelResult, cost_micros_for, estimate_tokens

# Statuses worth another attempt: rate-limited, or the server's
# own trouble. Everything else is an answer, not an accident.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class LiveDriver:
    """A MetaModelClient speaking HTTP to the Meta Model API."""

    driver_name = "live"

    def __init__(
        self,
        settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        if not settings.ai_api_base:
            raise ModelError(
                "The live AI driver needs GPA_AI_API_BASE set (the Meta Model API base URL)."
            )
        if not settings.ai_api_key:
            raise ModelError(
                "The live AI driver needs GPA_AI_API_KEY set — secrets come from the environment only."
            )
        self._base = settings.ai_api_base.rstrip("/")
        self._api_key = settings.ai_api_key
        self._models = {"spark": settings.ai_model_spark, "wonder": settings.ai_model_wonder}
        self._timeout = settings.ai_live_timeout_seconds
        self._max_attempts = max(1, settings.ai_live_max_attempts)
        self._price_spark = settings.ai_price_spark_micros_per_1k
        self._price_wonder = settings.ai_price_wonder_micros_per_1k
        self._transport = transport
        self._sleep = sleep
        self._jitter = jitter

    # --- The one wire function -------------------------------------

    def _chat(self, *, tier: str, messages: list[dict]) -> tuple[str, int, int, str]:
        """One chat-completions call, with the retry discipline.

        Returns (content_text, input_tokens, output_tokens,
        model_id). Raises ModelError when the call has honestly
        failed: attempts exhausted on retryable trouble, or any
        non-retryable answer.
        """
        model_id = self._models.get(tier, self._models["spark"])
        payload = {"model": model_id, "messages": messages, "temperature": 0}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_error = "no attempt made"
        with httpx.Client(
            transport=self._transport, timeout=self._timeout
        ) as client:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = client.post(
                        f"{self._base}/chat/completions", json=payload, headers=headers
                    )
                except httpx.HTTPError as exc:
                    # A transport failure produced no answer at
                    # all — retrying cannot duplicate a success.
                    last_error = f"transport error: {exc}"
                    self._backoff(attempt)
                    continue
                if response.status_code in _RETRYABLE_STATUSES:
                    last_error = f"HTTP {response.status_code}"
                    self._backoff(attempt)
                    continue
                if response.status_code >= 400:
                    raise ModelError(
                        f"Meta Model API refused the call: HTTP {response.status_code}."
                    )
                # A 2xx: the call succeeded. Parse OUTSIDE any
                # retry path — this result is returned, never
                # re-requested.
                try:
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    usage = body.get("usage") or {}
                    return (
                        content,
                        int(usage.get("prompt_tokens", 0)),
                        int(usage.get("completion_tokens", 0)),
                        str(body.get("model") or model_id),
                    )
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise ModelError(
                        f"Meta Model API answered in an unexpected shape: {exc}."
                    ) from exc
        raise ModelError(
            f"Meta Model API call failed after {self._max_attempts} attempts ({last_error})."
        )

    def _backoff(self, attempt: int) -> None:
        """Exponential backoff + jitter between attempts (and no
        sleep after the final attempt — the failure is the news)."""
        if attempt >= self._max_attempts:
            return
        delay = min(2.0 ** attempt, 8.0) + self._jitter()
        self._sleep(delay)

    def _result(self, *, tier: str, prompt: str, content: str, in_tok: int, out_tok: int, model_id: str) -> ModelResult:
        return ModelResult(
            text=content,
            model_id=model_id,
            tier=tier,
            input_tokens=in_tok or estimate_tokens(prompt),
            output_tokens=out_tok or estimate_tokens(content),
            cost_micros=cost_micros_for(
                tier=tier, input_tokens=in_tok, output_tokens=out_tok,
                price_spark_per_1k=self._price_spark,
                price_wonder_per_1k=self._price_wonder,
            ),
        )

    # --- The protocol ------------------------------------------------

    def complete_text(self, *, prompt: str, tier: str = "spark") -> ModelResult:
        content, in_tok, out_tok, model_id = self._chat(
            tier=tier, messages=[{"role": "user", "content": prompt}]
        )
        return self._result(
            tier=tier, prompt=prompt, content=content,
            in_tok=in_tok, out_tok=out_tok, model_id=model_id,
        )

    def complete_json(self, *, prompt: str, schema: dict, tier: str = "spark") -> ModelResult:
        # The schema travels in the instruction; the body's JSON is
        # parsed here so the pipeline receives data, not hope. A
        # body that is not JSON is a failed call (ModelError), and
        # the pipeline's one repair retry is the remedy (Doc 07 §2).
        instruction = (
            f"{prompt}\n\nRespond with JSON only, following this JSON Schema:\n"
            f"{json.dumps(schema)}"
        )
        content, in_tok, out_tok, model_id = self._chat(
            tier=tier, messages=[{"role": "user", "content": instruction}]
        )
        result = self._result(
            tier=tier, prompt=prompt, content=content,
            in_tok=in_tok, out_tok=out_tok, model_id=model_id,
        )
        result.data = _parse_json_content(content)
        return result

    def read_image(
        self, *, prompt: str, image_bytes: bytes, schema: dict | None = None,
        tier: str = "wonder",
    ) -> ModelResult:
        data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
        content_parts: list[dict] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
        content, in_tok, out_tok, model_id = self._chat(
            tier=tier, messages=[{"role": "user", "content": content_parts}]
        )
        result = self._result(
            tier=tier, prompt=prompt, content=content,
            in_tok=in_tok, out_tok=out_tok, model_id=model_id,
        )
        if schema is not None:
            result.data = _parse_json_content(content)
        return result

    def transcribe(self, *, audio_bytes: bytes, mime: str = "audio/wav", tier: str = "spark") -> ModelResult:
        model_id = self._models.get(tier, self._models["spark"])
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_error = "no attempt made"
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = client.post(
                        f"{self._base}/audio/transcriptions",
                        headers=headers,
                        data={"model": model_id},
                        files={"file": ("audio", audio_bytes, mime)},
                    )
                except httpx.HTTPError as exc:
                    last_error = f"transport error: {exc}"
                    self._backoff(attempt)
                    continue
                if response.status_code in _RETRYABLE_STATUSES:
                    last_error = f"HTTP {response.status_code}"
                    self._backoff(attempt)
                    continue
                if response.status_code >= 400:
                    raise ModelError(
                        f"Meta Model API refused transcription: HTTP {response.status_code}."
                    )
                try:
                    text = str(response.json()["text"])
                except (ValueError, KeyError, TypeError) as exc:
                    raise ModelError(
                        f"Transcription answered in an unexpected shape: {exc}."
                    ) from exc
                return ModelResult(
                    text=text, model_id=model_id, tier=tier,
                    input_tokens=0, output_tokens=estimate_tokens(text),
                    cost_micros=0,
                )
        raise ModelError(
            f"Transcription failed after {self._max_attempts} attempts ({last_error})."
        )

    def generate_image(self, *, prompt: str, tier: str = "wonder") -> ModelResult:
        # Honest boundary (B-067): no image-generation endpoint is
        # wired for the live driver in this codebase, and inventing
        # a wire shape for one would be a fabrication. The method
        # exists so the protocol is whole; it refuses in plain
        # words until a real endpoint is configured and this body
        # is written against it. Mock and fixture drivers carry
        # the feature in tests and the offline demo.
        raise ModelError(
            "Image generation is not wired to a live endpoint yet — "
            "no GPA setting names one. Use the mock or fixture driver, "
            "or configure the endpoint when the Meta image API is chosen."
        )


def _parse_json_content(content: str):
    """Parse a model body's JSON, tolerating code fences only.

    Models love wrapping JSON in ``` fences; strip exactly that
    and nothing more — anything else unparseable is a failed call,
    not a puzzle to solve generously (Doc 07 §2: untrusted input).
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ModelError(f"Model did not return valid JSON: {exc}.") from exc
