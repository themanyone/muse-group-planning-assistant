"""Evaluation harness tests (roadmap B-046) — including the
phase-gate comparison itself.

Three assertions: the harness reproduces the recorded baseline
(the corpus is honest, imperfections included); the gate passes
on that run; and the gate has TEETH — a deliberately degraded
corpus (the good place answer replaced by a wrong one) drops
event:place precision beyond the 2-point tolerance and the gate
names it.
"""

import copy

from gpa.ai.evaluation import evaluate, format_table, gate_failures, load_corpus


def test_harness_reproduces_the_baseline():
    metrics = evaluate()
    # The deliberate imperfections, visible in the numbers.
    assert metrics["event:place"]["precision"] == 0.5
    assert metrics["event:place"]["recall"] == 0.5
    assert metrics["event:cost"]["recall"] == 0.0
    assert metrics["event:starts"]["precision"] == 1.0
    table = format_table(metrics)
    assert "event:place" in table  # the printable report exists


def test_gate_passes_on_the_shipped_corpus():
    """B-046's gate: no baseline field dropped > 2 points."""
    assert gate_failures(evaluate()) == []


def test_gate_catches_a_regression():
    """Degrade the golden run — the club night's place answered
    wrong — and the gate must fail, naming the field."""
    corpus = copy.deepcopy(load_corpus())
    club = corpus[0]
    club["answers"]["event"]["items"][0]["fields"]["place"] = {
        "value": "the moon", "confidence": 0.9, "quote": "at the hall",
    }
    metrics = evaluate(corpus)
    assert metrics["event:place"]["precision"] == 0.0
    failures = gate_failures(metrics)
    assert any("event:place" in failure for failure in failures), failures
