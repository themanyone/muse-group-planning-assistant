"""B-178 — the production-quality pipeline, enforced in-suite.

The ruff half of scripts/quality_gate.py runs here (fast); the
mypy strict ratchet runs in CI and through the gate's --mypy
mode (it takes ~30 s). This test also pins the ratchet's law:
the ceiling constant exists, is a positive count, and the
pipeline config ships in pyproject where a fresh checkout
finds it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import quality_gate  # noqa: E402


def test_ruff_enforced_set_is_clean():
    assert quality_gate.ruff_problems(ROOT) == []


def test_mypy_ceiling_is_recorded_and_positive():
    assert quality_gate.MYPY_STRICT_CEILING > 0
    assert set(quality_gate.MYPY_TREES) == {
        "src/gpa/services",
        "src/gpa/ai",
        "src/gpa/internal",
        "src/gpa/crypto",
    }


def test_pipeline_config_ships_in_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for needle in ("[tool.ruff]", "[tool.mypy]", "[tool.coverage.run]"):
        assert needle in text
    assert (ROOT / ".pre-commit-config.yaml").is_file()
