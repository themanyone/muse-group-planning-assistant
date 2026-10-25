"""The extraction evaluation harness (roadmap B-046).

Golden-set measurement of the extraction pipeline: a small,
version-controlled corpus of sources with hand-written expected
drafts, run through the REAL pipeline (fixture driver, recorded
answers embedded in the corpus), scored per field:

- **precision** — of the fields the pipeline predicted (with a
  value), how many were right;
- **recall** — of the fields the gold expects, how many the
  pipeline got right.

Dates are scored on their RESOLVED date (D-22) — the phrase is
the model's, the meaning is the resolver's, and the meaning is
what a calendar lives or dies by. The corpus deliberately
contains one wrong place and one missing field, so the numbers
are evidence, not decoration: a harness that cannot show red
cannot be trusted when it shows green.

The phase gate (tests/test_evaluation.py) compares a fresh run
against ``eval_baseline.json``: any field dropping more than
2 points of precision or recall fails the build. Improving a
prompt means re-recording the baseline in the same commit — the
drift is the review.

Run it by hand: ``python -m gpa.ai.evaluation`` prints the table.
"""

import json
from datetime import datetime
from pathlib import Path

from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import KIND_CONFIGS, build_prompt, extract_kind

HERE = Path(__file__).parent
CORPUS_PATH = HERE / "eval_corpus.json"
BASELINE_PATH = HERE / "eval_baseline.json"

# The gate's tolerance: a field may wobble 2 points; beyond that
# the drift is a regression, not weather (B-046's exact words).
GATE_TOLERANCE = 0.02


def load_corpus() -> list[dict]:
    """The golden corpus, as data."""
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]


def load_baseline() -> dict:
    """The recorded baseline metrics (per "kind:field")."""
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["fields"]


class _Chunk:
    """A detached chunk stand-in with the attributes the pipeline
    reads (the corpus is data; no database is involved)."""

    def __init__(self, spec: dict):
        self.id = f"eval-{spec['position']}"
        self.position = spec["position"]
        self.sender = spec.get("sender")
        self.sent_at = (
            datetime.fromisoformat(spec["sent_at"]) if spec.get("sent_at") else None
        )
        self.text = spec["text"]


def _predicted_value(candidate, kind: str, field_name: str):
    """What the pipeline claims for one field: for date fields
    the resolved date; otherwise the raw value."""
    entry = candidate.draft.get("fields", {}).get(field_name)
    if entry is None:
        return None, False
    if field_name in KIND_CONFIGS[kind]["date_fields"]:
        return entry.get("resolved_date"), True
    return entry.get("value"), True


def _subject_of_expected(kind: str, fields: dict):
    from gpa.services.quality import SUBJECT_FIELD

    return fields.get(SUBJECT_FIELD.get(kind, "title"))


def _subject_of_candidate(candidate, kind: str):
    from gpa.services.quality import SUBJECT_FIELD

    name = SUBJECT_FIELD.get(kind, "title")
    entry = candidate.draft.get("fields", {}).get(name, {})
    return entry.get("value")


def _norm(value) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).lower().split())


def evaluate(corpus: list[dict] | None = None) -> dict[str, dict]:
    """Run the corpus and score it. Returns
    {"kind:field": {precision, recall, correct, predicted, gold}}.

    The fixture driver is recorded from each case's own embedded
    answers, keyed by the prompts the pipeline renders from the
    case's chunks — the same keying production recordings use.
    """
    cases = corpus if corpus is not None else load_corpus()
    stats: dict[str, dict[str, int]] = {}

    def bump(key: str, bucket: str):
        stats.setdefault(key, {"correct": 0, "predicted": 0, "gold": 0})[bucket] += 1

    for case in cases:
        chunks = [_Chunk(spec) for spec in case["chunks"]]
        tz = case["group_timezone"]
        created = datetime.fromisoformat(case["source_created_at"])
        driver = FixtureDriver()
        for kind, answer in case.get("answers", {}).items():
            prompt_name = KIND_CONFIGS[kind]["prompt"]
            schema = promptlib.load_prompt(prompt_name).schema
            driver.add_json(build_prompt(prompt_name, chunks, tz), schema, answer)

        for kind, gold_candidates in case.get("expected", {}).items():
            predicted = extract_kind(
                driver, kind=kind, chunks=chunks, group_timezone=tz,
                source_created_at=created,
            )
            # Match predicted to gold by subject (title / text).
            unmatched = list(predicted)
            for gold in gold_candidates:
                subject = _norm(_subject_of_expected(kind, gold["fields"]))
                match = next(
                    (c for c in unmatched
                     if _norm(_subject_of_candidate(c, kind)) == subject),
                    None,
                )
                if match is not None:
                    unmatched.remove(match)
                for field_name, gold_value in gold["fields"].items():
                    key = f"{kind}:{field_name}"
                    bump(key, "gold")
                    if match is None:
                        continue
                    value, present = _predicted_value(match, kind, field_name)
                    if present and _norm(value) == _norm(gold_value):
                        bump(key, "correct")
            # Every valued field of every predicted candidate is a
            # prediction — matched or not, right or wrong.
            for candidate in predicted:
                for field_name, entry in candidate.draft.get("fields", {}).items():
                    if entry.get("value") is not None:
                        bump(f"{kind}:{field_name}", "predicted")
            # Correct predictions were already counted in the
            # predicted bucket above by construction; fields whose
            # gold value is null and predicted null count as gold
            # only (nothing was predicted), scored on the gold side.

    out: dict[str, dict] = {}
    for key, counts in sorted(stats.items()):
        correct, predicted_n, gold_n = counts["correct"], counts["predicted"], counts["gold"]
        out[key] = {
            "precision": (correct / predicted_n) if predicted_n else 1.0,
            "recall": (correct / gold_n) if gold_n else 1.0,
            "correct": correct,
            "predicted": predicted_n,
            "gold": gold_n,
        }
    return out


def gate_failures(metrics: dict[str, dict], baseline: dict | None = None) -> list[str]:
    """The phase-gate comparison: human-readable failures for any
    baseline field that dropped beyond the tolerance."""
    baseline = baseline if baseline is not None else load_baseline()
    failures: list[str] = []
    for key, base in baseline.items():
        current = metrics.get(key)
        if current is None:
            failures.append(f"{key}: missing from the run entirely")
            continue
        for metric in ("precision", "recall"):
            if current[metric] < base[metric] - GATE_TOLERANCE:
                failures.append(
                    f"{key} {metric} dropped {base[metric]:.2f} -> "
                    f"{current[metric]:.2f} (beyond the 2-point tolerance)"
                )
    return failures


def format_table(metrics: dict[str, dict]) -> str:
    """The printable report (B-046: 'a table into the test
    report' — and onto any terminal that asks)."""
    lines = [
        f"{'field':<22} {'precision':>9} {'recall':>7} {'correct':>7} {'pred':>5} {'gold':>5}",
        "-" * 62,
    ]
    for key, m in metrics.items():
        lines.append(
            f"{key:<22} {m['precision']:>9.2f} {m['recall']:>7.2f} "
            f"{m['correct']:>7} {m['predicted']:>5} {m['gold']:>5}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    results = evaluate()
    print(format_table(results))
    problems = gate_failures(results)
    if problems:
        print("\nGATE FAILURES:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("\nWithin tolerance of the recorded baseline.")
