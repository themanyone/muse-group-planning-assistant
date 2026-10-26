"""MCP elicitation bridge tests (roadmap B-216).

The promises these guard:

* the fact-kind vocabulary is closed and typed: place /
  time / free_text carry a string answer, yes_no a boolean,
  and an invented kind is refused as validation_error at the
  module's door, at the operation's door, and at the tool's;
* capability detection reads the handshake honestly: no
  params, no elicitation capability, or a url-only
  capability are all "cannot be asked" — and none of them is
  an error, because degradation is a designed road;
* where a client CAN be asked and the human accepts, the
  answer returns as the tool's data with ``approval: False``
  and NOTHING is filed — no open question appears;
* everywhere else (no context, a decline, a cancel) the
  question is filed as an open question (B-086) authored by
  the asking agent's identity, routed to the organiser
  inbox as a notification, and a linked candidate stays
  exactly as pending as it was — elicitation informs a
  draft, it never approves one;
* a linked candidate that is already decided refuses the
  filing as a conflict, and an unknown one as not_found;
* the tool's scope is draft at call time (B-205): a
  read-only principal is refused before anything is asked
  or filed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from conftest import make_group, register
from gpa.errors import AppError
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.elicitation import (
    NOT_AN_APPROVAL,
    PlaceAnswer,
    YesNoAnswer,
    answer_schema,
    client_supports_elicitation,
    degrade_to_open_question,
    elicitation_message,
)
from gpa.mcp.server import build_server
from gpa.models.notifications import Notification


def _call(server, name, args):
    """Call one tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _world(client, app, settings, **server_kwargs):
    """An owner (the group's organiser person), their group,
    and a server on the app's bridge."""
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge, **server_kwargs)
    return owner, group, bridge, server


def _tool_fn(server, name="gpa_ask_for_missing_fact"):
    """The served tool's own function, for ctx-carrying calls.

    A live elicitation needs a session context the dispatch
    in these tests does not have, so the elicited legs call
    the function directly with a fake context — the function
    IS the tool (FastMCP registers this callable), and the
    fake speaks exactly the two members the tool reads:
    ``request_context.client_params`` and ``elicit``.
    """
    return server._tool_manager.get_tool(name).fn


class _FakeCtx:
    """A stand-in MCP context: form-capable params + a scripted elicit."""

    def __init__(self, action="accept", answer="The village hall"):
        """Script one elicitation outcome (accept/decline/cancel)."""
        self.request_context = SimpleNamespace(
            client_params=SimpleNamespace(
                capabilities=SimpleNamespace(
                    elicitation=SimpleNamespace(
                        form=SimpleNamespace(), url=None
                    )
                )
            )
        )
        self._action = action
        self._answer = answer
        self.seen = []

    async def elicit(self, message, schema):
        """Record the ask; answer it as scripted."""
        self.seen.append((message, schema))
        if self._action == "accept":
            return SimpleNamespace(
                action="accept", data=schema(answer=self._answer)
            )
        return SimpleNamespace(action=self._action, data=None)


# --- the vocabulary and the schemas ---------------------------------------------


def test_fact_kinds_carry_typed_answer_schemas():
    """Each kind's form has one ``answer`` field of its kind's
    type: words for place/time/free_text, a boolean for yes_no."""
    assert answer_schema("place") is PlaceAnswer
    assert answer_schema("yes_no") is YesNoAnswer
    assert PlaceAnswer(answer="The hall").answer == "The hall"
    assert YesNoAnswer(answer=True).answer is True
    for kind in ("time", "free_text"):
        assert answer_schema(kind)(answer="words").answer == "words"


def test_an_invented_fact_kind_is_a_typed_refusal():
    """Unknown kind: validation_error at the module door, and
    the message the human would see carries the standing
    not-an-approval sentence for a real kind."""
    with pytest.raises(AppError) as caught:
        answer_schema("teleportation")
    assert caught.value.code == "validation_error"
    message = elicitation_message("Where do we meet?", "place")
    assert "Where do we meet?" in message and NOT_AN_APPROVAL in message


def test_capability_detection_reads_the_handshake():
    """Form support is the whole question: object and dict
    shapes agree, and every absence is a plain False."""
    capable = SimpleNamespace(
        capabilities=SimpleNamespace(
            elicitation=SimpleNamespace(form=SimpleNamespace(), url=None)
        )
    )
    assert client_supports_elicitation(capable) is True
    assert client_supports_elicitation(
        {"capabilities": {"elicitation": {"form": {}}}}
    ) is True
    # No params, no capability, url-only: all "cannot ask".
    assert client_supports_elicitation(None) is False
    assert client_supports_elicitation(SimpleNamespace(capabilities=None)) is False
    url_only = SimpleNamespace(
        capabilities=SimpleNamespace(
            elicitation=SimpleNamespace(form=None, url=SimpleNamespace())
        )
    )
    assert client_supports_elicitation(url_only) is False


# --- the degradation: filed, authored, routed, never an approval -----------------


def test_no_context_degrades_to_a_filed_question(client, app, settings):
    """The plain tool call (no elicitation in reach) files an
    open question authored by the agent, notifies the
    organiser person, and approves nothing."""
    owner, group, bridge, server = _world(client, app, settings)
    result = _call(
        server,
        "gpa_ask_for_missing_fact",
        {
            "group_id": group["id"],
            "question": "Where does the bonfire night happen?",
            "fact_kind": "place",
            "actor": "seat:scout-1",
        },
    )
    assert result["elicited"] is False and result["degraded"] is True
    assert result["approval"] is False and result["notified_organisers"] == 1
    # The question is really among the group's open questions,
    # in the agent's name — the B-086 row, not a side table.
    questions = _call(server, "gpa_list_questions", {"group_id": group["id"]})
    assert questions["count"] == 1
    row = questions["questions"][0]
    assert row["question_id"] == result["question_id"]
    assert row["text"] == "Where does the bonfire night happen?"
    assert row["created_by"] == "seat:scout-1" and row["status"] == "open"
    # The routing really landed: one elicitation notification
    # row for the organiser person in this group.
    db = app.state.db.session()
    try:
        rows = (
            db.query(Notification)
            .filter(
                Notification.group_id == group["id"],
                Notification.kind == "elicitation.question",
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].recipient_user_id == owner["user_id"]
        assert "seat:scout-1" in rows[0].body_text
    finally:
        db.close()


def test_degradation_leaves_a_linked_candidate_pending(client, app, settings):
    """A question ABOUT a draft informs it only: the candidate
    is still pending afterwards, and resolving the question
    later is a separate act this slice does not perform."""
    owner, group, bridge, server = _world(client, app, settings)
    drafted = _call(
        server,
        "gpa_create_candidate_draft",
        {
            "group_id": group["id"],
            "kind": "event",
            "title": "Bonfire night",
            "created_by": "seat:scout-1",
        },
    )
    result = _call(
        server,
        "gpa_ask_for_missing_fact",
        {
            "group_id": group["id"],
            "question": "What time does the bonfire start?",
            "fact_kind": "time",
            "actor": "seat:scout-1",
            "candidate_id": drafted["candidate_id"],
        },
    )
    assert result["candidate_id"] == drafted["candidate_id"]
    queue = _call(server, "gpa_list_candidates", {"group_id": group["id"]})
    assert queue["candidates"][0]["status"] == "pending"


def test_a_decided_or_unknown_candidate_refuses_the_filing(client, app, settings):
    """The context law at the operation's door: an unknown
    candidate is not_found; the filed question is not created
    for a context that cannot be informed."""
    owner, group, bridge, server = _world(client, app, settings)
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_ask_for_missing_fact",
            {
                "group_id": group["id"],
                "question": "Where?",
                "fact_kind": "place",
                "actor": "seat:scout-1",
                "candidate_id": "no-such-candidate",
            },
        )
    # FastMCP wraps tool refusals in its ToolError; the
    # backend's own code leads the message, as everywhere.
    assert "not_found" in str(caught.value)
    questions = _call(server, "gpa_list_questions", {"group_id": group["id"]})
    assert questions["count"] == 0


# --- the live ask -----------------------------------------------------------------


def test_an_accepted_elicitation_returns_data_and_files_nothing(
    client, app, settings
):
    """The happy road: a form-capable client, a human who
    answers — the answer is the tool's data, approval is
    False, and no open question was filed."""
    owner, group, bridge, server = _world(client, app, settings)
    ctx = _FakeCtx(action="accept", answer="The village hall")
    result = asyncio.run(
        _tool_fn(server)(
            group_id=group["id"],
            question="Where does the bonfire night happen?",
            fact_kind="place",
            actor="seat:scout-1",
            ctx=ctx,
        )
    )
    assert result["elicited"] is True and result["degraded"] is False
    assert result["answer"] == "The village hall"
    assert result["approval"] is False
    # The human saw the question and the standing sentence.
    assert NOT_AN_APPROVAL in ctx.seen[0][0]
    questions = _call(server, "gpa_list_questions", {"group_id": group["id"]})
    assert questions["count"] == 0


def test_a_declined_elicitation_still_files_the_question(client, app, settings):
    """Decline is not a dead end: the same question degrades
    to the filed road, with the agent's identity attached."""
    owner, group, bridge, server = _world(client, app, settings)
    ctx = _FakeCtx(action="decline")
    result = asyncio.run(
        _tool_fn(server)(
            group_id=group["id"],
            question="Do we move the feast indoors if it rains?",
            fact_kind="yes_no",
            actor="seat:scout-1",
            ctx=ctx,
        )
    )
    assert result["elicited"] is False and result["degraded"] is True
    assert result["approval"] is False
    questions = _call(server, "gpa_list_questions", {"group_id": group["id"]})
    assert questions["count"] == 1
    assert questions["questions"][0]["created_by"] == "seat:scout-1"


# --- the scope law ------------------------------------------------------------------


def test_a_read_only_principal_cannot_ask(client, app, settings):
    """Draft is the tool's scope: a read-only principal meets
    insufficient_scope at call time, and nothing is filed."""
    reader = McpPrincipal(
        kind="user", user_id="reader-1", scopes=frozenset({"read"})
    )
    owner, group, bridge, server = _world(
        client, app, settings, principal_provider=lambda: reader
    )
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_ask_for_missing_fact",
            {
                "group_id": group["id"],
                "question": "Where?",
                "fact_kind": "place",
                "actor": "user:reader-1",
            },
        )
    assert "insufficient_scope" in str(caught.value)


def test_the_helper_validates_before_it_calls(client, app, settings):
    """degrade_to_open_question is a public door too: an
    invented kind is refused before the bridge is touched."""
    owner, group, bridge, server = _world(client, app, settings)
    with pytest.raises(AppError) as caught:
        degrade_to_open_question(
            bridge,
            group_id=group["id"],
            actor="seat:scout-1",
            question="Where?",
            fact_kind="teleportation",
        )
    assert caught.value.code == "validation_error"
