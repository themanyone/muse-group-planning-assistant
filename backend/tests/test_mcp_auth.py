"""MCP authentication + authorisation tests (roadmap B-205).

The acceptance promises, in the roadmap's own order:

* expired, wrong-audience, and under-scoped tokens each fail
  with the MCP-correct error — ``token_expired``,
  ``token_invalid``, and ``insufficient_scope`` here;
* the local road is an agent-seat API key: it resolves to that
  seat (group, role, scopes), a person's key does not resolve,
  and a revoked key stops working on the next call;
* scope is re-checked at CALL time, not connect time: a
  principal whose scopes shrink after the server is built
  loses the tool on its very next call;
* credential scopes map onto internal scopes through the one
  table in ``gpa.mcp.auth``.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import bearer, make_group, register
from gpa.errors import AppError
from gpa.mcp.auth import (
    EXTERNAL_TO_INTERNAL,
    TOOL_SCOPES,
    OAuthTokenVerifier,
    authenticate_seat_key,
    issue_oauth_token,
    principal_from_access_token,
    require_tool_scope,
    resource_url,
    seat_key_provider,
    verify_oauth_token,
)
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server


def _seat(client, owner, group, scopes, role="member"):
    """Create one seat through the API; return (seat, raw key)."""
    made = client.post(
        f"/api/v1/groups/{group['id']}/seats",
        json={"name": "Auth Seat", "role": role, "scopes": scopes},
        headers=bearer(owner),
    )
    assert made.status_code == 201, made.text
    body = made.json()
    return body, body["key"]


# --- OAuth verification: the three acceptance failures -----------------------


def test_expired_token_fails_token_expired(settings):
    """An otherwise-valid token past its exp is token_expired."""
    token = issue_oauth_token(
        settings, subject="seat:abc", scopes=["read"], minutes=-5
    )
    with pytest.raises(AppError) as caught:
        verify_oauth_token(settings, token)
    assert caught.value.code == "token_expired"


def test_wrong_audience_token_fails_token_invalid(settings):
    """A token issued for another resource is token_invalid."""
    token = issue_oauth_token(
        settings,
        subject="seat:abc",
        scopes=["read"],
        audience="http://127.0.0.1:9999/mcp",
    )
    with pytest.raises(AppError) as caught:
        verify_oauth_token(settings, token)
    assert caught.value.code == "token_invalid"
    assert "different resource" in caught.value.detail


def test_bad_signature_and_unknown_scope_fail_token_invalid(settings):
    """Tampered signatures and unknown scopes are token_invalid."""
    token = issue_oauth_token(settings, subject="seat:abc", scopes=["read"])
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(AppError) as caught:
        verify_oauth_token(settings, tampered)
    assert caught.value.code == "token_invalid"
    weird = issue_oauth_token(settings, subject="seat:abc", scopes=["fly"])
    with pytest.raises(AppError) as caught:
        verify_oauth_token(settings, weird)
    assert caught.value.code == "token_invalid"


def test_valid_token_resolves_seat_principal_and_internal_mapping(settings):
    """A good token resolves its seat, scopes, and mapping."""
    token = issue_oauth_token(
        settings,
        subject="seat:seat-1",
        scopes=["read", "draft"],
        seat_id="seat-1",
        group_id="group-1",
        role="organiser",
    )
    principal = verify_oauth_token(settings, token)
    assert principal.kind == "seat" and principal.seat_id == "seat-1"
    assert principal.group_id == "group-1" and principal.role == "organiser"
    assert principal.label == "seat:seat-1"
    assert principal.internal_scopes() == EXTERNAL_TO_INTERNAL["draft"]
    # The audience this server derives is the bind address's URL.
    assert resource_url(settings).endswith("/mcp")


async def _verifier_result(settings, token):
    """Run the SDK verifier seam the HTTP middleware uses."""
    return await OAuthTokenVerifier(settings).verify_token(token)


def test_sdk_verifier_accepts_good_and_refuses_bad(settings):
    """The middleware seam: good token in, bad tokens -> None (401)."""
    good = issue_oauth_token(settings, subject="seat:s1", scopes=["read"])
    accepted = asyncio.run(_verifier_result(settings, good))
    assert accepted is not None and accepted.subject == "seat:s1"
    assert accepted.resource == resource_url(settings)
    expired = issue_oauth_token(
        settings, subject="seat:s1", scopes=["read"], minutes=-1
    )
    assert asyncio.run(_verifier_result(settings, expired)) is None
    wrong_aud = issue_oauth_token(
        settings, subject="seat:s1", scopes=["read"], audience="http://x/mcp"
    )
    assert asyncio.run(_verifier_result(settings, wrong_aud)) is None
    # The verified token maps back onto the same principal shape.
    principal = principal_from_access_token(accepted)
    assert principal.kind == "seat" and principal.scopes == frozenset({"read"})


# --- The seat-key road --------------------------------------------------------


def test_seat_key_resolves_and_revocation_lands_on_next_call(client, app, settings):
    """Seat key -> seat principal; after revoke, the provider refuses."""
    owner = register(client)
    group = make_group(client, owner)
    seat, raw_key = _seat(client, owner, group, ["read", "draft"], role="organiser")
    bridge = InternalBridge(settings, database=app.state.db)
    provider = seat_key_provider(bridge, raw_key)
    principal = provider()
    assert principal.kind == "seat" and principal.seat_id == seat["id"]
    assert principal.group_id == group["id"] and principal.role == "organiser"
    assert principal.scopes == frozenset({"read", "draft"})
    # Revoke the key through the seat's own door in the API: the
    # seat-key rows are revoked by deleting? No — revocation is
    # the key row's revoked_at stamp; set it via a fresh session
    # exactly as an operator action would land in the table.
    session = app.state.db.session()
    try:
        from sqlalchemy import text as sql_text

        session.execute(
            sql_text("UPDATE api_keys SET revoked_at = CURRENT_TIMESTAMP WHERE seat_id = :s"),
            {"s": seat["id"]},
        )
        session.commit()
    finally:
        session.close()
    with pytest.raises(AppError) as caught:
        provider()
    assert caught.value.code == "not_authenticated"


def test_person_key_and_garbage_key_do_not_resolve_as_seat_keys(client, app, settings):
    """A person's API key is not a seat key; garbage is refused."""
    owner = register(client)
    made = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "person key", "scopes": ["read"]},
        headers=bearer(owner),
    )
    assert made.status_code == 201, made.text
    session = app.state.db.session()
    try:
        for raw in (made.json()["key"], "gpa_not-a-real-key"):
            with pytest.raises(AppError) as caught:
                authenticate_seat_key(session, raw)
            assert caught.value.code == "not_authenticated"
            session.rollback()
    finally:
        session.close()


# --- Call-time scope re-check -------------------------------------------------


def test_under_scoped_principal_fails_insufficient_scope():
    """require_tool_scope: the under-scoped call's exact error."""
    from gpa.mcp.auth import McpPrincipal

    reader = McpPrincipal(kind="seat", seat_id="s", scopes=frozenset({"read"}))
    assert require_tool_scope(reader, "gpa_server_info") == "read"
    with pytest.raises(AppError) as caught:
        require_tool_scope(reader, "gpa_create_candidate_draft")
    assert caught.value.code == "insufficient_scope"
    # Every registered tool has a stated scope (registry honesty).
    from gpa.mcp.server import build_server as _build  # noqa: F401

    # Superseded by B-206: the generated registry grew the
    # catalogue to one tool per internal operation plus the two
    # meta tools; the scope table grew with it, and the registry
    # checker now asserts this set equals the registry's own.
    # Grown again by B-207 (groups / members / profile family),
    # B-208 (candidates / review-queue family), B-209
    # (events / RSVPs / calendar family), B-210
    # (sources / extraction-request family), B-211
    # (planning family), and B-212 (notifications / digests /
    # announcements family).
    assert set(TOOL_SCOPES) == {
        "gpa_server_info",
        "gpa_list_internal_operations",
        "gpa_create_candidate_draft",
        "gpa_publish_event",
        "gpa_request_notification",
        "gpa_notify_rsvp_promotion",
        "gpa_enqueue_job",
        "gpa_submit_ai_job",
        "gpa_emit_integration_event",
        "gpa_get_group",
        "gpa_list_members",
        "gpa_update_group",
        "gpa_get_profile",
        "gpa_update_profile",
        "gpa_list_candidates",
        "gpa_get_candidate",
        "gpa_approve_candidate",
        "gpa_reject_candidate",
        "gpa_edit_candidate",
        "gpa_list_events",
        "gpa_get_event",
        "gpa_cancel_event",
        "gpa_list_rsvps",
        "gpa_set_rsvp",
        "gpa_get_agenda",
        "gpa_list_sources",
        "gpa_get_source",
        "gpa_create_source",
        "gpa_append_source_chunks",
        "gpa_request_extraction",
        "gpa_list_tasks",
        "gpa_list_decisions",
        "gpa_get_decision_chain",
        "gpa_list_questions",
        "gpa_update_task",
        "gpa_resolve_question",
        "gpa_list_announcements",
        "gpa_create_announcement",
        "gpa_get_inbox",
        "gpa_get_notification_preferences",
        "gpa_list_digests",
        "gpa_preview_digest",
        # B-216: the elicitation bridge's one tool.
        "gpa_ask_for_missing_fact",
        # B-217: the sampling policy's one tool.
        "gpa_request_sampling",
    }


def test_tool_call_rechecks_scope_at_call_time(client, app, settings):
    """Scopes shrinking after build lose the tool on the next call.

    The server is built once; the provider's principal starts
    draft-scoped (the draft tool works) and then drops to
    read-only — the SAME server refuses the SAME tool, proving
    the check is per call, not a connect-time handshake fact.
    """
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    from gpa.mcp.auth import McpPrincipal

    state = {"scopes": frozenset({"read", "draft"})}
    server = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=state["scopes"]
        ),
    )
    async def call_draft():
        """Call the draft tool through the server's own dispatch."""
        return await server.call_tool(
            "gpa_create_candidate_draft",
            {
                "group_id": group["id"],
                "kind": "event",
                "title": "Call-time check draft",
                "created_by": "seat:s1",
            },
        )

    result = asyncio.run(call_draft())
    # FastMCP returns structured content as a dict for dict tools.
    data = result if isinstance(result, dict) else result[1]
    assert data["candidate_id"]
    state["scopes"] = frozenset({"read"})
    # FastMCP wraps tool refusals in its ToolError; the backend's
    # code leads the wrapped message, as B-204's rule promises.
    with pytest.raises(Exception) as caught:
        asyncio.run(call_draft())
    assert "insufficient_scope" in str(caught.value)
