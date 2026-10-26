"""MCP planning tool tests (roadmap B-211): tasks, decisions,
questions — the things approval mints.

The promises these guard:

* the family's world is built the agent way in these tests
  themselves: candidates drafted (B-204's tool) and approved
  (B-208's tool) mint a task, a decision, and a question,
  and the planning tools then read exactly those minted
  rows — provenance candidate_id riding along;
* a task's SUGGESTED owner stays visibly a suggestion
  (owner_id null) until a person assigns — the distinction
  the model itself states, kept by the serialiser;
* upkeep travels the services: a task edit lands, and
  resolving a question WITH decision text mints a linked
  Decision in the same act — after which the decisions log
  carries it and the question names it;
* "what did we decide?" is answered by the CHAIN: a
  superseded decision and its replacement walk oldest-first
  from either end, and current_only shows only the standing
  one;
* upkeep scope is draft at call time (B-205): a read-only
  principal lists but cannot edit or resolve.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import bearer, make_group, register
from gpa.mcp.auth import McpPrincipal
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.server import build_server


def _call(server, name, args):
    """Call one tool through the server's own dispatch."""

    async def run():
        """The one call, on the server's event loop."""
        return await server.call_tool(name, args)

    result = asyncio.run(run())
    return result if isinstance(result, dict) else result[1]


def _mint(server, group, owner, kind, title):
    """Draft + approve one candidate through the MCP tools;
    return the approval row (which names the minted entity)."""
    drafted = _call(
        server,
        "gpa_create_candidate_draft",
        {
            "group_id": group["id"],
            "kind": kind,
            "title": title,
            "created_by": f"user:{owner['user_id']}",
        },
    )
    return _call(
        server,
        "gpa_approve_candidate",
        {"consent": True, 
            "group_id": group["id"],
            "candidate_id": drafted["candidate_id"],
            "actor": f"user:{owner['user_id']}",
            "role": "owner",
            "user_id": owner["user_id"],
        },
    )


def _world(client, app, settings):
    """An owner, their group, a server on the app's bridge,
    and one task + one decision + one question minted
    through the queue road."""
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    task = _mint(server, group, owner, "task", "Bring chairs")
    decision = _mint(server, group, owner, "decision", "We meet on Saturdays")
    question = _mint(server, group, owner, "question", "Who brings the mead?")
    return owner, group, server, task, decision, question


# --- the minted things, read back ------------------------------------------------------


def test_lists_carry_the_minted_rows_with_provenance(client, app, settings):
    """Each list carries its minted row, and the rows name
    the candidate they were minted from — the provenance
    link is part of the row, not a lookup elsewhere."""
    owner, group, server, task, decision, question = _world(client, app, settings)
    tasks = _call(server, "gpa_list_tasks", {"group_id": group["id"]})
    assert tasks["count"] == 1
    row = tasks["tasks"][0]
    assert row["task_id"] == task["resulting_entity_id"]
    assert row["title"] == "Bring chairs" and row["status"] == "open"
    assert row["candidate_id"]
    decisions = _call(server, "gpa_list_decisions", {"group_id": group["id"]})
    assert [d["decision_id"] for d in decisions["decisions"]] == [
        decision["resulting_entity_id"]
    ]
    assert decisions["decisions"][0]["status"] == "current"
    questions = _call(server, "gpa_list_questions", {"group_id": group["id"]})
    assert questions["count"] == 1
    assert questions["questions"][0]["text"] == "Who brings the mead?"
    assert questions["questions"][0]["status"] == "open"


def test_a_suggested_owner_is_not_an_assignment(client, app, settings):
    """The minted task's owner SUGGESTION (from an edited
    draft naming 'Ben') rides in suggested_owner_label
    while owner_id stays null — evidence, not assignment,
    exactly as the mint's law states."""
    owner = register(client)
    group = make_group(client, owner)
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)
    drafted = _call(
        server,
        "gpa_create_candidate_draft",
        {
            "group_id": group["id"],
            "kind": "task",
            "title": "Book the hall",
            "created_by": f"user:{owner['user_id']}",
        },
    )
    _call(
        server,
        "gpa_edit_candidate",
        {
            "group_id": group["id"],
            "candidate_id": drafted["candidate_id"],
            "actor": f"user:{owner['user_id']}",
            "draft": {"fields": {"owner": {"value": "Ben"}}},
        },
    )
    _call(
        server,
        "gpa_approve_candidate",
        {"consent": True, 
            "group_id": group["id"],
            "candidate_id": drafted["candidate_id"],
            "actor": f"user:{owner['user_id']}",
            "role": "owner",
            "user_id": owner["user_id"],
        },
    )
    row = _call(server, "gpa_list_tasks", {"group_id": group["id"]})["tasks"][0]
    assert row["suggested_owner_label"] == "Ben"
    assert row["owner_id"] is None


# --- upkeep ------------------------------------------------------------------------------


def test_update_task_and_resolve_question_with_truth(client, app, settings):
    """A task edit lands through the service (title +
    status); resolving the question WITH decision text
    returns the question settled and LINKED, and the
    decisions log now carries the minted answer as current."""
    owner, group, server, task, decision, question = _world(client, app, settings)
    edited = _call(
        server,
        "gpa_update_task",
        {
            "group_id": group["id"],
            "task_id": task["resulting_entity_id"],
            "actor": f"user:{owner['user_id']}",
            "changes": {"title": "Bring chairs and benches", "status": "done"},
        },
    )
    assert edited["title"] == "Bring chairs and benches"
    assert edited["status"] == "done"
    resolved = _call(
        server,
        "gpa_resolve_question",
        {
            "group_id": group["id"],
            "question_id": question["resulting_entity_id"],
            "actor": f"user:{owner['user_id']}",
            "decision_text": "Ana brings the mead",
        },
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolution_decision_id"]
    decisions = _call(
        server, "gpa_list_decisions", {"group_id": group["id"], "current_only": True}
    )
    texts = {row["text"] for row in decisions["decisions"]}
    assert "Ana brings the mead" in texts
    with pytest.raises(Exception) as caught:
        _call(
            server,
            "gpa_resolve_question",
            {
                "group_id": group["id"],
                "question_id": question["resulting_entity_id"],
                "actor": f"user:{owner['user_id']}",
            },
        )
    assert "conflict" in str(caught.value)


# --- the chain ------------------------------------------------------------------------------


def test_decision_chain_walks_the_supersede_history(client, app, settings):
    """Supersede the minted decision at the HTTP door; the
    chain tool then walks BOTH rows oldest-first from the
    replacement's id, and current_only lists only the
    standing decision."""
    owner, group, server, task, decision, question = _world(client, app, settings)
    first_id = decision["resulting_entity_id"]
    made = client.post(
        f"/api/v1/groups/{group['id']}/decisions",
        json={"text": "We meet on Sundays now", "supersedes_id": first_id},
        headers=bearer(owner),
    )
    assert made.status_code == 201, made.text
    chain = _call(
        server,
        "gpa_get_decision_chain",
        {"group_id": group["id"], "decision_id": made.json()["id"]},
    )
    assert [row["text"] for row in chain["chain"]] == [
        "We meet on Saturdays",
        "We meet on Sundays now",
    ]
    assert chain["chain"][0]["status"] == "superseded"
    assert chain["chain"][1]["status"] == "current"
    current = _call(
        server, "gpa_list_decisions", {"group_id": group["id"], "current_only": True}
    )
    assert [row["text"] for row in current["decisions"]] == ["We meet on Sundays now"]


# --- upkeep scope at call time ---------------------------------------------------------------


def test_upkeep_tools_refuse_a_read_only_principal(client, app, settings):
    """A read-only principal lists the planning world and is
    refused both upkeep tools at the guard."""
    owner, group, server, task, decision, question = _world(client, app, settings)
    bridge = InternalBridge(settings, database=app.state.db)
    reader = build_server(
        bridge,
        principal_provider=lambda: McpPrincipal(
            kind="seat", seat_id="s1", scopes=frozenset({"read"})
        ),
    )
    assert _call(reader, "gpa_list_tasks", {"group_id": group["id"]})["count"] == 1
    for name, args in (
        (
            "gpa_update_task",
            {
                "group_id": group["id"],
                "task_id": task["resulting_entity_id"],
                "actor": "seat:s1",
                "changes": {"status": "done"},
            },
        ),
        (
            "gpa_resolve_question",
            {
                "group_id": group["id"],
                "question_id": question["resulting_entity_id"],
                "actor": "seat:s1",
            },
        ),
    ):
        with pytest.raises(Exception) as caught:
            _call(reader, name, args)
        assert "insufficient_scope" in str(caught.value), name
