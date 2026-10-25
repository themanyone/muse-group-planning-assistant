"""Pruning evaluation (roadmap B-121).

The golden pruning set, scored through the real engine.
Floors are the acceptance — and one case is a deliberate
judge miss, asserted BY NAME, so the harness is proven to
measure rather than flatter.
"""

from gpa.ai.pruning_evaluation import evaluate_pruning, format_table


def test_pruning_evaluation_scores_the_golden_set():
    result = evaluate_pruning()
    assert len(result["cases"]) == 4
    # Floors: the judge is good, not perfect — the means must
    # clear an honest bar with the imperfect cases included.
    assert result["mean_recall"] >= 0.80
    assert result["mean_precision"] >= 0.90

    by_question = {row["question"]: row for row in result["cases"]}
    perfect = by_question["Where do club nights happen?"]
    assert perfect["recall"] == 1.0 and perfect["precision"] == 1.0
    # The deliberate miss, measured and named: half the
    # relevant chunks for the marquee question were dropped
    # by the recorded judge, and the harness says so.
    miss = by_question["Who is bringing the marquee and when?"]
    assert miss["recall"] == 0.5
    # The over-keeper: full recall, dented precision.
    generous = by_question["What did we decide about food for the supper?"]
    assert generous["recall"] == 1.0 and generous["precision"] == 0.75


def test_pruning_evaluation_table_renders():
    table = format_table(evaluate_pruning())
    assert "| Question | Chunks | Relevant | Kept | Recall | Precision |" in table
    assert "Where do club nights happen?" in table
    assert "**Mean**" in table
