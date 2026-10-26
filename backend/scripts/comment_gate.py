"""Comment + documentation gate (roadmap B-176, standard §6A.1).

What this file is for
---------------------
§6A.1 says the code must read like a book: every file opens with a
purpose header, every module / class / function carries a docstring
saying what it does and why, every migration and prompt template
carries its why, and ``.env.example`` is a commented document rather
than a bare key list.  This script is that standard as tooling — an
AST-based checker with no dependencies beyond the standard library,
so a fresh checkout can run it before anything is installed.

What the gate enforces (and what it deliberately does not)
----------------------------------------------------------
Hard rules — a single breach fails the gate:

* every Python file under ``src/gpa``, ``migrations/versions`` and
  ``scripts`` opens with a module docstring or a ``#`` purpose
  header (the docstring IS the header in this codebase's style);
* every migration carries a module docstring — a migration without
  its why-comment is a schema change nobody can audit later;
* every prompt template (``src/gpa/ai/prompts/*.md``) carries the
  front-matter header naming it and pinning its version — editing a
  prompt without a version bump is exactly what B-038 forbids, and
  a template with no header at all is outside that discipline;
* every setting in ``.env.example`` sits in a block that a comment
  introduces — a setting nobody explained is a setting nobody can
  safely change;
* no docstring may merely restate the name it documents
  (``def add_one`` documented as "add one" counts AGAINST the
  standard in §6A.1's words, so the checker refuses it outright).

Ratchet floors — a module dropping below its floor fails the gate:

* per-module docstring coverage for functions and for classes, and
  per-module documentation density (docstring lines + comment
  lines, as a share of all lines).  The floors in ``FLOORS`` below
  were measured on the tree the day this gate landed (B-176,
  2026-10-25) and rounded DOWN to the nearest five points: they are
  a ratchet, not a target.  They may only ever rise, in the same
  commit that raises a module's real coverage.  §6A.1 is explicit
  that the gate "enforces presence and floor; human review enforces
  quality" — 100% per-function coverage remains the standard the
  floors climb toward, and every individual missing docstring is
  still REPORTED, by file and line, in the gate's gap list so the
  climb is visible work rather than a hidden debt.

How it is run
-------------
``python scripts/comment_gate.py`` from the repo root checks the
real tree and exits 1 on any hard breach or floor drop.  The same
check runs inside ``test.sh`` through ``tests/test_comment_gate.py``
(test.sh is the pytest suite, so a gate test IS the gate running
there).  ``check_tree(root)`` is importable so the tests can point
the gate at a deliberately stripped fixture module and watch it
fail, by file and line, exactly as B-176's acceptance requires.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# The ratchet floors (see the module docstring: measured at B-176,
# rounded down to the nearest five points, and only ever rising).
# Each entry maps a module name to (function-docstring %, class-
# docstring %, documentation-density %).  The module name is the
# first path part under src/gpa ("(top)" for the package's own
# files), or the tree's own name for migrations / scripts.
# ---------------------------------------------------------------------------
FLOORS: dict[str, tuple[int, int, int]] = {
    "(top)": (60, 95, 30),
    "ai": (60, 95, 25),
    "crypto": (70, 0, 30),
    "internal": (60, 95, 25),
    "models": (95, 95, 40),
    "routers": (85, 60, 15),
    "schemas": (55, 70, 20),
    "services": (75, 95, 20),
    "storage": (60, 95, 30),
    "migrations": (10, 0, 15),
    "scripts": (40, 0, 15),
}

# Trees the gate walks, relative to the repo root.  Tests are NOT
# walked as production code — §6A.1 asks tests to read as
# specifications, which their own module headers already do, and
# folding 100 test files into the density average would let test
# bulk mask a thinning service module (the B-178 concern, applied
# early: floors are per module precisely so nothing averages away).
PY_TREES = ("src/gpa", "migrations/versions", "scripts")


@dataclass
class Gap:
    """One missing docstring, named by file and line.

    Gaps are reported, not gate-fatal on their own (the floors are
    the fatal form of the same fact) — except that a stripped
    fixture produces gaps AND a floor breach together, so the
    acceptance test can assert both the failure and its address.
    """

    path: str
    line: int
    name: str
    kind: str  # "function" or "class"


@dataclass
class Report:
    """The gate's verdict over one tree.

    ``violations`` holds the hard breaches and floor drops, each a
    plain-words sentence naming its file or module.  ``gaps`` holds
    every individual missing docstring.  ``modules`` holds the
    measured per-module numbers so a report can show its working.
    """

    violations: list[str] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    modules: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """The gate passes exactly when nothing hard was breached."""
        return not self.violations


def _docstring_span(node: ast.AST) -> int:
    """How many source lines a node's docstring occupies (0 if none).

    Density must count the words, not just their presence — a
    one-word docstring and a ten-line explanation both "have a
    docstring", and §6A.1's floor is about the reading, not the
    tick-box.
    """
    body = getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return (body[0].end_lineno or body[0].lineno) - body[0].lineno + 1
    return 0


def _module_name(root: Path, path: Path) -> str:
    """The gate-module a file belongs to (see FLOORS' comment)."""
    parts = path.relative_to(root).parts
    if parts[:2] == ("src", "gpa"):
        return parts[2] if len(parts) > 3 else "(top)"
    if parts[0] == "migrations":
        return "migrations"
    return parts[0]


def _check_python_file(
    root: Path, path: Path, report: Report, stats: dict[str, dict[str, float]]
) -> None:
    """Apply the per-file rules to one Python file.

    A file that does not parse is a violation in itself: a gate
    that silently skipped unparseable code would certify a tree it
    never actually read.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    rel = str(path.relative_to(root))
    module = _module_name(root, path)
    bucket = stats.setdefault(
        module,
        {"lines": 0, "doc_lines": 0, "funcs": 0, "funcs_doc": 0,
         "classes": 0, "classes_doc": 0},
    )
    bucket["lines"] += len(lines)
    bucket["doc_lines"] += sum(1 for ln in lines if ln.strip().startswith("#"))
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        report.violations.append(f"{rel}: does not parse ({exc.msg}, line {exc.lineno})")
        return
    bucket["doc_lines"] += _docstring_span(tree)

    # Hard rule 1: a purpose header — the module docstring, or a
    # leading comment block for the rare file whose style is a
    # comment header (scripts run under a shebang count too).
    stripped = text.lstrip("\ufeff \t\r\n")
    if not ast.get_docstring(tree) and not stripped.startswith("#"):
        report.violations.append(
            f"{rel}:1: no purpose header (module docstring or opening comment)"
        )
    # Hard rule 2 (migrations): the why-comment is not optional —
    # a comment header does NOT satisfy it, the docstring that
    # travels with the revision metadata is the auditable record.
    if module == "migrations" and not ast.get_docstring(tree):
        report.violations.append(
            f"{rel}:1: migration carries no docstring saying what it changes and why"
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind, tally, tally_doc = "function", "funcs", "funcs_doc"
        elif isinstance(node, ast.ClassDef):
            kind, tally, tally_doc = "class", "classes", "classes_doc"
        else:
            continue
        bucket[tally] += 1
        doc = ast.get_docstring(node)
        bucket["doc_lines"] += _docstring_span(node)
        if doc:
            bucket[tally_doc] += 1
            # Hard rule 3: a docstring that merely restates the
            # name ("add_one" -> "add one.") is banned by §6A.1 —
            # it occupies the place an explanation should live.
            words = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in doc.lower())
            if " ".join(words.split()) == node.name.replace("_", " "):
                report.violations.append(
                    f"{rel}:{node.lineno}: docstring of {node.name} merely restates its name"
                )
        else:
            report.gaps.append(Gap(rel, node.lineno, node.name, kind))


def _check_prompts(root: Path, report: Report) -> None:
    """Hard rule: every prompt template carries name + version.

    The templates live under src/gpa/ai/prompts; a tree without
    that directory (a fixture) simply has no prompts to check.
    The front matter is the ``---`` block B-038's manifest pins —
    name and version inside it are what make an edit without a
    version bump visible.
    """
    prompt_dir = root / "src" / "gpa" / "ai" / "prompts"
    if not prompt_dir.is_dir():
        return
    for path in sorted(prompt_dir.glob("*.md")):
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text.split("---", 2)
        front = head[1] if len(head) > 2 and text.lstrip().startswith("---") else ""
        for key in ("name:", "version:"):
            if key not in front:
                report.violations.append(
                    f"{rel}:1: prompt template header is missing its {key[:-1]} line"
                )


def _check_env_example(root: Path, report: Report) -> None:
    """Hard rule: every .env.example setting is comment-introduced.

    Settings are grouped in blocks separated by blank lines; the
    rule is that a block containing a setting also contains a
    comment BEFORE its first setting — the explanation must lead,
    not trail.  A fixture tree without the file has nothing here.
    """
    path = root / ".env.example"
    if not path.is_file():
        return
    block_has_comment = False
    block_has_setting = False

    def flush(lineno: int) -> None:
        """Judge one finished block (see the grouping rule above)."""
        if block_has_setting and not block_has_comment:
            report.violations.append(
                f".env.example:{lineno}: a setting block carries no introducing comment"
            )

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for idx, line in enumerate(lines, start=1):
        if not line.strip():
            flush(idx)
            block_has_comment = block_has_setting = False
        elif line.lstrip().startswith("#"):
            if not block_has_setting:
                block_has_comment = True
        elif "=" in line:
            block_has_setting = True
    flush(len(lines))


def check_tree(root: Path) -> Report:
    """Run the whole gate over the repo tree rooted at ``root``.

    Returns the Report; the caller decides what a failure means
    (the CLI exits 1, the test asserts).  Per-module floors are
    judged after every file is read, from the measured numbers —
    never from a cached table — so the gate cannot certify a tree
    it did not just measure.
    """
    root = Path(root)
    report = Report()
    stats: dict[str, dict[str, float]] = {}
    for tree_name in PY_TREES:
        base = root / tree_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            _check_python_file(root, path, report, stats)
    _check_prompts(root, report)
    _check_env_example(root, report)

    for module, bucket in sorted(stats.items()):
        funcs = bucket["funcs"]
        classes = bucket["classes"]
        entry = {
            "func_cov": 100.0 * bucket["funcs_doc"] / funcs if funcs else 100.0,
            "class_cov": 100.0 * bucket["classes_doc"] / classes if classes else 100.0,
            "density": 100.0 * bucket["doc_lines"] / bucket["lines"] if bucket["lines"] else 0.0,
        }
        report.modules[module] = entry
        floors = FLOORS.get(module)
        if floors is None:
            report.violations.append(
                f"module {module!r} has no ratchet floor in the comment gate — "
                "a new module must be measured and floored in the commit that adds it"
            )
            continue
        func_floor, class_floor, density_floor = floors
        if entry["func_cov"] < func_floor:
            report.violations.append(
                f"module {module}: function docstring coverage "
                f"{entry['func_cov']:.0f}% is below its ratchet floor {func_floor}%"
            )
        if entry["class_cov"] < class_floor:
            report.violations.append(
                f"module {module}: class docstring coverage "
                f"{entry['class_cov']:.0f}% is below its ratchet floor {class_floor}%"
            )
        if entry["density"] < density_floor:
            report.violations.append(
                f"module {module}: documentation density "
                f"{entry['density']:.0f}% is below its ratchet floor {density_floor}%"
            )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry: print the measured table, the gaps, the verdict.

    Exit 0 = the gate passes; exit 1 = it does not, with every
    breach named.  ``--root`` exists so drills and the test suite
    can point the gate at a copied or fixture tree.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[args.index("--root") + 1]) if "--root" in args else Path(__file__).resolve().parent.parent
    report = check_tree(root)
    print("Comment + documentation gate (B-176) — measured modules:")
    for module, entry in report.modules.items():
        print(
            f"  {module:12s} functions {entry['func_cov']:5.1f}%  "
            f"classes {entry['class_cov']:5.1f}%  density {entry['density']:5.1f}%"
        )
    if report.gaps:
        print(f"{len(report.gaps)} individual docstring gaps (reported, floor-judged):")
        for gap in report.gaps[:40]:
            print(f"  {gap.path}:{gap.line}: {gap.kind} {gap.name} has no docstring")
        if len(report.gaps) > 40:
            print(f"  … and {len(report.gaps) - 40} more")
    if report.ok:
        print("GATE PASSES — every hard rule holds and no module is below its floor.")
        return 0
    print("GATE FAILS:")
    for violation in report.violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
