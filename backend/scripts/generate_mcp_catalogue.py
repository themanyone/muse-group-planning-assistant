"""Regenerate Doc 13's generated MCP tool catalogue section (B-219).

    python scripts/generate_mcp_catalogue.py

What this tool does, and the discipline it keeps:

* The catalogue TABLE in docs/13-security-and-cryptography.md
  is a generated section, fenced by two marker comments —
  ``<!-- BEGIN GENERATED MCP TOOL CATALOGUE (B-219) -->`` and
  ``<!-- END GENERATED MCP TOOL CATALOGUE -->`` — inside the
  chapter's B-219 prose section. This script replaces the
  text BETWEEN those markers with
  ``gpa.mcp.catalogue.render_catalogue_markdown()``'s current
  rendering, byte for byte, and touches nothing else in the
  chapter: the prose around the table is a human's, the table
  is the generator's, and the fence keeps the two apart.
* Idempotent by construction: the section is replaced with
  the renderer's fresh output in the renderer's own framing
  (marker line, newline, rendered text, newline, marker
  line), so running the script twice changes the file once.
* It fails plainly when the markers are absent: a chapter
  whose B-219 section was renamed or removed is a chapter a
  human must look at, not a file a script should guess a new
  section into. The fix it names is the hand act — restore
  the prose section with its markers — after which this
  script fills the table again.
* The drift guard is the suite, not this script:
  tests/test_mcp_conformance.py compares the chapter's
  fenced section against the renderer's output exactly, so
  a registry change that forgets to regenerate fails a test
  BY NAME instead of shipping a stale catalogue in the
  signed documentation.

Stdlib plus the package itself; run from the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root and the chapter this tool writes into, named
# once: the script lives in scripts/, the chapter in docs/.
ROOT = Path(__file__).resolve().parent.parent
CHAPTER = ROOT / "docs" / "13-security-and-cryptography.md"

# The fence markers, verbatim as they stand in the chapter:
# the script and the chapter must agree on these strings
# exactly, which is why they are constants here and a test
# pins the section they fence.
BEGIN = "<!-- BEGIN GENERATED MCP TOOL CATALOGUE (B-219) -->"
END = "<!-- END GENERATED MCP TOOL CATALOGUE -->"


def regenerate(chapter_text: str, rendered: str) -> str:
    """One chapter's text with its fenced section replaced.

    Pure text work, apart from the file: the section between
    the markers becomes exactly ``rendered``, framed by one
    newline on each side, and every other byte of the chapter
    is preserved. Absent (or inverted) markers raise
    ValueError with the plain-words reason — see the module
    docstring for why guessing is refused here.
    """
    # Locate both markers first and refuse before changing
    # anything: a half-fenced chapter is a human's problem,
    # and a script that "repaired" it by appending a second
    # section would make two catalogues where Doc 13's law
    # allows exactly one.
    if BEGIN not in chapter_text or END not in chapter_text:
        raise ValueError(
            "docs/13 is missing the B-219 catalogue markers — restore the "
            "'MCP conformance + client pack (B-219)' section with its "
            "BEGIN/END GENERATED MCP TOOL CATALOGUE markers by hand, "
            "then run this script again."
        )
    head, rest = chapter_text.split(BEGIN, 1)
    _old_section, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{rendered}\n{END}{tail}"


def main() -> int:
    """Regenerate the chapter's catalogue section in place.

    Returns a process exit code: 0 with a one-line receipt
    (tool count + digest prefix, read from the renderer's
    own output) on success; 1 with the refusal on stderr
    when the markers are absent, having written nothing.
    """
    # The renderer is imported here, after sys.path is set
    # below in the entry guard's spirit: running the script
    # from the repo root finds src/ through the path insert,
    # exactly as scripts/build_file_atlas.py does.
    sys.path.insert(0, str(ROOT / "src"))
    from gpa.mcp.catalogue import render_catalogue_markdown

    rendered = render_catalogue_markdown()
    try:
        updated = regenerate(CHAPTER.read_text(encoding="utf-8"), rendered)
    except ValueError as exc:
        print(f"generate_mcp_catalogue: {exc}", file=sys.stderr)
        return 1
    CHAPTER.write_text(updated, encoding="utf-8")
    print(f"catalogue: Doc 13 section regenerated ({len(rendered.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
