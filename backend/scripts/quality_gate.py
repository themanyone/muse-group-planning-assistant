"""Production-quality pipeline gate (roadmap B-178).

One door for the quality checks that are not the test suite
itself, mirroring CI (.github/workflows/test.yml) and the
pre-commit hooks (.pre-commit-config.yaml):

1. **ruff lint** — the enforced rule set (pyproject
   ``[tool.ruff.lint]``: E9 syntax + F pyflakes, the correctness
   core) must pass on ``src/gpa`` with zero findings. This set
   was brought to green at the B-178 landing (33 findings fixed
   at root, one load-bearing re-export restored with its noqa).
2. **mypy strict, as a ratchet** — ``mypy --strict`` runs over
   ``services/``, ``ai/``, ``internal/``, ``crypto/`` (the
   roadmap's named trees). The tree is NOT strict-clean yet:
   722 findings stood at the landing, dominated by untyped
   ``dict`` payloads (391 ``type-arg``) and untyped defs (171).
   The gate's law is the B-176 pattern: the count may only
   FALL. ``MYPY_STRICT_CEILING`` records the landing count;
   a run above it fails, and lowering the ceiling in the same
   commit that lowers the count is the expected motion.
3. **ruff format** — NOT enforced: the tree's house format
   (D-26 comment-heavy, hand-wrapped) predates the formatter,
   and a 137-file reformat would bury the team's history in
   churn. Recorded as a named deviation in QUALITY_PIPELINE.md,
   not silently skipped.

Coverage floors per module are measured from the Phase 17
full-gate run and recorded in QUALITY_PIPELINE.md; the security
and durability suites are excluded from any average there —
they must be complete, not averaged (the roadmap's words).

Importable (``run_gate(root)`` -> problem strings) and a CLI.
The suite runs the ruff half via
``tests/test_quality_gate.py``; the mypy half runs in CI and by
hand (it takes ~30 s, too slow for every test run).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# The B-178 landing count for `mypy --strict` over the four
# named trees. ONLY EVER FALLS — lower it in the same commit
# that lowers the real count.
MYPY_STRICT_CEILING = 722

MYPY_TREES = ("src/gpa/services", "src/gpa/ai", "src/gpa/internal", "src/gpa/crypto")


def ruff_problems(root: Path) -> list[str]:
    """Run the enforced ruff set; return problem lines."""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src/gpa"],
        cwd=root, capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return []
    return [
        f"ruff: {line}"
        for line in (proc.stdout + proc.stderr).splitlines()
        if line.strip()
    ]


def mypy_problems(root: Path) -> list[str]:
    """Run mypy --strict over the named trees; enforce the ceiling."""
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", *MYPY_TREES],
        cwd=root, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    match = re.search(r"Found (\d+) error", out)
    if match is None:
        return [f"mypy: could not parse its own output:\n{out[-2000:]}"]
    count = int(match.group(1))
    if count > MYPY_STRICT_CEILING:
        return [
            f"mypy: {count} strict errors over the named trees, above "
            f"the ratchet ceiling {MYPY_STRICT_CEILING} — the count "
            "may only fall (B-178)"
        ]
    return []


def run_gate(root: Path, *, include_mypy: bool = False) -> list[str]:
    """The gate's verdict: empty list = green."""
    problems = ruff_problems(root)
    if include_mypy:
        problems += mypy_problems(root)
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI: --mypy also runs the strict ratchet (CI does)."""
    args = argv if argv is not None else sys.argv[1:]
    root = Path(__file__).resolve().parent.parent
    problems = run_gate(root, include_mypy="--mypy" in args)
    if problems:
        print("Quality gate FAILED:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("Quality gate green: ruff enforced set clean"
          + ("; mypy strict at or below the ratchet ceiling."
             if "--mypy" in args else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
