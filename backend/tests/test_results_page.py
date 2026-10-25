"""Numbers page (roadmap B-149).

RESULTS.md is generated, never hand-typed: this test re-renders
the page from the shipped corpora and requires the committed
file to match byte-for-byte — so the numbers the submission
quotes always regenerate identically from the fixtures.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _render() -> str:
    spec = importlib.util.spec_from_file_location("make_results", ROOT / "scripts" / "make_results.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render()


def test_results_page_regenerates_identically():
    committed = (ROOT / "RESULTS.md").read_text(encoding="utf-8")
    assert _render() == committed
    assert _render() == _render()  # deterministic: no clock, no randomness


def test_results_page_quotes_the_honest_imperfections():
    text = (ROOT / "RESULTS.md").read_text(encoding="utf-8")
    assert "| event:place | 0.50 | 0.50 |" in text
    assert "| event:cost | 1.00 | 0.00 |" in text
    assert "| **overall** | **0.8125** |" in text
