"""The architecture checker — import-linter style rules (B-161).

§6A.3's boundary law, made mechanical. This module parses every
Python file under ``src/gpa`` with the standard-library AST (no
imports are executed, so checking is fast and side-effect free)
and reports violations of four rule families:

``layer``
    The layering of §3 may only lean one way. Routers never
    import models (the B-017 law: deps and services are the only
    doors). Models import nothing above the data layer. Schemas
    never import services or routers. Nothing anywhere imports
    a router — HTTP is the outermost skin, never an ingredient.

``private``
    A name with a leading underscore belongs to its file. No
    file may import another file's private names, in any layer:
    a helper two modules need is promoted to a public name in
    its owning module, or moved to a shared module — it is never
    reached for through the underscore door.

``inventory``
    Cross-module service edges — a services file importing a
    DIFFERENT module's service file, a services file importing
    the ai package, an ai file importing a service — must be
    exactly the pinned set the architecture test carries. The
    set may only shrink (as B-162's internal API absorbs an
    operation, its old direct edge leaves the pin in the same
    commit). A new edge, or a stale pin for an edge that no
    longer exists, both fail: the border is a deliberate act,
    never drift. Composition roots (main, worker, arq_worker,
    deps) and the internal package itself are exempt — wiring
    modules together is their stated job. The shared common
    modules are exempt as targets: audit (the same-transaction
    writer every module uses, B-019) and crypto (the sealing
    primitives, B-127/B-167) are imported the way errors is —
    their public functions ARE their surface (§6A.3 names them
    common modules). Router → model edges are pinned the same
    way: a handful of routers carry small projection fetches
    (B-140's members-only job view among them); the pin means
    that set can shrink but never silently grow. (B-161's scan
    is the evidence; it corrects the older, narrower B-017
    note that no router imports any model.)

``unmapped``
    Every services file must appear in the module map. A new
    service file is a module decision, taken in the open.

The checker is used two ways: the architecture test runs it over
the real tree, and the same test runs it over a deliberately
violating fixture tree to prove the rules bite (B-161's
acceptance). It returns violations as data; it never raises on
a violation — a red report is a result, not a crash.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from gpa.internal import modulemap


@dataclass(frozen=True)
class Edge:
    """One import statement, located.

    ``imported`` is the full dotted module named by the import;
    ``names`` carries the imported symbols for ``from`` imports
    (empty for a plain ``import x.y`` statement).
    """

    importer: str  # path relative to the scanned root
    imported: str  # dotted module, e.g. "gpa.services.notifications"
    names: tuple[str, ...]
    lineno: int


@dataclass(frozen=True)
class Violation:
    """One broken rule, located precisely enough to fix."""

    rule: str
    path: str
    lineno: int
    message: str


@dataclass
class ScanResult:
    """Everything one tree scan found: files, edges, violations."""

    files: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)


def _dotted_for(relpath: str) -> str:
    """The dotted module name a file under src/ implements."""
    parts = relpath.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts[1:])  # drop the leading "src"


def _resolve_from(importer_rel: str, module: str | None, level: int) -> str:
    """Resolve an ImportFrom's module to an absolute dotted name.

    The codebase writes absolute ``gpa.…`` imports everywhere;
    relative imports are resolved against the importer's package
    anyway, so a stray one is checked rather than skipped.
    """
    if level == 0:
        return module or ""
    package = _dotted_for(importer_rel).split(".")
    if importer_rel.endswith("__init__.py"):
        base = package
    else:
        base = package[:-1]
    if level > 1:
        base = base[: -(level - 1)] if level - 1 <= len(base) else []
    if module:
        base = base + module.split(".")
    return ".".join(base)


def scan_imports(root: Path) -> tuple[list[str], list[Edge]]:
    """Parse every Python file under ``root/src/gpa``.

    Returns (files, edges): the file list is every ``.py`` path
    relative to ``root``, the edges are every import statement
    found, in file order. Files that fail to parse are reported
    as edges-free but still listed — a syntax error is the test
    suite's problem to name, and it will.

    One normalisation matters: ``from gpa.services import
    notifications`` names a SUBMODULE, while ``from
    gpa.services.events import etag_of`` names a symbol. The
    scanner knows the tree's own module set (pass one), so a
    from-import whose target + name is itself a module in the
    tree becomes an edge to that module; anything else is a
    symbol of the named module.
    """
    base = root / "src" / "gpa"
    paths = sorted(base.rglob("*.py"))
    files: list[str] = [p.relative_to(root).as_posix() for p in paths]
    known_modules = {_dotted_for(rel) for rel in files}
    edges: list[Edge] = []
    for path, rel in zip(paths, files):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.append(Edge(rel, alias.name, (), node.lineno))
            elif isinstance(node, ast.ImportFrom):
                imported = _resolve_from(rel, node.module, node.level)
                symbols: list[str] = []
                for alias in node.names:
                    candidate = f"{imported}.{alias.name}" if imported else alias.name
                    if candidate in known_modules:
                        edges.append(Edge(rel, candidate, (), node.lineno))
                    else:
                        symbols.append(alias.name)
                if symbols or not node.names:
                    edges.append(Edge(rel, imported, tuple(symbols), node.lineno))
    return files, edges


def _classify(dotted: str) -> tuple[str, str | None, str | None]:
    """(layer, module, file stem) for a dotted gpa module name.

    Non-gpa imports (stdlib, installed packages) classify as
    layer ``external`` — the boundary law governs only our own
    tree's shape, never what a file takes from its environment.
    """
    parts = dotted.split(".")
    if parts[0] != "gpa":
        return "external", None, None
    if len(parts) == 1:
        return "root", None, None
    second = parts[1]
    stem = parts[2] if len(parts) > 2 else "__init__"
    if second == "services":
        return "services", modulemap.service_module(stem), stem
    if second in ("models", "schemas", "routers"):
        # The module is the file's domain family, keyed by the
        # same stem conventions the services map uses.
        return second, stem, stem
    if second == "ai":
        return "ai", "ai", stem
    if second == "storage":
        return "storage", "storage", stem
    if second == "internal":
        return "internal", "internal", stem
    if second == "mcp":
        # The MCP surface (B-204) is its own module: it may
        # speak through the internal API (exempt, like every
        # internal edge) and the foundation files, and its
        # defining law — no service, router, or model imports —
        # is pinned by the MCP core test's own import scan.
        return "mcp", "mcp", stem
    return "root", None, second


def _importer_class(relpath: str) -> tuple[str, str | None]:
    """(layer, module) for the importing file itself."""
    layer = modulemap.layer_of(relpath)
    stem = Path(relpath).stem
    if layer == "services":
        return layer, modulemap.service_module(stem)
    if layer == "ai":
        return layer, "ai"
    if layer == "storage":
        return layer, "storage"
    if layer == "internal":
        return layer, "internal"
    if layer == "mcp":
        return layer, "mcp"
    return layer, stem


def edge_key(edge: Edge) -> tuple[str, str]:
    """The inventory key of one edge: (importer file, module)."""
    return (edge.importer, edge.imported)


def cross_module_edges(edges: list[Edge]) -> set[tuple[str, str]]:
    """The service inventory: cross-module edges the law pins.

    An edge is pinned when a services file imports a DIFFERENT
    module's service file or the ai package, or an ai file
    imports a service file. Same-module edges (extraction's
    files importing each other, say) are a module's own
    business. Composition roots and the internal package are
    exempt by design (see modulemap), and the shared common
    modules — audit and crypto — are exempt as targets: every
    module writes its audit rows in its own transaction through
    the one writer, and sealing is a primitive, not a behaviour.
    """
    keys: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.importer in modulemap.COMPOSITION_FILES:
            continue
        i_layer, i_module = _importer_class(edge.importer)
        t_layer, t_module, _stem = _classify(edge.imported)
        if i_layer == "internal" or t_layer == "internal":
            continue  # the SDK door is always open, both ways
        if t_module in modulemap.SHARED_TARGET_MODULES:
            continue  # audit + crypto: common modules, open surface
        if i_layer == "services" and t_layer in ("services", "ai"):
            if t_module != i_module:
                keys.add(edge_key(edge))
        elif i_layer == "ai" and t_layer == "services":
            keys.add(edge_key(edge))
    return keys


def router_model_edges(edges: list[Edge]) -> set[tuple[str, str]]:
    """The router inventory: (router file, models module) pairs.

    Routers reach data through services and deps — except for
    the pinned projection fetches the tree legitimately carries
    (see the module docstring). The pin is exact, both ways.
    """
    keys: set[tuple[str, str]] = set()
    for edge in edges:
        i_layer, _i_module = _importer_class(edge.importer)
        t_layer, _t_module, _stem = _classify(edge.imported)
        if i_layer == "routers" and t_layer == "models":
            keys.add(edge_key(edge))
    return keys


def check_tree(
    root: Path,
    pinned: set[tuple[str, str]],
    pinned_router_models: set[tuple[str, str]] | None = None,
) -> ScanResult:
    """Run every rule over the tree at ``root``.

    ``pinned`` is the exact cross-module service-edge inventory
    the caller (the architecture test) declares legal, and
    ``pinned_router_models`` the exact router → model inventory.
    Returns the scan with all violations attached.
    """
    pinned_router_models = pinned_router_models or set()
    files, edges = scan_imports(root)
    result = ScanResult(files=files, edges=edges)
    violations = result.violations

    # unmapped: every services file must be placed in the map.
    for rel in files:
        if modulemap.layer_of(rel) == "services":
            stem = Path(rel).stem
            if stem == "__init__":
                continue  # the package marker is not a service file
            if modulemap.service_module(stem) is None:
                violations.append(
                    Violation(
                        "unmapped",
                        rel,
                        1,
                        f"services/{stem}.py is not in the module map; "
                        "placing it is a deliberate module decision",
                    )
                )

    for edge in edges:
        i_layer, _i_module = _importer_class(edge.importer)
        t_layer, _t_module, _stem = _classify(edge.imported)
        if t_layer == "external":
            continue
        where = f"{edge.importer} -> {edge.imported}"

        # layer rules: the one-way lean of §3. (Router → model
        # imports are governed by the pinned inventory below,
        # not a blanket ban — see the module docstring.)
        if i_layer == "models" and t_layer in ("services", "routers", "ai", "internal"):
            violations.append(
                Violation(
                    "layer",
                    edge.importer,
                    edge.lineno,
                    f"model imports upward ({where}); the data layer "
                    "depends on nothing above it",
                )
            )
        if i_layer == "schemas" and t_layer in ("services", "routers"):
            violations.append(
                Violation(
                    "layer",
                    edge.importer,
                    edge.lineno,
                    f"schema imports {t_layer} ({where}); wire shapes "
                    "depend on models and schemas only",
                )
            )
        if (
            t_layer == "routers"
            and i_layer != "routers"
            and edge.importer not in modulemap.COMPOSITION_FILES
        ):
            # Composition roots (main.py above all) MOUNT the
            # routers — that is their job, not an ingredient use.
            violations.append(
                Violation(
                    "layer",
                    edge.importer,
                    edge.lineno,
                    f"non-router imports a router ({where}); HTTP is "
                    "the outermost layer and is never an ingredient",
                )
            )
        if i_layer == "storage" and t_layer in ("services", "ai", "routers"):
            violations.append(
                Violation(
                    "layer",
                    edge.importer,
                    edge.lineno,
                    f"storage driver imports upward ({where}); drivers "
                    "know config and models, never services",
                )
            )

        # private rules: an underscore name belongs to its file.
        target_dotted = _dotted_for(edge.importer)
        if edge.imported != target_dotted:
            for name in edge.names:
                if name.startswith("_") and not name.startswith("__"):
                    violations.append(
                        Violation(
                            "private",
                            edge.importer,
                            edge.lineno,
                            f"imports private name {name} from "
                            f"{edge.imported}; promote it in its owning "
                            "module or move it to a shared module",
                        )
                    )
            last = edge.imported.split(".")[-1]
            if last.startswith("_") and not last.startswith("__") and edge.names == ():
                violations.append(
                    Violation(
                        "private",
                        edge.importer,
                        edge.lineno,
                        f"imports private module {edge.imported}; "
                        "private modules belong to their own package",
                    )
                )

    # inventory rules: the pinned set must match reality exactly.
    actual = cross_module_edges(edges)
    for key in sorted(actual - pinned):
        violations.append(
            Violation(
                "inventory",
                key[0],
                0,
                f"new cross-module edge {key[0]} -> {key[1]} is not "
                "pinned; route the call through the internal API or "
                "pin it deliberately with a recorded reason",
            )
        )
    for key in sorted(pinned - actual):
        violations.append(
            Violation(
                "inventory",
                key[0],
                0,
                f"pinned edge {key[0]} -> {key[1]} no longer exists; "
                "remove the stale pin in the same commit that "
                "removed the edge",
            )
        )

    # The router → model inventory is pinned exactly, both ways.
    actual_rm = router_model_edges(edges)
    for key in sorted(actual_rm - pinned_router_models):
        violations.append(
            Violation(
                "inventory",
                key[0],
                0,
                f"new router -> model edge {key[0]} -> {key[1]} is "
                "not pinned; reach the data through a service, or "
                "pin the projection fetch deliberately",
            )
        )
    for key in sorted(pinned_router_models - actual_rm):
        violations.append(
            Violation(
                "inventory",
                key[0],
                0,
                f"pinned router -> model edge {key[0]} -> {key[1]} "
                "no longer exists; remove the stale pin",
            )
        )
    return result
