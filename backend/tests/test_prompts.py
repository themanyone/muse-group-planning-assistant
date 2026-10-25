"""Prompt library tests (roadmap B-038).

The library's laws: every named prompt loads with a version and
its output schema; the manifest matches the files exactly; and
the acceptance — *changing a prompt without bumping its version
fails* — is proven by tampering with a copy of the library three
ways (silent edit, header-only bump, manifest drift).
"""

import shutil

import pytest

from gpa.ai import promptlib
from gpa.ai.promptlib import PromptError, load_prompt, verify_manifest, write_manifest

EXPECTED_PROMPTS = {
    "extraction",
    "extraction_tasks",
    "extraction_decisions",
    "extraction_questions",
    # Phase 6 (B-077): change detection joins the library —
    # words about an existing event draft as change candidates.
    "extraction_changes",
    "flyer_reading",
    "board_segmentation",
    "summary",
    "briefing",
    "reminder_wording",
    "ask_memory",
}

# Current versions, per prompt. Superseded by Phase 5 (B-063):
# this test once asserted every prompt sits at version 1 — true
# when the library was born, but a version that can never move
# is not a version. Phase 5 bumped flyer_reading to 2 (its item
# grew transcript / alt_text / links, B-065/B-066) and added
# board_segmentation at 1 (B-064). The law that matters is kept
# at full strength below: a version change without the manifest
# ceremony still fails.
EXPECTED_VERSIONS = {"flyer_reading": 2}


def test_every_named_prompt_loads_with_version_and_schema():
    for name in sorted(EXPECTED_PROMPTS):
        template = load_prompt(name)
        assert template.name == name
        assert template.version == EXPECTED_VERSIONS.get(name, 1)
        assert template.schema is not None, f"{name} must carry its output schema"
        assert template.body.strip(), f"{name} must not be empty"


def test_the_library_verifies_clean():
    """The shipped manifest matches the shipped templates."""
    assert verify_manifest() == []


def test_render_substitutes_placeholders():
    rendered = load_prompt("extraction").render(
        CHUNKS="[chunk 0] hello", GROUP_TIMEZONE="Europe/London"
    )
    assert "[chunk 0] hello" in rendered
    assert "Europe/London" in rendered
    assert "{{CHUNKS}}" not in rendered


def _copy_library(tmp_path):
    root = tmp_path / "prompts"
    shutil.copytree(promptlib.PROMPTS_DIR, root)
    return root


def test_silent_edit_without_version_bump_fails(tmp_path):
    """B-038 acceptance: edit a template, touch nothing else — the
    hash no longer matches the manifest, and loading refuses."""
    root = _copy_library(tmp_path)
    path = root / "extraction.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nBe extra chatty.\n", encoding="utf-8")
    problems = verify_manifest(root)
    assert any("without a version bump" in p for p in problems), problems
    with pytest.raises(PromptError):
        load_prompt("extraction", root=root)


def test_header_bump_without_manifest_update_fails(tmp_path):
    """Bumping the version number alone is not enough either —
    the manifest must be regenerated as the deliberate second act."""
    root = _copy_library(tmp_path)
    path = root / "summary.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("version: 1", "version: 2"),
        encoding="utf-8",
    )
    problems = verify_manifest(root)
    assert any("summary" in p for p in problems), problems


def test_proper_bump_flow_passes(tmp_path):
    """Edit + bump + regenerate manifest = a sound library at the
    new version. This is the documented way to change a prompt."""
    root = _copy_library(tmp_path)
    path = root / "summary.md"
    text = path.read_text(encoding="utf-8").replace("version: 1", "version: 2")
    path.write_text(text + "\nOne more instruction.\n", encoding="utf-8")
    write_manifest(root)
    assert verify_manifest(root) == []
    assert load_prompt("summary", root=root).version == 2


def test_extraction_schema_shape_is_the_contract():
    """The event schema the pipeline validates against (B-040):
    items of fields, per-field value/confidence/quote, and the
    date fields carrying raw phrases for the resolver (D-22)."""
    schema = load_prompt("extraction").schema
    fields = schema["properties"]["items"]["items"]["properties"]["fields"]["properties"]
    assert {"title", "starts", "ends", "place", "host", "cost", "bring", "description"} <= set(fields)
    datefield = schema["$defs"]["datefield"]
    assert {"date_phrase", "time_phrase"} <= set(datefield["properties"])
