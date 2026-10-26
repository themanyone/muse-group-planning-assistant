"""MCP sampling policy — the server asking the client's model (roadmap B-217).

What this module is, and the law it works under:

* **Sampling is the server spending someone else's model.**
  MCP sampling lets a server ask the CONNECTED CLIENT to run
  a generation on the client's own model and hand the text
  back (``sampling/createMessage``). That is a powerful and
  an unusual direction of travel: the prompt is chosen here,
  the bill lands on the client operator's model account, and
  the model that answers is one this deployment does not
  control, audit, or price. A server that could sample
  freely could farm a connected client's model — quietly,
  repeatedly, at the client's expense — and could route
  group-adjacent prompts out to a model the group never
  chose. That is why this slice's posture exists.
* **Off by default, on only by an operator's act.** The
  setting ``GPA_MCP_SAMPLING_ENABLED`` defaults to False,
  and nothing in this package turns it on implicitly — not a
  capable client, not a publish credential, not a tool
  argument. Enabling sampling is a deployment decision,
  taken by the operator who runs the server, in the open,
  in the environment. The default-off posture and this
  reason are also written in Doc 13 (the security chapter),
  where an operator deciding the question will look.
* **Where enabled, it is scoped, capped, and logged.**
  Scoped: the sampling tool requires the ``publish``
  credential scope at call time (B-205's law) and the
  ``sampling:request`` internal scope beneath it — asking a
  model to work over a group's material is publish-grade,
  exactly as commissioning an extraction run is (B-210).
  Capped: one call may request at most
  ``GPA_MCP_SAMPLING_MAX_TOKENS`` tokens of generation, and
  one group may make at most
  ``GPA_MCP_SAMPLING_MAX_CALLS_PER_DAY`` sampling calls per
  UTC day, counted on B-047's AI usage ledger; a group whose
  daily AI budget (that same ledger) is already spent may
  not sample at all. Logged: EVERY authorised call writes
  its ledger row and its hash-chained audit row through the
  ``sampling.sample`` operation BEFORE the client is asked,
  so an attempt is on the record even if the client's model
  then refuses or fails — and the record carries counts and
  lengths, never the prompt's or the answer's words.
* **Sampled text is data, never instructions.** What a
  client's model returns is model output about group
  material; the tool result marks it ``is_data: True`` and
  this server never executes, follows, or files it on its
  own — it returns to the calling agent as material, the
  same posture the server instructions state for all group
  content (and B-218's hardening states at this boundary).
* **No capable client, no sampling — and no fallback.**
  Unlike elicitation (B-216), sampling has no degraded
  road: a client that never advertised the sampling
  capability simply cannot be asked, and the tool says so
  in its result. Nothing is filed, queued, or approximated
  with the server's own AI drivers — a sampling request is
  a request for THE CLIENT's model, and substituting a
  different model would be a different act wearing its name.

The shapes, for the reviewer
----------------------------
* ``sampling_policy(settings)`` — the whole policy as data:
  enabled or not, the two caps, and the default-off reason.
* ``client_supports_sampling(client_params)`` — capability
  read from the initialize handshake (an object, a dict, or
  None; absence is a plain False, never an error).
* ``require_sampling_enabled(settings)`` — the policy door:
  raises ``sampling_disabled`` while the flag is off.
* ``effective_max_tokens(settings, requested)`` — the token
  cap applied: a caller's request, clamped to the ceiling.
* ``authorise_sampling_call(bridge, ...)`` — the pre-ask
  call through the bridge: validates, then records the
  attempt on the ledger and the audit chain via the
  ``sampling.sample`` operation, whose budget refusals
  travel back unchanged.
"""

from __future__ import annotations

from typing import Any

from gpa.config import Settings
from gpa.errors import AppError

# The reason sampling is off by default, in one statement.
# It is carried in the policy dict, in the refusal an agent
# meets while sampling is off, and in Doc 13 — one sentence,
# kept in one constant so the three tellings cannot drift.
DEFAULT_OFF_REASON = (
    "Server-initiated sampling spends the connected client's "
    "model budget on prompts this server chose, through a "
    "model this deployment does not control; it is therefore "
    "off unless an operator deliberately enables it."
)

# The internal operation every authorised sampling call is
# recorded through (registered in gpa.internal.handlers).
# Named here so the module, the tool in the server, and the
# tests name the same door without a second spelling.
SAMPLING_OPERATION = "sampling.sample"


def sampling_policy(settings: Settings) -> dict:
    """The sampling policy as plain data, for tools and tests.

    Everything an agent or an operator needs to know about
    whether sampling may happen here: the flag's state, the
    two caps that ride with it, and — always, enabled or not
    — the reason the default is off, so the posture explains
    itself at the surface that enforces it.
    """
    # The caps are read fresh from the settings each call:
    # an operator's change is the policy, with no cached
    # copy anywhere in this package to go stale against it.
    return {
        "enabled": bool(settings.mcp_sampling_enabled),
        "default_off": True,
        "max_tokens": int(settings.mcp_sampling_max_tokens),
        "max_calls_per_day": int(settings.mcp_sampling_max_calls_per_day),
        "reason": DEFAULT_OFF_REASON,
    }


def client_supports_sampling(client_params: Any) -> bool:
    """Does this client accept sampling requests at all?

    ``client_params`` is the initialize handshake's client
    parameters, in whatever shape the caller holds them: the
    SDK's params object, a plain dict of the same words, or
    None (no handshake in reach — a direct tool call in a
    test, say). Support means exactly one thing: a
    ``sampling`` capability is present. Its sub-capabilities
    (context, tools) govern OPTIONAL request features this
    slice never uses — a plain text generation needs the
    capability itself and nothing beneath it.

    Absence is never an error here: False simply means this
    client cannot be asked, which the tool reports as data.
    """
    # Normalise the two shapes to one lookup, exactly as the
    # elicitation bridge does: attributes on the SDK's
    # models, keys on a dict — the words are the same either
    # way ("capabilities", then "sampling").
    def _get(node: Any, name: str) -> Any:
        """One step of the capability walk, either shape."""
        if node is None:
            return None
        if isinstance(node, dict):
            return node.get(name)
        return getattr(node, name, None)

    # The walk itself: capabilities -> sampling. Any missing
    # step means the client never offered sampling, and the
    # honest answer is False.
    capabilities = _get(client_params, "capabilities")
    return _get(capabilities, "sampling") is not None


def require_sampling_enabled(settings: Settings) -> None:
    """The policy door: refuse while sampling is switched off.

    Raises ``sampling_disabled`` — a 403 in the backend's one
    refusal language, carrying the default-off reason — so
    an agent whose call is refused learns WHY the door is
    shut and whose act (the operator's) would open it,
    instead of meeting a bare failure it might retry.
    """
    # One check, at every entry: the helper below and the
    # server tool both pass through here, so no road into a
    # sampling request can step around the flag.
    if not settings.mcp_sampling_enabled:
        raise AppError("sampling_disabled", detail=DEFAULT_OFF_REASON)


def effective_max_tokens(settings: Settings, requested: int | None) -> int:
    """Apply the per-call token cap to one sampling request.

    A request that names no size gets the cap itself (the
    operator's ceiling IS the default size); a request under
    the cap keeps its size; a request over the cap is
    CLAMPED to it, not refused — the cap is a ceiling on
    spend, and a caller asking for more generation than the
    deployment allows simply gets the allowed amount, with
    the effective size stated in the tool result where the
    caller can see the clamp happened. A non-positive size
    is not a size at all and is refused as
    ``validation_error`` naming the value.
    """
    # The ceiling, read from the settings at call time for
    # the same fresh-read reason as the policy dict above.
    cap = int(settings.mcp_sampling_max_tokens)
    if requested is None:
        return cap
    if requested <= 0:
        raise AppError(
            "validation_error",
            detail=(
                f"A sampling request must ask for a positive "
                f"number of tokens; got {requested}."
            ),
        )
    return min(int(requested), cap)


def authorise_sampling_call(
    bridge: Any,
    *,
    settings: Settings,
    group_id: str,
    actor: str,
    prompt: str,
    max_tokens: int | None = None,
) -> dict:
    """Authorise and record one sampling call, before the ask.

    The order in this helper IS the policy: the enabled door
    first (no record is written for a call the deployment
    never allowed — a disabled server does not sample, and
    its ledger stays clean of attempts it refused at the
    gate), then the prompt's shape (a sampling request with
    no prompt is a bill with no work on it), then the token
    cap, and only then the bridge call to
    ``sampling.sample`` — the operation that checks the
    group's budget on B-047's ledger and writes this
    attempt's ledger row and audit row inside its own
    transaction. The operation's refusals (an unknown group,
    a spent budget, the day cap reached) travel back to the
    caller unchanged, in the backend's one error language.

    The prompt's WORDS travel only inside this call's
    payload to the operation, which records their length and
    never their text; nothing in this module logs them.

    Returns the operation's own result — the recorded
    call's facts (group, actor, effective token ceiling,
    today's sampling count and spend state) — extended with
    the effective ``max_tokens`` the client ask must use.
    """
    # Door one: the operator's flag, before anything else.
    require_sampling_enabled(settings)
    # Door two: the prompt must carry actual words to spend
    # a client's model on; blank is refused here, at the
    # module's own door, before the bridge is touched.
    if not prompt or not prompt.strip():
        raise AppError(
            "validation_error",
            detail="A sampling request needs a prompt with words in it.",
        )
    # Door three: the token ceiling, applied (and clamped)
    # here so the size the operation records is the size
    # the client is later asked for — one number, no drift.
    effective = effective_max_tokens(settings, max_tokens)
    payload: dict[str, Any] = {
        "group_id": group_id,
        "actor": actor,
        "prompt_chars": len(prompt),
        "max_tokens": effective,
        # The day cap travels with the call: the settings
        # live in this (the MCP) process, and the operation
        # enforces the number it is handed, stated in its
        # answer — no second settings read at the far door.
        "max_calls_per_day": int(settings.mcp_sampling_max_calls_per_day),
    }
    data = bridge.call(SAMPLING_OPERATION, payload)
    return {"max_tokens": effective, **data}
