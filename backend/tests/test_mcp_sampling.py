"""MCP sampling policy tests (roadmap B-217).

The promises these guard:

* sampling is OFF by default: the policy says so, the
  authorisation door refuses with ``sampling_disabled``
  carrying the reason, and a refused call writes no ledger
  or audit record — a disabled server does not sample;
* capability detection reads the handshake honestly: a
  present sampling capability is support, every absence is
  a plain False, in object and dict shapes alike;
* the token cap clamps rather than surprises, a blank
  prompt and a non-positive size are typed refusals, and
  an unknown group is ``not_found`` at the operation door;
* where enabled, an authorised call is recorded BEFORE the
  ask: one ledger row (operation ``mcp_sampling``, honestly
  zero server-side cost) and one hash-chained audit row
  (action ``mcp.sampling``) carrying counts and lengths —
  never the prompt's words;
* the budget gates bite: a group at its daily AI budget
  cap and a group that has used its sampling calls for the
  day both meet ``sampling_budget_exceeded``;
* a capable client's model answers through
  ``create_message`` with the authorised token ceiling,
  and its text returns marked ``is_data``; an incapable
  client is reported as data (``sampled: False``), with no
  model substituted and nothing else filed;
* the tool's scope is publish at call time: a principal
  without publish is refused before anything is recorded.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from conftest import make_group, register
from gpa.errors import AppError
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.sampling import (
    DEFAULT_OFF_REASON,
    authorise_sampling_call,
    client_supports_sampling,
    effective_max_tokens,
    sampling_policy,
)
from gpa.mcp.server import build_server
from gpa.models.ai import AiUsage
from gpa.models.core import AuditLog
from gpa.models.groups import Group


def _call(server, name, args):
    """Call one tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _world(client, app, settings, **server_kwargs):
    """An owner, their group, and a server on the app's bridge."""
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge, **server_kwargs)
    return owner, group, bridge, server


def _enabled(settings, **overrides):
    """The test settings with sampling switched on, plus caps."""
    values = {"mcp_sampling_enabled": True}
    values.update(overrides)
    return settings.model_copy(update=values)


def _tool_fn(server, name="gpa_request_sampling"):
    """The served tool's own function, for ctx-carrying calls."""
    return server._tool_manager.get_tool(name).fn


class _FakeSession:
    """A stand-in client session: one scripted create_message."""

    def __init__(self, text="A short generated summary."):
        """Script the model's answer text."""
        self._text = text
        self.seen = []

    async def create_message(self, messages, max_tokens):
        """Record the ask; answer with the scripted text."""
        self.seen.append((messages, max_tokens))
        return SimpleNamespace(
            content=SimpleNamespace(type="text", text=self._text),
            model="client-model-1",
            stopReason="endTurn",
        )


class _FakeCtx:
    """A stand-in MCP context: sampling-capable params + a session."""

    def __init__(self, session=None):
        """Carry a fake session (a fresh one by default)."""
        self.request_context = SimpleNamespace(
            client_params=SimpleNamespace(
                capabilities=SimpleNamespace(sampling=SimpleNamespace())
            ),
            session=session or _FakeSession(),
        )


def _rows(app, model, **filters):
    """Read rows straight from the test database."""
    db = app.state.db.session()
    try:
        query = db.query(model)
        for key, value in filters.items():
            query = query.filter(getattr(model, key) == value)
        return query.all()
    finally:
        db.close()


# --- the policy itself ------------------------------------------------------------


def test_sampling_is_off_by_default_and_says_why(settings):
    """The shipped posture: disabled, capped, reasoned."""
    policy = sampling_policy(settings)
    assert policy["enabled"] is False and policy["default_off"] is True
    assert policy["reason"] == DEFAULT_OFF_REASON
    assert policy["max_tokens"] == 512 and policy["max_calls_per_day"] == 20


def test_capability_detection_reads_the_handshake():
    """A present sampling capability is support; absences are False."""
    capable = SimpleNamespace(
        capabilities=SimpleNamespace(sampling=SimpleNamespace())
    )
    assert client_supports_sampling(capable) is True
    assert client_supports_sampling({"capabilities": {"sampling": {}}}) is True
    assert client_supports_sampling(None) is False
    assert client_supports_sampling(SimpleNamespace(capabilities=None)) is False
    assert (
        client_supports_sampling(
            SimpleNamespace(capabilities=SimpleNamespace(sampling=None))
        )
        is False
    )


def test_the_token_cap_clamps_and_refuses_nonpositive(settings):
    """Over-cap requests are clamped to the ceiling, not refused;
    a non-positive size is a validation_error naming itself."""
    on = _enabled(settings)
    assert effective_max_tokens(on, None) == 512
    assert effective_max_tokens(on, 100) == 100
    assert effective_max_tokens(on, 9999) == 512
    with pytest.raises(AppError) as caught:
        effective_max_tokens(on, 0)
    assert caught.value.code == "validation_error"


def test_a_disabled_server_refuses_and_records_nothing(client, app, settings):
    """Default settings: the tool meets sampling_disabled, and
    neither a ledger row nor an audit row is written."""
    owner, group, bridge, server = _world(client, app, settings)
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_request_sampling",
            {"consent": True, 
                "group_id": group["id"],
                "prompt": "Summarise the bonfire plan.",
                "actor": "seat:scout-1",
            },
        )
    assert "sampling_disabled" in str(caught.value)
    assert _rows(app, AiUsage, operation="mcp_sampling") == []
    assert _rows(app, AuditLog, action="mcp.sampling") == []


def test_authorise_refuses_a_blank_prompt_and_unknown_group(
    client, app, settings
):
    """The module's own doors: blank prompt is validation_error
    before the bridge; an unknown group is not_found at the
    operation, and neither refusal records a call."""
    owner, group, bridge, server = _world(client, app, _enabled(settings))
    with pytest.raises(AppError) as caught:
        authorise_sampling_call(
            bridge,
            settings=_enabled(settings),
            group_id=group["id"],
            actor="seat:scout-1",
            prompt="   ",
        )
    assert caught.value.code == "validation_error"
    with pytest.raises(AppError) as caught:
        authorise_sampling_call(
            bridge,
            settings=_enabled(settings),
            group_id="no-such-group",
            actor="seat:scout-1",
            prompt="Summarise.",
        )
    assert caught.value.code == "not_found"
    assert _rows(app, AiUsage, operation="mcp_sampling") == []


# --- the record and the budget ------------------------------------------------------


def test_an_authorised_call_is_ledgered_and_audited_without_words(
    client, app, settings
):
    """Enabled + no capable client in reach: sampled is False
    as data, but the attempt is already on both records —
    with the prompt's length, never its words."""
    owner, group, bridge, server = _world(client, app, _enabled(settings))
    result = _call(
        server,
        "gpa_request_sampling",
        {"consent": True, 
            "group_id": group["id"],
            "prompt": "Summarise the bonfire plan in one line.",
            "actor": "seat:scout-1",
        },
    )
    assert result["sampled"] is False
    assert result["reason"] == "client_sampling_unsupported"
    assert result["recorded"] is True and result["calls_today"] == 1
    ledger = _rows(app, AiUsage, operation="mcp_sampling")
    assert len(ledger) == 1 and ledger[0].group_id == group["id"]
    assert ledger[0].cost_micros == 0  # the client's model bills the client
    audit = _rows(app, AuditLog, action="mcp.sampling")
    assert len(audit) == 1 and audit[0].actor == "seat:scout-1"
    assert "bonfire" not in audit[0].detail_json


def test_the_day_cap_refuses_the_call_after_the_last_one(
    client, app, settings
):
    """max_calls_per_day=1: the first call records, the second
    meets sampling_budget_exceeded and records nothing more."""
    on = _enabled(settings, mcp_sampling_max_calls_per_day=1)
    owner, group, bridge, server = _world(client, app, on)
    first = _call(
        server,
        "gpa_request_sampling",
        {"consent": True, "group_id": group["id"], "prompt": "One.", "actor": "seat:scout-1"},
    )
    assert first["calls_today"] == 1
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_request_sampling",
            {"consent": True, "group_id": group["id"], "prompt": "Two.", "actor": "seat:scout-1"},
        )
    assert "sampling_budget_exceeded" in str(caught.value)
    assert len(_rows(app, AiUsage, operation="mcp_sampling")) == 1


def test_a_group_at_its_ai_budget_cap_may_not_sample(client, app, settings):
    """B-047's gate at this door: budget set to zero is spent
    by definition, so the very first sampling call is refused."""
    owner, group, bridge, server = _world(client, app, _enabled(settings))
    db = app.state.db.session()
    try:
        row = db.get(Group, group["id"])
        row.ai_daily_budget_micros = 0
        db.commit()
    finally:
        db.close()
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_request_sampling",
            {"consent": True, "group_id": group["id"], "prompt": "One.", "actor": "seat:scout-1"},
        )
    assert "sampling_budget_exceeded" in str(caught.value)
    assert _rows(app, AiUsage, operation="mcp_sampling") == []


# --- the ask itself -------------------------------------------------------------------


def test_a_capable_client_answers_and_the_text_is_data(client, app, settings):
    """The happy road: create_message is asked with the clamped
    ceiling, and the model's text returns marked as data."""
    owner, group, bridge, server = _world(client, app, _enabled(settings))
    session = _FakeSession(text="The bonfire starts at dusk.")
    ctx = _FakeCtx(session=session)
    result = asyncio.run(
        _tool_fn(server)(
            group_id=group["id"],
            prompt="Summarise the bonfire plan.",
            actor="seat:scout-1",
            max_tokens=9999,
            consent=True,
            ctx=ctx,
        )
    )
    assert result["sampled"] is True and result["is_data"] is True
    assert result["text"] == "The bonfire starts at dusk."
    assert result["model"] == "client-model-1"
    # The ask carried the prompt and the CLAMPED ceiling (512).
    messages, ceiling = session.seen[0]
    assert ceiling == 512 and messages[0].content.text.startswith("Summarise")


def test_a_principal_without_publish_cannot_sample(client, app, settings):
    """Publish is the tool's scope: a draft principal meets
    insufficient_scope at call time, and nothing is recorded."""
    drafter = McpPrincipal(
        kind="user", user_id="drafter-1", scopes=frozenset({"read", "draft"})
    )
    owner, group, bridge, server = _world(
        client,
        app,
        _enabled(settings),
        principal_provider=lambda: drafter,
    )
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_request_sampling",
            {"consent": True, "group_id": group["id"], "prompt": "One.", "actor": "user:drafter-1"},
        )
    assert "insufficient_scope" in str(caught.value)
    assert _rows(app, AiUsage, operation="mcp_sampling") == []
