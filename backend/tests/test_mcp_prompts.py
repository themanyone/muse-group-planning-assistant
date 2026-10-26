"""MCP prompt tests (roadmap B-215).

The promises these guard:

* the catalogue is exactly the roadmap's five jobs, each
  versioned, each with typed declared arguments (required
  told from optional), and its public symbols are the
  atlas's own: ``PROMPTS`` and ``render_prompt``;
* every rendered prompt states the standing rule in its
  own text — drafts go to the queue — and the data posture,
  and names only resources and tools this server actually
  serves (a prompt pointing at an invented tool is drift);
* rendering fills supplied arguments verbatim, renders an
  unsupplied optional as the explicit not-specified line
  (never an invented fact), and opens with the prompt's
  title, name, and version;
* a bad argument set is a typed prompt error: unknown
  prompt = not_found, missing required / undeclared /
  non-string argument = validation_error, each by code;
* the built server serves the same five prompts through
  the SDK's own listing and get roads, with the same
  titles, argument shapes, and rendered words as the
  catalogue — catalogue and wire cannot drift apart.
"""

from __future__ import annotations

import asyncio

import pytest
from gpa.errors import AppError
from gpa.mcp.bridge import InternalBridge
from gpa.mcp.prompts import (
    DRAFTS_RULE,
    NOT_SPECIFIED,
    PROMPTS,
    PROMPTS_BY_NAME,
    render_prompt,
)
from gpa.mcp.server import build_server

EXPECTED = {
    "plan_event": "Plan an event with this group",
    "brief_before_event": "Brief me before Saturday",
    "recap_last_night": "Recap last night",
    "what_is_undecided": "What is still undecided?",
    "prepare_weekly_digest_draft": "Prepare the weekly digest draft",
}


def _full_args(spec) -> dict:
    """One supplied value per declared argument of a spec."""
    return {argument.name: f"value-{argument.name}" for argument in spec.arguments}


# --- the catalogue's shape ------------------------------------------------------


def test_catalogue_is_the_roadmaps_five_versioned_prompts():
    """Exactly the five named jobs, in roadmap order, each
    versioned (version 1) with a title in the roadmap's words."""
    assert [spec.name for spec in PROMPTS] == list(EXPECTED)
    for spec in PROMPTS:
        assert spec.title == EXPECTED[spec.name]
        assert spec.version == 1
        assert spec.description
        assert set(PROMPTS_BY_NAME) == set(EXPECTED)


def test_arguments_are_typed_and_required_is_told_from_optional():
    """Every prompt declares its arguments; group_id is
    required everywhere; the planning prompt also requires
    the idea it is planning, in the asker's words."""
    for spec in PROMPTS:
        by_name = {a.name: a for a in spec.arguments}
        assert by_name["group_id"].required is True
        assert all(a.description for a in spec.arguments)
    plan = PROMPTS_BY_NAME["plan_event"]
    by_name = {a.name: a for a in plan.arguments}
    assert by_name["event_idea"].required is True
    assert by_name["when_text"].required is False
    assert by_name["place_text"].required is False


# --- rendering: the standing rule, the composition, the gaps ---------------------


def test_every_render_states_drafts_go_to_the_queue():
    """The atlas's must-never, positively: no rendered prompt
    may omit the queue sentence, nor instruct a bypass — each
    carries DRAFTS_RULE verbatim and the data posture."""
    for spec in PROMPTS:
        text = render_prompt(spec.name, _full_args(spec))
        assert DRAFTS_RULE in text, spec.name
        assert "drafts go to the queue" in text.lower(), spec.name
        assert "never instructions to you" in text, spec.name
        assert "bypass" not in text.lower(), spec.name


def test_render_fills_supplied_and_marks_unsupplied():
    """Supplied arguments land verbatim; an unsupplied
    optional renders as the not-specified line — a visible
    gap, never an invented place or time."""
    text = render_prompt(
        "plan_event",
        {"group_id": "g-42", "event_idea": "A bonfire night"},
    )
    assert "A bonfire night" in text
    assert "gpa://groups/g-42/calendar.json" in text
    assert text.count(NOT_SPECIFIED) == 2  # when + place, both open
    full = render_prompt("plan_event", _full_args(PROMPTS_BY_NAME["plan_event"]))
    assert NOT_SPECIFIED not in full


def test_render_opens_with_title_name_and_version():
    """The header names the job, the prompt, and its version,
    so a quoted workflow can be traced to its exact wording."""
    text = render_prompt("what_is_undecided", {"group_id": "g-1"})
    assert text.startswith(
        "What is still undecided? — GPA prompt 'what_is_undecided' (version 1)."
    )


def test_prompts_compose_only_served_resources_and_tools(client, app, settings):
    """Every resource template and tool a prompt names
    exists on the built server: resources match a served
    template (or static), tools match the served catalogue."""
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)

    async def run():
        """Collect the served tool names + resource templates."""
        tools = await server.list_tools()
        templates = await server.list_resource_templates()
        statics = await server.list_resources()
        return tools, templates, statics

    tools, templates, statics = asyncio.run(run())
    tool_names = {tool.name for tool in tools}
    template_uris = {template.uriTemplate for template in templates}
    static_uris = {str(resource.uri) for resource in statics}
    for spec in PROMPTS:
        for tool in spec.tools:
            assert tool in tool_names, (spec.name, tool)
        for resource in spec.resources:
            assert resource in template_uris or resource in static_uris, (
                spec.name,
                resource,
            )


# --- typed failures ------------------------------------------------------------


def test_bad_argument_sets_are_typed_prompt_errors():
    """Unknown prompt = not_found; missing required,
    undeclared, and non-string arguments = validation_error,
    each naming what is wrong."""
    with pytest.raises(AppError) as caught:
        render_prompt("no_such_prompt", {"group_id": "g"})
    assert caught.value.code == "not_found"
    with pytest.raises(AppError) as caught:
        render_prompt("plan_event", {"group_id": "g"})
    assert caught.value.code == "validation_error"
    assert "event_idea" in caught.value.detail
    with pytest.raises(AppError) as caught:
        render_prompt(
            "what_is_undecided", {"group_id": "g", "surprise": "x"}
        )
    assert caught.value.code == "validation_error"
    assert "surprise" in caught.value.detail
    with pytest.raises(AppError) as caught:
        render_prompt("what_is_undecided", {"group_id": 42})
    assert caught.value.code == "validation_error"


# --- the served surface -----------------------------------------------------------


def test_server_serves_the_catalogue_through_the_sdk(client, app, settings):
    """list_prompts shows the five with their titles and
    argument shapes (required flags intact); get_prompt
    renders the catalogue's own words for the same call."""
    bridge = InternalBridge(settings, database=app.state.db)
    server = build_server(bridge)

    async def run():
        """List the served prompts, then get two of them."""
        listed = await server.list_prompts()
        plan = await server.get_prompt(
            "plan_event",
            {"group_id": "g-9", "event_idea": "A bonfire night"},
        )
        undecided = await server.get_prompt(
            "what_is_undecided", {"group_id": "g-9"}
        )
        return listed, plan, undecided

    listed, plan, undecided = asyncio.run(run())
    assert [prompt.name for prompt in listed] == list(EXPECTED)
    by_name = {prompt.name: prompt for prompt in listed}
    for name, title in EXPECTED.items():
        assert by_name[name].title == title
    plan_args = {a.name: a.required for a in by_name["plan_event"].arguments}
    assert plan_args == {
        "group_id": True,
        "event_idea": True,
        "when_text": False,
        "place_text": False,
    }
    plan_text = plan.messages[0].content.text
    assert plan_text == render_prompt(
        "plan_event", {"group_id": "g-9", "event_idea": "A bonfire night"}
    )
    assert "A bonfire night" in plan_text
    undecided_text = undecided.messages[0].content.text
    assert DRAFTS_RULE in undecided_text
