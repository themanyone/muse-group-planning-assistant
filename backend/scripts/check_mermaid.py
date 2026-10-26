#!/usr/bin/env python3
"""Mermaid render check — roadmap B-181 (documentation architecture).

WHY THIS FILE EXISTS
    Roadmap D-34 / section 6A.8: every diagram in the documentation
    set is Mermaid, and a diagram that does not parse blocks a
    release exactly like a failing test. This checker is the gate.
    It walks a tree of Markdown files, extracts every ```mermaid
    fenced block, and validates each block's structure with a
    stdlib-only parser (no Node, no network, no mermaid-cli — the
    checker must run on every machine the backend runs on,
    including a Pi, per the project's native-only law).

WHAT "PARSES" MEANS HERE (honest boundary)
    A full Mermaid render needs the Mermaid JS engine. What this
    checker proves, deterministically and offline, is the class of
    breakage that actually ships broken diagrams:
      * an unterminated ```mermaid fence,
      * an empty diagram block,
      * a first line that is not a known Mermaid diagram type,
      * unbalanced (), [], {} or "" inside the diagram body
        (the classic hand-edit breakage: a dropped bracket).
    A block failing any rule is reported BY FILE AND LINE, and the
    checker exits non-zero — the acceptance shape B-181 names:
    "a deliberately broken diagram fails the gate, by file."

USAGE
    python scripts/check_mermaid.py [path ...]
    With no paths, checks docs/ plus the repo-root Markdown and
    submission/ (the surfaces that ship in a release ZIP).
    Exit 0 = every diagram structurally sound; 1 = failures named.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The Mermaid diagram types this project uses or may plausibly use.
# A first line naming anything else is not a diagram Mermaid can
# render, so it is a failure, not a warning.
KNOWN_DIAGRAM_TYPES = {
    "graph", "flowchart", "sequencediagram", "classdiagram",
    "statediagram", "statediagram-v2", "erdiagram", "gantt",
    "pie", "userjourney", "journey", "gitgraph", "mindmap",
    "timeline", "quadrantchart", "requirementdiagram",
    "c4context", "c4container", "c4component", "sankey-beta",
    "xychart-beta", "block-beta",
}

# Bracket pairs whose balance is checked inside a diagram body.
# Mermaid node shapes lean on all of these; an odd count is the
# signature of a truncated or double-typed edit.
_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TO_OPEN = {v: k for k, v in _OPEN_TO_CLOSE.items()}


def extract_blocks(text: str) -> list[tuple[int, list[str], bool]]:
    """Return (start_line, body_lines, terminated) per mermaid block.

    start_line is 1-based, pointing at the opening fence so a
    failure message can name the exact place a human must fix.
    """
    blocks: list[tuple[int, list[str], bool]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().lower().startswith("```mermaid"):
            start = i + 1  # 1-based line of the fence itself
            body: list[str] = []
            i += 1
            terminated = False
            while i < len(lines):
                if lines[i].strip().startswith("```"):
                    terminated = True
                    break
                body.append(lines[i])
                i += 1
            blocks.append((start, body, terminated))
        i += 1
    return blocks


def check_block(body: list[str]) -> str | None:
    """Return a plain-words problem for one diagram body, or None."""
    # Drop blank lines and %% comments before judging the shape.
    meaningful = [
        ln for ln in body if ln.strip() and not ln.strip().startswith("%%")
    ]
    if not meaningful:
        return "the diagram block is empty"
    first = meaningful[0].strip().split()[0].lower()
    # 'pie title X' and 'graph TD' both lead with the type word;
    # a title line after the type is fine, before it is not Mermaid.
    if first not in KNOWN_DIAGRAM_TYPES:
        return f"first line does not name a Mermaid diagram type (saw {first!r})"
    # Balance check over the whole body, ignoring %% comment tails.
    # erDiagram crow's-foot cardinality (B-192) spends braces as
    # relationship glyphs — o{ and }| are "many" feet, not block
    # braces — so those two-character tokens are normalised away
    # before counting; an entity attribute block's own { } pair,
    # which never touches a | or an o in that spelling, still
    # counts in full, and a dropped closing brace still fails.
    is_er = first == "erdiagram"
    stack: list[str] = []
    in_quote = False
    for raw in meaningful:
        line = raw.split("%%", 1)[0]
        if is_er:
            for token in ("o{", "}|", "}o", "{|", "|{", "{o"):
                line = line.replace(token, "x")
        for ch in line:
            if ch == '"':
                in_quote = not in_quote
            elif in_quote:
                continue
            elif ch in _OPEN_TO_CLOSE:
                stack.append(ch)
            elif ch in _CLOSE_TO_OPEN:
                if not stack or stack[-1] != _CLOSE_TO_OPEN[ch]:
                    return f"unbalanced {ch!r} in the diagram body"
                stack.pop()
    if in_quote:
        return "an unterminated double quote in the diagram body"
    if stack:
        return f"unclosed {stack[-1]!r} in the diagram body"
    return None


def check_file(path: Path) -> list[str]:
    """Return failure strings (file + line + problem) for one file."""
    failures: list[str] = []
    text = path.read_text(encoding="utf-8")
    for start, body, terminated in extract_blocks(text):
        if not terminated:
            failures.append(
                f"{path}:{start}: the ```mermaid fence is never closed"
            )
            continue
        problem = check_block(body)
        if problem is not None:
            failures.append(f"{path}:{start}: {problem}")
    return failures


def iter_markdown(paths: list[Path]) -> list[Path]:
    """Expand files/dirs into a sorted, de-duplicated .md list."""
    found: dict[Path, None] = {}
    for p in paths:
        if p.is_dir():
            for md in sorted(p.rglob("*.md")):
                found[md] = None
        elif p.suffix == ".md" and p.exists():
            found[p] = None
    return list(found)


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    if argv:
        targets = [Path(a) for a in argv]
    else:
        # Default gate surface: everything that ships in a ZIP.
        targets = [root / "docs", root / "submission"] + sorted(
            root.glob("*.md")
        )
    files = iter_markdown(targets)
    failures: list[str] = []
    total = 0
    for f in files:
        total += len(extract_blocks(f.read_text(encoding="utf-8")))
        failures.extend(check_file(f))
    if failures:
        print("Mermaid check FAILED — broken diagram(s):")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"Mermaid check OK: {total} diagram(s) in {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
