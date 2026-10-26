"""Comment + documentation gate (roadmap B-176, standard §6A.1).

The promises guarded here, in the slice's own words:

* the real tree passes its own gate — every file carries a purpose
  header, every migration and prompt template carries its why, every
  ``.env.example`` setting is comment-introduced, no docstring merely
  restates its name, and no module sits below its ratchet floor;
* a deliberately stripped fixture module FAILS the gate, and the
  failure report names it by file and line — B-176's acceptance,
  word for word;
* each hard rule fails on its own, so a breach cannot hide inside a
  general failure: a migration with no why-docstring, a prompt
  template with no version header, an unexplained setting block,
  and a name-restating docstring are each refused by name.

The checker itself lives in ``scripts/comment_gate.py`` (stdlib
only, importable) — test.sh runs this suite, so the gate runs in
test.sh exactly as the roadmap requires.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "comment_gate", ROOT / "scripts" / "comment_gate.py"
)
comment_gate = importlib.util.module_from_spec(_spec)
# Registered before exec on purpose: the script's dataclasses look
# their module up in sys.modules while they are being processed,
# and an unregistered module makes that lookup fail (found by the
# suite's first single-test run).
sys.modules[_spec.name] = comment_gate
_spec.loader.exec_module(comment_gate)


def test_real_tree_passes_the_gate():
    """The shipped tree holds every hard rule and every floor."""
    report = comment_gate.check_tree(ROOT)
    assert report.ok, report.violations
    # The measured table is the gate's working, not a cached claim:
    # the modules the roadmap names are all present and measured.
    for module in ("services", "ai", "internal", "crypto", "models"):
        assert module in report.modules


def _fixture_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Lay down a minimal repo-shaped tree for the gate to judge."""
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


# A module stripped the way B-176's acceptance describes: a purpose
# header survives (so the failure is about the stripped insides,
# not the header), but every class and function docstring is gone.
STRIPPED = '''"""A fixture module whose insides were stripped for the gate."""

class Widget:
    def spin(self):
        return 1


def build_widget():
    return Widget()
'''


def test_stripped_fixture_fails_by_file_and_line(tmp_path):
    """B-176 acceptance: a stripped module fails, named by file+line."""
    root = _fixture_tree(
        tmp_path, {"src/gpa/services/stripped.py": STRIPPED}
    )
    report = comment_gate.check_tree(root)
    assert not report.ok
    # The gaps carry the exact addresses: class at line 3, its
    # method at line 4, the function at line 8 (counted in the
    # fixture above, header line included).
    by_name = {(g.name, g.line) for g in report.gaps}
    assert ("Widget", 3) in by_name
    assert ("spin", 4) in by_name
    assert ("build_widget", 8) in by_name
    assert all(g.path.endswith("stripped.py") for g in report.gaps)


def test_migration_without_why_docstring_fails(tmp_path):
    """A migration is a schema change; without its why it is refused."""
    root = _fixture_tree(
        tmp_path,
        {"migrations/versions/0099_bare.py": "def upgrade():\n    pass\n"},
    )
    report = comment_gate.check_tree(root)
    assert not report.ok
    assert any("migration" in v and "0099_bare" in v for v in report.violations)


def test_prompt_without_version_header_fails(tmp_path):
    """A prompt template outside the name+version discipline is refused."""
    root = _fixture_tree(
        tmp_path,
        {"src/gpa/ai/prompts/bare.md": "Just words, no header at all.\n"},
    )
    report = comment_gate.check_tree(root)
    assert not report.ok
    assert any("bare.md" in v for v in report.violations)


def test_unexplained_setting_block_fails(tmp_path):
    """An .env.example setting with no introducing comment is refused."""
    root = _fixture_tree(
        tmp_path,
        {".env.example": "# Explained block.\nGPA_A=1\n\nGPA_B=2\n"},
    )
    report = comment_gate.check_tree(root)
    assert not report.ok
    assert any(".env.example" in v for v in report.violations)


def test_restating_docstring_fails(tmp_path):
    """'add one' documenting add_one counts against the standard (§6A.1)."""
    root = _fixture_tree(
        tmp_path,
        {
            "src/gpa/services/restate.py": (
                '"""A fixture module for the restatement rule."""\n\n\n'
                "def add_one(value):\n"
                '    """Add one."""\n'
                "    return value + 1\n"
            )
        },
    )
    report = comment_gate.check_tree(root)
    assert not report.ok
    assert any("restates its name" in v for v in report.violations)
