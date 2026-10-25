"""Vision evaluation gate (roadmap B-073) — B-046's pattern,
vision edition.

The golden flyers score through the real pipeline; accuracy
per flyer type and overall must hold within 0.02 of the
recorded baseline. The degradation test tampers with a corpus
copy and proves the gate notices — a gate that cannot fail is
decoration.
"""

import json

from gpa.ai import vision_evaluation

TOLERANCE = 0.02


def test_vision_evaluation_holds_its_baseline():
    report = vision_evaluation.run_vision_evaluation()
    baseline = vision_evaluation.load_baseline()
    assert report["overall_accuracy"] >= baseline["overall_accuracy"] - TOLERANCE
    for kind, accuracy in report["by_type"].items():
        assert accuracy >= baseline["by_type"][kind] - TOLERANCE, (
            f"{kind} accuracy {accuracy:.2f} fell below its baseline "
            f"{baseline['by_type'][kind]:.2f}"
        )
    # The known misses are the corpus's deliberate imperfections,
    # named in the report — exactly three.
    missed = {(f["case"], f["field"]) for f in report["failures"]}
    assert missed == {
        ("quiz-night", "cost"),
        ("bonfire-night", "place"),
        ("craft-fair", "starts"),
    }


def test_the_report_prints_the_per_type_table():
    report = vision_evaluation.run_vision_evaluation()
    text = vision_evaluation.format_report(report, vision_evaluation.load_baseline())
    for kind in ("clean", "handwriting", "rotated", "glare"):
        assert kind in text
    assert "overall accuracy" in text


def test_the_gate_has_teeth(tmp_path):
    """Tamper: the clean flyer's answer now misreads the place.
    Accuracy must fall — if it does not, the harness is scoring
    nothing."""
    corpus = json.loads(
        vision_evaluation._CORPUS_PATH.read_text(encoding="utf-8")
    )
    clean = next(c for c in corpus["cases"] if c["type"] == "clean")
    clean["answer"]["items"][0]["fields"]["place"]["value"] = "Community Centre"
    tampered = tmp_path / "corpus.json"
    tampered.write_text(json.dumps(corpus), encoding="utf-8")
    report = vision_evaluation.run_vision_evaluation(tampered)
    baseline = vision_evaluation.load_baseline()
    assert report["by_type"]["clean"] < baseline["by_type"]["clean"] - TOLERANCE
    assert ("harvest-supper", "place") in {
        (f["case"], f["field"]) for f in report["failures"]
    }
