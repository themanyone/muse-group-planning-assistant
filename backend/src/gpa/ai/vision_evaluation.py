"""Vision evaluation harness (roadmap B-073) — the B-046
harness's vision edition.

The golden flyers of B-063 — clean print, handwriting, rotated,
glare — become a scored set: each flyer's picture is built
deterministically in code (Pillow), its fixture answer is
recorded against the REAL prompt and the REAL bytes, and the
real reading pipeline (ai/vision.py) drafts from it. Fields are
scored against the corpus's expected values — dates on the
*resolved* date, never the phrase — and reported per flyer
type, because "vision is 90% accurate" hides exactly the
failures a village cares about ("it cannot read handwriting").

The corpus is deliberately imperfect, like the text one: the
handwriting answer omits the cost, the rotated answer omits the
place, the glare answer misreads the date by a digit. The gate
(test_vision_evaluation.py, tolerance 0.02 like B-046) exists to
catch the pipeline getting *worse*, and a degradation test
proves it has teeth. The fixture answers are recorded fiction —
the honest stand-in for a real model until a Meta endpoint
exists (the driver boundary is the deliverable, Phase 5 map).

Run it:  python -m gpa.ai.vision_evaluation
"""

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.vision import build_vision_prompt, read_flyer_image

_CORPUS_PATH = Path(__file__).with_name("vision_eval_corpus.json")
_BASELINE_PATH = Path(__file__).with_name("vision_eval_baseline.json")

# The anchor every case resolves dates against: a fixed instant,
# so the scores never drift with the calendar.
ANCHOR = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
GROUP_TZ = "UTC"

# The fields every golden flyer is scored on, and where each
# lives in the pipeline's draft.
_SCORED = ("title", "starts", "place", "cost")


def build_golden_image(case: dict) -> bytes:
    """Render one golden flyer deterministically: a coloured
    ground, a pale band, dark bars standing in for lettering —
    plus the case's treatment (``rotated`` turns the finished
    poster a quarter-turn). The fixture answer is keyed by these
    exact bytes, so determinism is the whole game."""
    from PIL import Image, ImageDraw

    ground = tuple(case["ground"])
    image = Image.new("RGB", (400, 520), ground)
    draw = ImageDraw.Draw(image)
    draw.rectangle([24, 60, 376, 240], fill=(238, 232, 216))
    y = 84
    for width in (300, 210, 260):
        draw.rectangle([48, y, 48 + width, y + 20], fill=(30, 30, 34))
        y += 44
    draw.rectangle([24, 420, 120, 500], outline=(30, 30, 34), width=5)
    if case.get("treatment") == "rotated":
        image = image.rotate(90, expand=True)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _field_value(draft: dict, field: str):
    """Where a scored field lives in a vision draft: the start
    is scored on its resolved date; the rest on the draft's
    top-level mirror of the field value."""
    if field == "starts":
        return draft.get("starts_date")
    return draft.get(field)


def _norm(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def run_vision_evaluation(corpus_path: Path | None = None) -> dict:
    """Score every golden flyer through the real pipeline.

    Returns {cases: [...], overall_accuracy, by_type: {type:
    accuracy}, failures: [...]} — the failures list names the
    case and field, so a regression report says what broke.
    """
    corpus = json.loads((corpus_path or _CORPUS_PATH).read_text(encoding="utf-8"))
    schema = promptlib.load_prompt("flyer_reading").schema or {}
    prompt = build_vision_prompt()
    driver = FixtureDriver()
    images: dict[str, bytes] = {}
    for case in corpus["cases"]:
        image_bytes = build_golden_image(case)
        images[case["name"]] = image_bytes
        driver.add_image(prompt, image_bytes, case["answer"], schema=schema)

    results: list[dict] = []
    failures: list[dict] = []
    type_scores: dict[str, list[float]] = {}
    for case in corpus["cases"]:
        reading = read_flyer_image(
            driver,
            image_bytes=images[case["name"]],
            group_timezone=GROUP_TZ,
            source_created_at=ANCHOR,
        )
        expected = case["expected"]
        scored = {
            field: (
                _norm(_field_value(reading.draft, field))
                == _norm(expected["starts_date"] if field == "starts" else expected[field])
            )
            for field in _SCORED
        }
        accuracy = sum(scored.values()) / len(_SCORED)
        results.append(
            {"name": case["name"], "flyer_type": case["type"],
             "accuracy": accuracy, "fields": scored}
        )
        type_scores.setdefault(case["type"], []).append(accuracy)
        for field, ok in scored.items():
            if not ok:
                failures.append(
                    {"case": case["name"], "field": field,
                     "got": _field_value(reading.draft, field),
                     "expected": expected["starts_date"] if field == "starts"
                     else expected[field]}
                )
    overall = (
        sum(r["accuracy"] for r in results) / len(results) if results else 0.0
    )
    return {
        "cases": results,
        "overall_accuracy": overall,
        "by_type": {
            kind: sum(scores) / len(scores) for kind, scores in type_scores.items()
        },
        "failures": failures,
    }


def load_baseline() -> dict:
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def format_report(report: dict, baseline: dict) -> str:
    """The printed table (B-073's acceptance: the report
    contains it) — accuracy per flyer type, then the gate line."""
    lines = [
        "Vision evaluation — golden flyers (fixture driver)",
        f"{'type':<14}{'cases':>6}{'accuracy':>10}{'baseline':>10}",
    ]
    by_type_cases: dict[str, int] = {}
    for case in report["cases"]:
        by_type_cases[case["flyer_type"]] = by_type_cases.get(case["flyer_type"], 0) + 1
    for kind in sorted(report["by_type"]):
        lines.append(
            f"{kind:<14}{by_type_cases[kind]:>6}"
            f"{report['by_type'][kind]:>10.2f}"
            f"{baseline['by_type'].get(kind, 0.0):>10.2f}"
        )
    lines.append(
        f"overall accuracy {report['overall_accuracy']:.2f} "
        f"(baseline {baseline['overall_accuracy']:.2f}, tolerance 0.02)"
    )
    for failure in report["failures"]:
        lines.append(
            f"  miss: {failure['case']} · {failure['field']} — "
            f"got {failure['got']!r}, expected {failure['expected']!r}"
        )
    return "\n".join(lines)


def main() -> None:
    report = run_vision_evaluation()
    print(format_report(report, load_baseline()))


if __name__ == "__main__":
    main()
