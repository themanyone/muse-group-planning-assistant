"""The local-inference driver (roadmap B-119).

Doc 07 §7.5's other half: a group may run its PRUNING on a
model it hosts itself — an OpenAI-compatible chat-completions
endpoint on its own hardware (the wire shape Ollama, llama.cpp
servers, vLLM, and their kin all speak) — while extraction
keeps running on the hosted Meta models. The seam is the one
the whole adapter was built around (B-035): a driver is a
config choice, and per-group it is the ``prune_driver``
setting (B-113/B-114). Nothing downstream changes: the
pruning engine cannot tell which driver judged its chunks,
and the swap test (``tests/test_local_driver.py``) proves the
kept set is the driver's answers, not the driver's identity.

Honest limits, in plain words:

* TEXT ONLY. This driver completes text and JSON. It does
  not read images, transcribe audio, or generate images —
  those calls raise :class:`ModelError` saying so, because a
  local endpoint's vision/audio surface varies too much to
  pretend one shape fits, and a loud refusal beats a quiet
  wrong answer. Vision work stays on the hosted driver.
* ONE MODEL. A local setup names a single model id
  (``GPA_AI_LOCAL_MODEL``) used for every tier; the tier is
  still recorded on every result, so the ledger stays honest
  about which KIND of call was made.
* NO METERED COST. The group runs its own hardware, so
  ``cost_micros`` is 0 — the ledger records the call, its
  tokens (the endpoint's own counts when it reports them,
  estimates when it does not), and a zero price, never an
  invented one.
* ONE ATTEMPT. A local endpoint is near and owned; a failure
  is a fact to surface, not weather to wait out. The
  pipeline's own repair retry handles malformed answers.
"""

from __future__ import annotations

import json

import httpx

from gpa.ai.client import ModelError, ModelResult, estimate_tokens

_TEXT_ONLY = (
    "The local AI driver answers text only — it cannot {verb}. "
    "Vision and audio work stays on the hosted driver; set the "
    "group's pruning driver back to 'hosted' for that, or run "
    "this call through the process driver."
)


class LocalDriver:
    """A MetaModelClient speaking to an OpenAI-compatible
    local endpoint (``POST {base}/chat/completions``)."""

    driver_name = "local"

    def __init__(self, settings, *, transport: httpx.BaseTransport | None = None):
        if not settings.ai_local_base:
            raise ModelError(
                "The local AI driver needs GPA_AI_LOCAL_BASE set "
                "(the base URL of an OpenAI-compatible endpoint, "
                "for example http://localhost:11434/v1)."
            )
        if not settings.ai_local_model:
            raise ModelError(
                "The local AI driver needs GPA_AI_LOCAL_MODEL set "
                "(the model id the local endpoint serves)."
            )
        self._base = settings.ai_local_base.rstrip("/")
        self._model = settings.ai_local_model
        self._api_key = settings.ai_local_api_key
        self._timeout = settings.ai_local_timeout_seconds
        self._transport = transport

    # --- The one wire function ------------------------------------

    def _chat(self, *, prompt: str) -> tuple[str, int, int]:
        """One chat-completions call. Returns (content,
        input_tokens, output_tokens) — token counts are the
        endpoint's own when it reports usage, else 0 and the
        caller estimates."""
        headers = {}
        if self._api_key:
            # The header's value comes from the environment
            # only (the settings object) — never logged, never
            # stored. Many local servers want no key at all;
            # the header is simply absent then.
            headers["Authorization"] = "Bearer " + self._api_key
        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = client.post(
                    f"{self._base}/chat/completions",
                    headers=headers,
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
        except httpx.HTTPError as exc:
            raise ModelError(
                f"The local AI endpoint could not be reached: {exc}. "
                "Is the local model server running?"
            ) from exc
        if response.status_code >= 400:
            raise ModelError(
                f"The local AI endpoint refused the call: "
                f"HTTP {response.status_code}."
            )
        try:
            payload = response.json()
            content = str(payload["choices"][0]["message"]["content"])
            usage = payload.get("usage") or {}
            in_tok = int(usage.get("prompt_tokens") or 0)
            out_tok = int(usage.get("completion_tokens") or 0)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                f"The local AI endpoint answered in an unexpected shape: {exc}."
            ) from exc
        return content, in_tok, out_tok

    def _result(self, *, tier: str, prompt: str, content: str,
                in_tok: int, out_tok: int) -> ModelResult:
        return ModelResult(
            text=content,
            model_id=self._model,
            tier=tier,
            input_tokens=in_tok or estimate_tokens(prompt),
            output_tokens=out_tok or estimate_tokens(content),
            cost_micros=0,  # the group's own hardware — no meter
        )

    # --- The protocol ------------------------------------------------

    def complete_text(self, *, prompt: str, tier: str = "spark") -> ModelResult:
        content, in_tok, out_tok = self._chat(prompt=prompt)
        return self._result(
            tier=tier, prompt=prompt, content=content,
            in_tok=in_tok, out_tok=out_tok,
        )

    def complete_json(
        self, *, prompt: str, schema: dict, tier: str = "spark"
    ) -> ModelResult:
        # Same contract as the hosted driver: the schema
        # travels in the instruction, the body's JSON is parsed
        # here, and a non-JSON body is a failed call — the
        # pipeline's repair retry is the remedy.
        instruction = (
            f"{prompt}\n\nRespond with JSON only, following this JSON Schema:\n"
            f"{json.dumps(schema)}"
        )
        content, in_tok, out_tok = self._chat(prompt=instruction)
        result = self._result(
            tier=tier, prompt=prompt, content=content,
            in_tok=in_tok, out_tok=out_tok,
        )
        result.data = _parse_json_content(content)
        return result

    def read_image(self, **_kwargs) -> ModelResult:
        raise ModelError(_TEXT_ONLY.format(verb="read images"))

    def transcribe(self, **_kwargs) -> ModelResult:
        raise ModelError(_TEXT_ONLY.format(verb="transcribe audio"))

    def generate_image(self, **_kwargs) -> ModelResult:
        raise ModelError(_TEXT_ONLY.format(verb="generate images"))


def _parse_json_content(content: str):
    """Parse a model body's JSON, tolerating the code fences
    chat models love to wrap it in. Not-JSON is a failed call."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ModelError(
            f"The local model answered with text that is not JSON: {exc}."
        ) from exc
