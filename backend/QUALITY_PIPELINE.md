# Production-quality pipeline — Group Planning Assistant backend (B-178)

One pipeline, three doors, all running the same checks: the
test suite (`./test.sh`), CI (`.github/workflows/test.yml`),
and the pre-commit hooks (`.pre-commit-config.yaml`). A fresh
checkout reproduces CI with `uv sync --extra dev && ./test.sh`
plus `uv run python scripts/quality_gate.py --mypy`.

## What is enforced today

| Check | Door | State |
| --- | --- | --- |
| pytest suite (incl. B-176 comment gate, B-177 platform audit) | test.sh / CI / suite | enforced |
| ruff lint — E9 + F (syntax + pyflakes) on `src/gpa` | `scripts/quality_gate.py`, in-suite | **enforced, 0 findings** |
| mypy `--strict` on `services/`, `ai/`, `internal/`, `crypto/` | quality gate `--mypy`, CI | **ratchet: ≤ 722** |

## The ratchet (the B-176 pattern, honestly recorded)

The roadmap asks for mypy strict compliance and the full ruff
rule set. At this landing the tree is not there, and pretending
otherwise would be a lie in the papers:

- **mypy strict: 722 errors** over the four named trees
  (measured 2026-10-25, after the lint fixes; 723 before).
  Dominated by `type-arg` (391: untyped `dict` payloads — the
  internal-API and JSON-draft shapes) and `no-untyped-def`
  (171). The gate's ceiling is 722 and may only fall; lowering
  it in the same commit that lowers the count is the expected
  motion, exactly as B-176's documentation floors only rise.
- **ruff wider families: 1,189 findings** under the default+
  extended sets, dominated by B008 (945 — FastAPI's
  `Depends()`-in-defaults idiom, a framework pattern, not a
  defect class this codebase will chase), UP017 (61),
  unsorted imports (46). The enforced E9+F core was brought to
  zero at root: 33 findings fixed, including one undefined-name
  annotation (`InternalResult`, now imported at the top of
  `internal/client.py`) and several dead imports; one
  load-bearing re-export (`services.events.etag_of`, called by
  routers) was restored with its noqa after the autofix removed
  it — the regression batch below proves the tree still runs.
- **ruff format: NOT enforced.** 137 files would be
  reformatted; the tree's hand-wrapped, comment-heavy format
  (D-26) predates the formatter, and a wholesale reformat
  would bury reviewable history. Named deviation, revisit if
  the team ever votes a reformat day.

## Coverage

`pytest-cov` is a locked dev dependency and `[tool.coverage]`
ships in pyproject. Per-module floors are measured from the
Phase 17 full-gate run (the B-180 drill's suite) and will be
recorded here with that run's numbers — no floor is stated
before it is measured. Policy from the roadmap: the security
and durability suites are excluded from any coverage average;
they must be complete, not averaged.
