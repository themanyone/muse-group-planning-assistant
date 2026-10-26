"""MCP elicitation bridge — asking the human (roadmap B-216).

What this module is, and the law it works under:

* **Elicitation is the server asking, not deciding.** An agent
  mid-draft often discovers it is missing ONE fact — the place,
  the time, a yes/no on a draft's shape. MCP gives the server a
  road for exactly that: an ``elicitation/create`` request to
  the client, whose user answers a small form. This module is
  that road's GPA shape: the closed vocabulary of fact kinds an
  agent may ask about, the typed answer schema each kind
  carries, and the capability check that tells the server
  whether this client can be asked at all.
* **An answer can inform a draft; it can NEVER be an
  approval.** That sentence is the slice's whole law, and it is
  structural here, not a promise in prose: an elicited answer
  is returned to the CALLING AGENT as data (the tool result),
  and the agent's next act — if any — is the ordinary draft
  edit through the ordinary tools, which lands in the review
  queue like every draft. Nothing in this module calls an
  approval operation, mints an entity, or marks anything
  decided. The constant :data:`NOT_AN_APPROVAL` states it in
  one sentence every result carries.
* **No capability, no dead end: degrade to an open
  question.** A client that does not support elicitation (or a
  user who declines or cancels) must not lose the question.
  The bridge's fallback — :func:`degrade_to_open_question` —
  files the SAME question through the internal
  ``elicitation.ask`` operation, which records it as one of
  the group's open questions (B-086) with the asking agent's
  identity attached as its author, and routes it to the
  organiser inbox as a notification. The human answers it
  there, in the group's own time, and the answer settles
  through the ordinary question law — again, never as an
  approval of anything.
* **The fact kinds are a closed vocabulary.** ``place`` and
  ``time`` ask for a short text answer in the group's own
  words (the server does not parse them into facts here — a
  place name is evidence for a draft, exactly as a source's
  words are); ``yes_no`` asks for a boolean; ``free_text``
  covers the honest remainder. An invented kind is refused as
  ``validation_error`` naming the kind, at this module's door,
  before any elicitation is attempted or any question filed —
  a kind the group has no answer shape for is not a question
  this bridge may ask.
* **This module holds no backend door of its own.** Like the
  rest of the MCP package it speaks only through the bridge
  object it is handed (the internal encrypted API, B-204);
  the degradation helper takes the bridge as an argument for
  exactly that reason. The capability check and the schemas
  are pure — they touch no session, no group, no database.

The shapes, for the reviewer
----------------------------
* ``client_supports_elicitation(client_params)`` — form-mode
  support, read from the initialize handshake's client
  capabilities (an object, a dict, or None; absence is a
  plain False, never an error — degradation is the answer).
* ``answer_schema(fact_kind)`` — the pydantic model the
  elicitation form carries: one ``answer`` field, a string
  for place/time/free_text, a boolean for yes_no.
* ``elicitation_message(question, fact_kind)`` — the words
  the human sees: the agent's question, the kind of fact it
  is, and the not-an-approval sentence, so the person asked
  knows their answer informs a draft and decides nothing.
* ``degrade_to_open_question(bridge, ...)`` — the fallback
  call, returning the filed question's own data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from gpa.errors import AppError

# The closed vocabulary of missing facts an agent may ask a
# human about (see the module docstring): a place, a time, a
# yes/no, or — honestly labelled — anything else in words.
# Order is the catalogue's order, roadmap words first.
FACT_KINDS: tuple[str, ...] = ("place", "time", "yes_no", "free_text")

# The one sentence every elicitation result carries, in the
# message the human sees and in the tool result the agent
# reads: an elicited answer is material for a draft, and the
# draft still faces the queue. Kept in one constant so the
# server tool, the filed question, and the tests can never
# drift apart on the sentence the slice exists to enforce.
NOT_AN_APPROVAL = (
    "An elicited answer can inform a draft; it can never be an approval."
)


# --- the answer schemas ---------------------------------------------------------
#
# One small pydantic model per fact kind, each with exactly
# one field named ``answer`` — the MCP elicitation law allows
# only primitive fields in a form schema, and one field is
# the whole form a missing fact needs. The field's own
# description is what a client renders beside the box, so it
# is written for the human answering, not for the agent.


class PlaceAnswer(BaseModel):
    """The human's answer to a missing-place question."""

    # A place in the group's own words — a name, an address,
    # "the usual hall". Parsing it into a structured place is
    # the draft's job later, never this form's.
    answer: str = Field(description="The place, in your own words.")


class TimeAnswer(BaseModel):
    """The human's answer to a missing-time question."""

    # A time in words ("Saturday 7pm", "after the market").
    # Resolving words to a moment is the date resolver's work
    # at draft time (B-039), not an elicitation form's.
    answer: str = Field(description="The time, in your own words.")


class YesNoAnswer(BaseModel):
    """The human's answer to a yes/no question about a draft."""

    # A boolean, because a yes/no the agent must then PARSE
    # out of prose is a yes/no that can be misread; the form
    # carries the decision's shape, the queue carries its law.
    answer: bool = Field(description="Yes (true) or no (false).")


class FreeTextAnswer(BaseModel):
    """The human's answer to any other missing fact."""

    # The honest remainder: a fact that is neither a place, a
    # time, nor a yes/no still arrives as one string of words.
    answer: str = Field(description="The missing fact, in your own words.")


# Fact kind -> the answer model its form carries. The table
# IS the vocabulary's definition: require_fact_kind below
# reads membership from it, so a kind cannot be askable
# without an answer shape, nor shaped without being askable.
ANSWER_SCHEMAS: dict[str, type[BaseModel]] = {
    "place": PlaceAnswer,
    "time": TimeAnswer,
    "yes_no": YesNoAnswer,
    "free_text": FreeTextAnswer,
}


def require_fact_kind(fact_kind: str) -> str:
    """Validate one fact kind against the closed vocabulary.

    Returns the kind unchanged when it is askable; refuses
    anything else as ``validation_error`` naming the kind —
    the refusal the tool layer renders as an MCP tool error,
    so an agent learns its kind was invented, in the backend's
    one refusal language, before anything was asked or filed.
    """
    # Membership in the schema table, not the tuple: the two
    # are the same set by construction, and the table is the
    # one the next step (the form) actually needs.
    if fact_kind not in ANSWER_SCHEMAS:
        raise AppError(
            "validation_error",
            detail=(
                f"Unknown fact kind {fact_kind!r}: an elicitation can ask "
                f"about one of {', '.join(FACT_KINDS)}."
            ),
        )
    return fact_kind


def answer_schema(fact_kind: str) -> type[BaseModel]:
    """The answer model one fact kind's elicitation form carries.

    Validation rides along (an unknown kind raises here), so
    a caller that asks for the schema has already passed the
    vocabulary door — there is no path that elicits first and
    validates later.
    """
    return ANSWER_SCHEMAS[require_fact_kind(fact_kind)]


def client_supports_elicitation(client_params: Any) -> bool:
    """Does this client accept FORM-mode elicitation requests?

    ``client_params`` is the initialize handshake's client
    parameters, in whatever shape the caller holds them: the
    SDK's params object, a plain dict of the same words, or
    None (no handshake in reach — a direct tool call in a
    test, say). Support means: an elicitation capability is
    present AND its form mode is present — this bridge asks
    with forms (the SDK client's own capability advertises
    form + url when it installs a real callback, and nothing
    at all when it does not, which is exactly the distinction
    this check exists to read).

    Absence is never an error here: False simply routes the
    caller to the degradation, which is a designed road, not
    a failure.
    """
    # Normalise the two shapes to one lookup: attributes on
    # the SDK's models, keys on a dict — the words are the
    # same either way ("capabilities", then "elicitation",
    # then "form").
    def _get(node: Any, name: str) -> Any:
        """One step of the capability walk, either shape."""
        if node is None:
            return None
        if isinstance(node, dict):
            return node.get(name)
        return getattr(node, name, None)

    # The walk itself: capabilities -> elicitation -> form.
    # Any missing step means the client never offered form
    # elicitation, and the honest answer is False.
    capabilities = _get(client_params, "capabilities")
    elicitation = _get(capabilities, "elicitation")
    if elicitation is None:
        return False
    return _get(elicitation, "form") is not None


def elicitation_message(question: str, fact_kind: str) -> str:
    """The words the human sees when the server asks.

    The agent's question verbatim first (it is the agent's
    question, asked in its words, with its identity attached
    at the tool layer), then what kind of fact would answer
    it, then the standing sentence: the person asked must
    know, BEFORE answering, that their answer informs a
    draft and approves nothing — consent to answer is only
    informed if the answer's weight is stated up front.
    """
    return (
        f"{question}\n\n"
        f"(A missing fact of kind: {fact_kind}.)\n"
        f"{NOT_AN_APPROVAL}"
    )


def degrade_to_open_question(
    bridge: Any,
    *,
    group_id: str,
    actor: str,
    question: str,
    fact_kind: str,
    candidate_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    """File the question as an open question through the bridge.

    The degradation road (see the module docstring): one
    internal ``elicitation.ask`` call, which records the
    question among the group's open questions (B-086) with
    ``actor`` — the asking agent's identity — as its author,
    and routes it to the organiser inbox. The payload names
    only what the operation needs; optional context (the
    draft it would inform, the event it belongs to) rides
    only when the caller actually has it — an absent context
    is omitted, never sent as a null the operation must
    interpret.

    Returns the operation's own result: the filed question's
    data, the count of organisers notified, and the explicit
    ``approval: False`` the operation stamps on its answer.
    """
    # Validate at this door too: the helper is public, and a
    # direct caller must meet the same vocabulary law as the
    # tool that normally routes through it.
    require_fact_kind(fact_kind)
    payload: dict[str, Any] = {
        "group_id": group_id,
        "actor": actor,
        "question": question,
        "fact_kind": fact_kind,
    }
    if candidate_id is not None:
        payload["candidate_id"] = candidate_id
    if event_id is not None:
        payload["event_id"] = event_id
    return bridge.call("elicitation.ask", payload)
