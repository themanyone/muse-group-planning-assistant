"""The pruning evaluation harness (roadmap B-121).

B-046 gave the project its evaluation habit — golden sets,
scored by the real pipeline, reported as a table. This module
is that habit for the pruning engine: the golden questions in
``pruning_eval_corpus.json`` carry HUMAN-marked relevant
positions, and a recorded judge's kept positions are replayed
through the real engine (chunking, windowing, the lot), so
the recall and precision below measure the ENGINE end to
end, not a paraphrase of it.

The recorded judgements are deliberately imperfect — one
case drops a relevant chunk, one keeps an extra — because an
evaluation that cannot show a failure is not an evaluation.
The floors the phase gate asserts live in
``tests/test_pruning_evaluation.py``; the table this module
renders is pasted into TEST_REPORT.md at the gate.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from gpa.ai import promptlib, pruning
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import chunk_windows

_CORPUS_PATH = Path(__file__).with_name("pruning_eval_corpus.json")


def load_cases() -> list[dict]:
    """The golden pruning set, as data."""
    payload = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    return payload["cases"]


def _chunks_for(case: dict) -> list[pruning.MemoryChunk]:
    return [
        pruning.MemoryChunk(
            chunk_id=f"case:{index}",
            position=index,
            text=raw["text"],
            sender=raw.get("sender"),
            sent_at=(
                datetime.fromisoformat(raw["sent_at"])
                if raw.get("sent_at")
                else None
            ),
            origin="source",
        )
        for index, raw in enumerate(case["chunks"])
    ]


def _recorded_driver(case: dict, chunks: list[pruning.MemoryChunk]) -> FixtureDriver:
    """A fixture driver replaying the case's recorded
    judgements — the same recording construction the engine's
    own tests use: per window, the judged positions that fall
    in it (the engine renders chunks under their corpus
    positions, and judges answer in those positions)."""
    driver = FixtureDriver()
    schema = promptlib.load_prompt("prune").schema
    judged = set(case["judgements"])
    for window in chunk_windows(chunks):
        kept = [c.position for c in window if c.position in judged]
        driver.add_json(
            pruning.build_prune_prompt(case["question"], window),
            schema, {"kept": kept},
        )
    return driver


def evaluate_pruning() -> dict:
    """Run the golden set through the engine and score it.

    Returns per-case recall/precision (against the human
    marks) plus the means — the numbers the phase gate's
    floors and TEST_REPORT's table both read.
    """
    rows = []
    for case in load_cases():
        chunks = _chunks_for(case)
        driver = _recorded_driver(case, chunks)
        outcome = pruning.prune_once(
            driver,
            question=case["question"],
            chunks=chunks,
            token_budget=1_000_000,  # the eval measures judgement, not budget
        )
        kept_positions = {c.position for c in outcome.kept}
        relevant = set(case["relevant"])
        hits = kept_positions & relevant
        rows.append(
            {
                "question": case["question"],
                "chunks": len(chunks),
                "relevant": len(relevant),
                "kept": len(kept_positions),
                "recall": (len(hits) / len(relevant)) if relevant else 1.0,
                "precision": (
                    len(hits) / len(kept_positions) if kept_positions else 0.0
                ),
            }
        )
    return {
        "cases": rows,
        "mean_recall": (
            sum(r["recall"] for r in rows) / len(rows) if rows else 0.0
        ),
        "mean_precision": (
            sum(r["precision"] for r in rows) / len(rows) if rows else 0.0
        ),
    }


def format_table(result: dict) -> str:
    """The evaluation as a markdown table — the shape
    TEST_REPORT.md carries (B-046's report format)."""
    lines = [
        "| Question | Chunks | Relevant | Kept | Recall | Precision |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result["cases"]:
        lines.append(
            f"| {row['question']} | {row['chunks']} | {row['relevant']} "
            f"| {row['kept']} | {row['recall']:.2f} | {row['precision']:.2f} |"
        )
    lines.append(
        f"| **Mean** | | | | **{result['mean_recall']:.2f}** "
        f"| **{result['mean_precision']:.2f}** |"
    )
    return "\n".join(lines)
