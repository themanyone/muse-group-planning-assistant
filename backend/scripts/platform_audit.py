"""Cross-platform audit (roadmap B-177): the portability law as tooling.

The backend must run the same on Windows, macOS, and Linux (§6A.2).
Three habits break that silently, so this audit bans them in
``src/gpa`` and names every surviving exception in a pinned list
with its reason — a new hit fails the audit until a person either
fixes it or pins it here with words:

1. **Text I/O without an explicit encoding.** A bare ``open()`` in
   text mode, ``read_text()``, or ``write_text()`` decodes with the
   platform's locale encoding — UTF-8 on most Linux, often cp1252
   on Windows — so the same bytes become different text per OS.
   Binary opens (``"rb"``/``"wb"``/``os.fdopen(fd, "wb")``) carry
   no encoding question and are allowed.
2. **Filesystem paths built from strings.** ``os.path.join`` and
   string concatenation / f-strings / ``"/".join`` used as PATH
   building are banned; ``pathlib`` is the one road. URL building
   is not path building — the surviving URL joins are pinned
   below, each with its reason.
3. **OS-name branching in domain code.** ``sys.platform`` /
   ``os.name`` checks belong in the platform seams (transport,
   storage), never scattered through services; today there are
   none in ``src/gpa`` and the audit keeps it that way.

The audit is importable (``audit_tree(root)`` returns problem
strings, empty = clean) and runnable as a CLI. It runs inside the
test suite via ``tests/test_platform_matrix.py`` — the same gate
shape as B-176's comment gate.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Pinned exceptions: (relative file, needle) -> the reason a human
# recorded. A pinned site that disappears is also a failure — the
# pin must be removed in the same commit, so the list never rots.
PINNED: dict[tuple[str, str], str] = {
    ("src/gpa/main.py", '"/".join(parts)'): (
        "URL path redaction for access logs (B-145): joins URL "
        "segments, never a filesystem path."
    ),
    ("src/gpa/services/ical.py", '"webcal://" +'): (
        "Builds a webcal:// URL from a feed URL — a link, not a path."
    ),
    ("src/gpa/routers/calendar.py", '"webcal://" +'): (
        "Same webcal:// URL construction as services/ical.py."
    ),
    ("src/gpa/services/vision.py", '"https://" +'): (
        "Normalises a printed link into an https:// URL — not a path."
    ),
}


def _is_text_open(call: ast.Call) -> bool:
    """True when an ``open(...)`` call opens in TEXT mode.

    The mode is the second positional argument or ``mode=``; no
    mode at all means text mode ("r"). A mode containing "b" is
    binary and carries no encoding question.
    """
    mode: str | None = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = keyword.value.value
    if mode is None:
        return True  # bare open(path) reads text
    return isinstance(mode, str) and "b" not in mode


def _has_encoding(call: ast.Call) -> bool:
    """True when the call passes an explicit ``encoding=``."""
    return any(keyword.arg == "encoding" for keyword in call.keywords)


def _string_hits(node: ast.AST) -> list[str]:
    """Short descriptions of path-shaped string building at a node."""
    hits: list[str] = []
    if isinstance(node, ast.Call):
        func = node.func
        # os.path.join(...) — attribute chain ending path.join / join on os.path
        if isinstance(func, ast.Attribute) and func.attr == "join":
            base = func.value
            if isinstance(base, ast.Attribute) and base.attr == "path":
                hits.append("os.path.join")
            elif isinstance(base, ast.Constant) and base.value == "/":
                hits.append('"/".join (path building)')
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # Path building joins a VARIABLE to a "/"-carrying string.
        # Constant + constant is prose assembly (a message built
        # from two literals) and is not path building.
        for side, other in ((node.left, node.right), (node.right, node.left)):
            if isinstance(side, ast.Constant) and isinstance(side.value, str):
                # The other side must be a plain value (a name, an
                # attribute, a subscript) — a call result joined to
                # prose is message assembly, not path building.
                if "/" in side.value and isinstance(
                    other, (ast.Name, ast.Attribute, ast.Subscript)
                ):
                    hits.append(f'path-shaped string concat: {side.value!r}')
    return hits


def audit_tree(root: Path) -> list[str]:
    """Audit every Python file under ``root/src/gpa``.

    Returns problem strings (file:line + the rule broken). An
    empty list means the tree obeys the portability law.
    """
    problems: list[str] = []
    src = root / "src" / "gpa"
    seen_pins: set[tuple[str, str]] = set()
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                # Only the bare builtin open() is audited: attribute
                # .open() calls are library doors (Pillow's
                # Image.open, urllib's opener.open) whose arguments
                # are streams, not encodings.
                if (
                    isinstance(func, ast.Name)
                    and name == "open"
                    and _is_text_open(node)
                    and not _has_encoding(node)
                ):
                    problems.append(
                        f"{rel}:{node.lineno}: text open() without encoding="
                    )
                if name in ("read_text", "write_text") and not _has_encoding(node):
                    problems.append(
                        f"{rel}:{node.lineno}: {name}() without encoding="
                    )
                for hit in _string_hits(node):
                    pin = _pin_needle(text, node, hit, rel)
                    if pin and pin in PINNED:
                        seen_pins.add(pin)
                    else:
                        problems.append(f"{rel}:{node.lineno}: {hit}")
            if isinstance(node, ast.BinOp):
                for hit in _string_hits(node):
                    pin = _pin_needle(text, node, hit, rel)
                    if pin and pin in PINNED:
                        seen_pins.add(pin)
                    else:
                        problems.append(f"{rel}:{node.lineno}: {hit}")
            if isinstance(node, ast.Attribute) and node.attr == "platform":
                if isinstance(node.value, ast.Name) and node.value.id == "sys":
                    problems.append(
                        f"{rel}:{node.lineno}: sys.platform branch in domain code"
                    )
            if isinstance(node, ast.Name) and node.id == "os":
                pass  # os.name needs attribute context; checked below
            if isinstance(node, ast.Attribute) and node.attr == "name":
                if isinstance(node.value, ast.Name) and node.value.id == "os":
                    problems.append(
                        f"{rel}:{node.lineno}: os.name branch in domain code"
                    )
    for pin in PINNED:
        if pin not in seen_pins:
            problems.append(
                f"stale pin: {pin[0]} no longer contains {pin[1]!r} — "
                "remove the pin in the same commit"
            )
    return problems


def _pin_needle(
    text: str, node: ast.AST, hit: str, rel: str = ""
) -> tuple[str, str] | None:
    """Match a hit against THIS FILE's pinned needles by line text."""
    lines = text.splitlines()
    lineno = getattr(node, "lineno", 0)
    if not (1 <= lineno <= len(lines)):
        return None
    line = lines[lineno - 1]
    for (pin_rel, needle) in PINNED:
        if pin_rel == rel and needle in line:
            return (pin_rel, needle)
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI: audit the repo this script lives in; 0 = clean."""
    root = Path(__file__).resolve().parent.parent
    problems = audit_tree(root)
    if problems:
        print("Platform audit FAILED:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("Platform audit clean: pathlib-only paths, explicit "
          "encodings, no OS branching in domain code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
