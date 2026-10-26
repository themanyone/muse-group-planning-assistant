"""Build the B-197 per-code-file atlas from the live tree.

Roadmap B-197 orders an atlas entry for EVERY code file: purpose,
module, public symbols, allowed and forbidden dependencies, data
owned or touched, failure behaviour, tests that cover it, and the
slice that created it — plus a checker that diffs the file tree
against the atlas so an undocumented file fails the release.

This script is that atlas's generator. It derives every entry
from evidence in the tree itself, never from hand-typed claims:

* purpose — the file's own module docstring (the D-26 header the
  B-176 comment gate enforces), first paragraph;
* module — ``gpa.internal.modulemap.SERVICE_MODULES`` for service
  files, the directory family for everything else;
* public symbols — the AST's top-level non-underscore defs,
  classes, and constant assignments;
* dependencies — the AST's import statements, verbatim roots;
* data — ``__tablename__`` values for model files; other layers
  own no tables by the §6A.3 layering law;
* tests — test modules whose text names the file's stem
  (labelled honestly as "tests that name it");
* slice — the first commit that added the file, from
  ``git log --diff-filter=A``, with its B-slice id when the
  commit subject carries one.

Failure behaviour and forbidden dependencies are stated per
LAYER, because they are layer laws (§6A.3), not per-file
inventions; the templates below say exactly that.

Run:  python scripts/build_file_atlas.py
Writes docs/16-per-file-atlas.md. Stdlib only.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "16-per-file-atlas.md"

sys.path.insert(0, str(ROOT / "src"))
try:
    from gpa.internal.modulemap import SERVICE_MODULES
except Exception:  # pragma: no cover - atlas must build standalone
    SERVICE_MODULES = {}

# Layer laws (§6A.3): what each layer may and may not do, and how
# its failures surface. Stated once per layer, applied per file.
LAYER_LAWS: dict[str, tuple[str, str]] = {
    "models": (
        "May import: stdlib, SQLAlchemy, sibling models. "
        "Forbidden: services, routers, schemas, AI, storage.",
        "Import or mapping errors fail at process start; "
        "constraint violations surface as the caller's rollback.",
    ),
    "schemas": (
        "May import: stdlib, pydantic, models (pure helpers only). "
        "Forbidden: services, routers.",
        "Invalid input is refused as a 422 problem+json body by "
        "the validation door before any service runs.",
    ),
    "services": (
        "May import: models, schemas, sibling services through "
        "the pinned architecture inventory, the internal API. "
        "Forbidden: routers; private names of other files.",
        "Domain refusals are AppError problem+json with a "
        "registry code; unexpected errors propagate to the "
        "chassis and roll the transaction back.",
    ),
    "routers": (
        "May import: deps (the only door), schemas, services' "
        "public surface. Forbidden: direct model writes outside "
        "the pinned projection reads; business rules of its own.",
        "Refusals come from the service layer as AppError; the "
        "router itself only shapes the response.",
    ),
    "ai": (
        "May import: stdlib, httpx (live driver only), models for "
        "usage rows. Forbidden: routers; vendor SDKs anywhere.",
        "Model failures raise ModelError in plain words; a "
        "fixture miss is a loud FixtureMissError, never an "
        "invented answer.",
    ),
    "internal": (
        "May import: stdlib, cryptography, models, the audit "
        "writer. Forbidden: routers; payload content in logs or "
        "audit detail.",
        "Refusals are the internal_* registry codes, signed even "
        "on the wire door; dispatch happens in the caller's "
        "transaction or not at all.",
    ),
    "storage": (
        "May import: stdlib, cryptography (encrypted driver). "
        "Forbidden: everything above the data layer.",
        "Writes are atomic (temp + rename); a bad key is refused "
        "by the strict key pattern before any byte moves.",
    ),
    "crypto": (
        "May import: stdlib, cryptography. Forbidden: services "
        "above the primitives, routers.",
        "A seal that does not verify raises in plain words; "
        "opening never creates key material.",
    ),
    "root": (
        "Composition and foundation files: may wire any module's "
        "public surface (reviewed as composition). Forbidden: "
        "business rules that belong in a service.",
        "Startup refusals are plain-words and fatal by design "
        "(prod secret law, half-configured mail); request-time "
        "failures ride the problem+json chassis.",
    ),
    "migrations": (
        "May import: Alembic, SQLAlchemy, the models' metadata "
        "(0001 only). Forbidden: application services.",
        "A failed migration leaves the previous revision "
        "stamped; guarded revisions are idempotent by inspector "
        "checks.",
    ),
    "scripts": (
        "Operator tools: may import the package and stdlib. "
        "Forbidden: shipping in the request path; secrets in "
        "output.",
        "Tools refuse in plain words and exit non-zero; "
        "destructive tools verify before they place or delete.",
    ),
    "tests": (
        "May import: the whole tree, through public surfaces and "
        "fixtures. Forbidden: touching the real network or a "
        "real third-party account.",
        "A failure here is the gate working: the suite is red "
        "until the code or the superseded assertion is fixed.",
    ),
}


def layer_of(rel: str) -> str:
    """The atlas layer for a repo-relative path."""
    parts = rel.split("/")
    if parts[0] == "migrations":
        return "migrations"
    if parts[0] == "scripts":
        return "scripts"
    if parts[0] == "tests":
        return "tests"
    if len(parts) > 2 and parts[2] in LAYER_LAWS:
        return parts[2]
    return "root"


def module_of(rel: str, layer: str) -> str:
    """The owning module: modulemap for services, family else."""
    stem = Path(rel).stem
    if layer == "services":
        return SERVICE_MODULES.get(stem, "UNMAPPED — see B-161 map")
    if layer in ("models", "schemas", "routers"):
        return f"{layer} family of the {stem} domain"
    if layer == "root":
        return "composition / foundation"
    return layer


def first_commit(rel: str) -> str:
    """The slice that created the file, from git's own record."""
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%s",
             "--reverse", "--", rel],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        ).stdout.strip().splitlines()
    except Exception:
        out = []
    if not out:
        return "pre-git or generated (see git history)"
    subject = out[0]
    m = re.search(r"B-\d{3}", subject)
    return f"{m.group(0)} — {subject[:90]}" if m else subject[:90]


def public_symbols(tree: ast.Module) -> list[str]:
    """Top-level public defs, classes, and constants."""
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id.isupper()
                    and not target.id.startswith("_")
                ):
                    names.append(target.id)
    return names


def imports_of(tree: ast.Module) -> list[str]:
    """The file's import roots, verbatim and sorted."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return sorted(found)


def entry(rel: str, test_texts: dict[str, str]) -> str:
    """One atlas entry, derived from the file itself."""
    path = ROOT / rel
    layer = layer_of(rel)
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
        doc = (ast.get_docstring(tree) or "").strip()
    except SyntaxError:
        tree, doc = None, ""
    purpose = " ".join(doc.split("\n\n")[0].split()) if doc else (
        "No header docstring — named in the B-176 comment gate's "
        "gap list; the file's role is its path and symbols below."
    )
    symbols = public_symbols(tree) if tree else []
    shown = ", ".join(f"`{s}`" for s in symbols[:25]) or "—"
    if len(symbols) > 25:
        shown += f" (+{len(symbols) - 25} more)"
    deps = ", ".join(f"`{d}`" for d in imports_of(tree)) if tree else "—"
    tables = re.findall(r'__tablename__\s*=\s*"([^"]+)"', text)
    data = (
        "Owns tables: " + ", ".join(f"`{t}`" for t in tables)
        if tables else
        "Owns no tables (layer law); touches data only through "
        "the models/services named in its dependencies."
    )
    stem = Path(rel).stem
    naming = sorted(
        name for name, body in test_texts.items()
        if stem in body and name != rel
    )
    tests = ", ".join(f"`{n}`" for n in naming[:6]) or (
        "— (no test module names it; covered indirectly or a "
        "fixture/helper)"
    )
    allowed, failure = LAYER_LAWS[layer]
    return (
        f"### `{rel}`\n\n"
        f"- **Purpose:** {purpose}\n"
        f"- **Module:** {module_of(rel, layer)} · **Layer:** {layer}\n"
        f"- **Public symbols:** {shown}\n"
        f"- **Dependencies (imports):** {deps or '—'}\n"
        f"- **Allowed / forbidden:** {allowed}\n"
        f"- **Data:** {data}\n"
        f"- **Failure behaviour:** {failure}\n"
        f"- **Tests that name it:** {tests}\n"
        f"- **Created by:** {first_commit(rel)}\n"
    )


def main() -> None:
    """Collect every code file and write the atlas chapter."""
    files = sorted(
        str(p.relative_to(ROOT))
        for base in ("src", "migrations", "scripts", "tests")
        for p in (ROOT / base).rglob("*.py")
        if "__pycache__" not in p.parts
    )
    test_texts = {
        str(p.relative_to(ROOT)): p.read_text(encoding="utf-8", errors="replace")
        for p in (ROOT / "tests").rglob("*.py")
        if "__pycache__" not in p.parts
    }
    areas: dict[str, list[str]] = {}
    for rel in files:
        areas.setdefault(rel.split("/")[0], []).append(rel)
    parts: list[str] = [HEADER]
    parts.append(
        f"The tree at generation time holds **{len(files)} Python "
        f"code files**: " +
        ", ".join(f"{k} {len(v)}" for k, v in sorted(areas.items())) +
        ". Every one has an entry below; the B-197 checker in "
        "`tests/test_docs_gate.py` re-derives this list from the "
        "live tree and fails, naming the file, if any file lacks "
        "an entry or any entry names a file that no longer "
        "exists.\n"
    )
    for area in ("src", "migrations", "scripts", "tests"):
        parts.append(f"\n## Files under `{area}/`\n")
        for rel in areas.get(area, []):
            parts.append(entry(rel, test_texts))
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"atlas: {len(files)} files -> {OUT.relative_to(ROOT)}")


HEADER = """# Per-code-file atlas

*Roadmap slice B-197. For every code file in the tree: purpose,
module, public symbols with what each is for, allowed and
forbidden dependencies, data it owns or touches, failure
behaviour, tests that cover it, and the slice that created it.
The atlas is the contract behind D-26's comments: the header in
the file and the chapter in the atlas must agree.*

## How this atlas is made — and kept true

This chapter is GENERATED from the live tree by
`scripts/build_file_atlas.py` (stdlib AST + git history), and
is regenerated whenever the tree changes shape. That is the
honest way to keep 390-odd entries true: a hand-maintained list
would drift within a phase. (One entry ages by design: a new
file's "Created by" reads "pre-git" until its creating commit
lands, and the next regeneration corrects it.) Each entry
derives from evidence:

- **Purpose** is the file's own header docstring — the D-26
  comment the B-176 gate enforces — so a disagreement between
  header and atlas is impossible by construction, and a file
  with no header says so in its entry.
- **Public symbols** and **dependencies** are the file's AST:
  top-level public defs/classes/constants, and its imports.
- **Module** is the B-161 module map for service files; other
  layers name their family.
- **Allowed / forbidden dependencies** and **failure
  behaviour** are LAYER laws (§6A.3), stated once per layer in
  the generator and applied to each file — they are not
  per-file inventions.
- **Data** names the tables a model file owns; every other
  layer owns none, by the layering law.
- **Tests that name it** lists test modules whose text names
  the file's stem — an honest proxy, labelled as such; the
  full-suite gate is the real coverage proof.
- **Created by** is the first commit that added the file
  (`git log --diff-filter=A`), with its slice id when the
  commit subject carries one.

## The checker (B-197 acceptance)

```mermaid
flowchart LR
  T[Live file tree] --> G[scripts/build_file_atlas.py]
  G --> A[docs/16-per-file-atlas.md]
  T --> C{test_docs_gate checker}
  A --> C
  C -->|every file has an entry, no stale entries| P[Release may proceed]
  C -->|a file is missing or stale| F[Gate fails, naming the file]
```

The walking tour: the live tree feeds both the generator,
which writes this atlas, and the docs-gate checker, which
independently re-lists the tree and diffs it against the
entries below. Agreement lets a release proceed; any missing
or stale entry fails the gate naming the exact file — the
same by-file discipline as the Mermaid gate (B-181) and the
model checker (B-192).

Scope honesty: the atlas covers every Python file under
`src/`, `migrations/`, `scripts/`, and `tests/`. Non-Python
code (shell entry points `run.sh`/`test.sh`/`demo.sh`, the
`deploy/` units, Jinja templates, AI prompt templates) is
documented by its owning chapter — deployment (B-199),
durability (B-195), AI pipeline (B-193) — and the prompt
templates carry their own name+version front-matter under
the B-038 discipline.
"""


if __name__ == "__main__":
    main()
