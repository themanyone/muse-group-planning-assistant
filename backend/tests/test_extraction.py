"""Extraction pipeline tests (roadmap B-040, B-041, B-042).

Golden material: the Doc 07 worked example — Ana, Ben and Cleo
planning a club night on Wed 2026-10-21 — plus the twelve-event
newsletter. The model is played by the fixture driver (recordings
keyed by the exact prompts the pipeline renders), so what runs
here is the whole verification spine: quote matching, date
resolution against sent-at + group timezone, coverage flags,
repair-on-malformed, and the hallucinated quote being caught.
"""

from datetime import datetime

import pytest

from gpa.ai import promptlib
from gpa.ai.client import ModelError, ModelResult
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt, chunk_windows, extract_kind
from gpa.models.sources import SourceChunk

TZ = "America/New_York"


def chunk(position, sender, sent_at, text):
    return SourceChunk(
        id=f"chk_{position}", source_id="src_test", position=position,
        sender=sender, sent_at=sent_at, text=text,
    )


def golden_chat():
    return [
        chunk(0, "Ana", datetime(2026, 10, 21, 23, 2),
              "Club night is on! this Saturday at the hall, doors 7pm"),
        chunk(1, "Ben", datetime(2026, 10, 21, 23, 3),
              "I can bring the projector"),
        chunk(2, "Cleo", datetime(2026, 10, 21, 23, 4),
              "I'll sort the playlist again"),
        chunk(3, "Ana", datetime(2026, 10, 21, 23, 5),
              "Agreed with the committee: the quiz is postponed to November"),
        chunk(4, "Ben", datetime(2026, 10, 21, 23, 6),
              "Quick question — who is doing the door rota this time?"),
    ]


def field(value, confidence, quote, **extra):
    return {"value": value, "confidence": confidence, "quote": quote, **extra}


EVENT_ANSWER = {
    "items": [
        {
            "fields": {
                "title": field("Club night", 0.95, "Club night is on!"),
                "starts": field(
                    "this Saturday 7pm", 0.9, "this Saturday",
                    date_phrase="this Saturday", time_phrase="7pm",
                ),
                "place": field("the hall", 0.85, "at the hall"),
                "host": field(None, 0.0, ""),
            }
        }
    ]
}

TASK_ANSWER = {
    "items": [
        {
            "fields": {
                "title": field("Bring the projector", 0.9, "bring the projector"),
                "owner": field("Ben", 0.85, "I can bring the projector"),
            }
        }
    ]
}

DECISION_ANSWER = {
    "items": [
        {"fields": {"text": field(
            "the quiz is postponed to November", 0.92,
            "the quiz is postponed to November",
        )}}
    ]
}

QUESTION_ANSWER = {
    "items": [
        {"fields": {"text": field(
            "who is doing the door rota this time?", 0.9,
            "who is doing the door rota this time?",
        )}}
    ]
}


def record(driver: FixtureDriver, prompt_name: str, chunks, tz, answer) -> None:
    """Record one answer under the exact prompt + schema the
    pipeline will send (the fixture key is their hash)."""
    schema = promptlib.load_prompt(prompt_name).schema
    driver.add_json(build_prompt(prompt_name, chunks, tz), schema, answer)


def recorded_driver(chunks, answers: dict[str, dict]) -> FixtureDriver:
    """A fixture driver recorded with the exact prompts the
    pipeline will render for these chunks and kinds."""
    driver = FixtureDriver()
    prompts = {
        "event": "extraction", "task": "extraction_tasks",
        "decision": "extraction_decisions", "question": "extraction_questions",
    }
    for kind, answer in answers.items():
        record(driver, prompts[kind], chunks, TZ, answer)
    return driver


def test_golden_chat_event_extraction():
    """B-040: the chat yields the club night, every field carrying
    its quote and confidence; the date resolves deterministically
    against the quote's chunk sent-at in the group timezone —
    Wed 2026-10-21's "this Saturday" is Sat 2026-10-24, 7pm."""
    chunks = golden_chat()
    driver = recorded_driver(chunks, {"event": EVENT_ANSWER})
    (candidate,) = extract_kind(
        driver, kind="event", chunks=chunks, group_timezone=TZ,
        source_created_at=datetime(2026, 10, 21, 23, 2),
    )
    fields = candidate.draft["fields"]
    assert fields["title"]["value"] == "Club night"
    assert fields["title"]["supported"] is True
    assert fields["starts"]["resolved_date"] == "2026-10-24"
    assert fields["starts"]["resolved_time"] == "19:00:00"
    assert fields["starts"]["date_ambiguous"] is False
    assert fields["place"]["chunk_position"] == 0
    assert fields["host"]["value"] is None and fields["host"]["supported"] is False
    assert candidate.confidence == pytest.approx((0.95 + 0.9 + 0.85) / 3)
    assert candidate.prompt_name == "extraction" and candidate.prompt_version == 1
    provenance = {row["field"]: row for row in candidate.field_provenance}
    assert provenance["starts"]["chunk_id"] == "chk_0"
    assert provenance["starts"]["sent_at"] == "2026-10-21T23:02:00"


def test_hallucinated_quote_is_caught():
    """B-040's spine: a quote that appears nowhere in the source
    marks the field unsupported, strips its provenance, and keeps
    its confidence out of the candidate's score."""
    chunks = golden_chat()
    answer = {"items": [{"fields": {
        "title": field("Club night", 0.95, "Club night is on!"),
        "place": field("the secret castle", 0.99, "at the secret castle"),
    }}]}
    driver = recorded_driver(chunks, {"event": answer})
    (candidate,) = extract_kind(
        driver, kind="event", chunks=chunks, group_timezone=TZ,
        source_created_at=datetime(2026, 10, 21, 23, 2),
    )
    place = candidate.draft["fields"]["place"]
    assert place["supported"] is False
    assert place["value"] == "the secret castle"  # kept, but exposed
    provenance = {row["field"]: row for row in candidate.field_provenance}
    assert provenance["place"]["chunk_id"] is None
    assert candidate.confidence == pytest.approx(0.95)  # only supported fields


def test_all_four_kinds_coexist_in_one_thread():
    """B-042: the same pipeline drafts the task, the decision and
    the open question out of the same five messages."""
    chunks = golden_chat()
    driver = recorded_driver(chunks, {
        "event": EVENT_ANSWER, "task": TASK_ANSWER,
        "decision": DECISION_ANSWER, "question": QUESTION_ANSWER,
    })
    task = extract_kind(driver, kind="task", chunks=chunks, group_timezone=TZ,
                        source_created_at=datetime(2026, 10, 21, 23, 3))
    decision = extract_kind(driver, kind="decision", chunks=chunks, group_timezone=TZ,
                            source_created_at=datetime(2026, 10, 21, 23, 5))
    question = extract_kind(driver, kind="question", chunks=chunks, group_timezone=TZ,
                            source_created_at=datetime(2026, 10, 21, 23, 6))
    assert task[0].draft["fields"]["title"]["value"] == "Bring the projector"
    assert task[0].draft["fields"]["owner"]["value"] == "Ben"
    assert decision[0].draft["fields"]["text"]["value"] == "the quiz is postponed to November"
    assert question[0].draft["fields"]["text"]["value"] == "who is doing the door rota this time?"
    assert task[0].prompt_name == "extraction_tasks"


def test_newsletter_yields_twelve_candidates():
    """B-041: twelve sections in, twelve candidates out — the
    model's splitting verified by quote coverage, one chunk each,
    no coverage flags."""
    chunks = [
        chunk(i, None, datetime(2026, 10, 20, 9, 0), f"Event {i}: gathering number {i} at the hall")
        for i in range(12)
    ]
    answer = {"items": [
        {"fields": {"title": field(f"Event {i}", 0.9, f"Event {i}:")}}
        for i in range(12)
    ]}
    driver = FixtureDriver()
    record(driver, "extraction", chunks, "UTC", answer)
    candidates = extract_kind(
        driver, kind="event", chunks=chunks, group_timezone="UTC",
        source_created_at=datetime(2026, 10, 20, 9, 0),
    )
    assert len(candidates) == 12
    assert all(c.flags == [] for c in candidates)
    assert {c.draft["fields"]["title"]["value"] for c in candidates} == {
        f"Event {i}" for i in range(12)
    }


def test_unflagged_shared_chunk_is_flagged_on_both_claimants():
    """B-041: two candidates drinking from the same chunk with
    neither flagged shared -> both carry the coverage flag."""
    chunks = golden_chat()
    answer = {"items": [
        {"fields": {"title": field("Club night", 0.9, "Club night is on!")}},
        {"fields": {"title": field("Saturday social", 0.8, "this Saturday at the hall")}},
    ]}
    driver = recorded_driver(chunks, {"event": answer})
    candidates = extract_kind(
        driver, kind="event", chunks=chunks, group_timezone=TZ,
        source_created_at=datetime(2026, 10, 21, 23, 2),
    )
    assert len(candidates) == 2
    assert all("shared_chunk_unflagged" in c.flags for c in candidates)


def test_flagged_shared_chunk_is_not_flagged():
    """The same overlap, honestly flagged shared by the model,
    raises no coverage flag."""
    chunks = golden_chat()
    answer = {"items": [
        {"fields": {"title": field("Club night", 0.9, "Club night is on!")}, "shared": True},
        {"fields": {"title": field("Hall booking", 0.8, "this Saturday at the hall")}},
    ]}
    driver = recorded_driver(chunks, {"event": answer})
    candidates = extract_kind(
        driver, kind="event", chunks=chunks, group_timezone=TZ,
        source_created_at=datetime(2026, 10, 21, 23, 2),
    )
    assert all("shared_chunk_unflagged" not in c.flags for c in candidates)


def test_chunk_windows_respect_both_bounds():
    """B-040's windowing: at most 40 chunks and ~12k characters
    per window; a single oversize chunk travels alone."""
    many = [chunk(i, "A", None, "x") for i in range(45)]
    windows = chunk_windows(many)
    assert [len(w) for w in windows] == [40, 5]
    big = [chunk(i, "A", None, "y" * 7000) for i in range(3)]
    assert [len(w) for w in chunk_windows(big)] == [1, 1, 1]


class QueueClient:
    """A scripted fake client for the malformed-output paths."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete_json(self, *, prompt, schema, tier="wonder"):
        self.prompts.append(prompt)
        return ModelResult(
            text="", model_id="fake", tier=tier, data=self.answers.pop(0)
        )

    def complete_text(self, *, prompt, tier="spark"):
        raise NotImplementedError

    def read_image(self, *, prompt, image_bytes, schema=None, tier="wonder"):
        raise NotImplementedError

    def transcribe(self, *, audio_bytes, mime="audio/wav", tier="spark"):
        raise NotImplementedError


def test_malformed_output_gets_one_repair_retry():
    """A shape-broken answer is retried once with the fault named
    in the prompt; the good second answer flows through."""
    chunks = golden_chat()
    client = QueueClient([{"nonsense": True}, EVENT_ANSWER])
    (candidate,) = extract_kind(
        client, kind="event", chunks=chunks, group_timezone=TZ,
        source_created_at=datetime(2026, 10, 21, 23, 2),
    )
    assert candidate.draft["fields"]["title"]["value"] == "Club night"
    assert len(client.prompts) == 2
    assert "not valid" in client.prompts[1]


def test_second_malformed_output_fails_visibly():
    """Two bad answers and the extraction refuses — a job built on
    this fails loudly instead of drafting from garbage."""
    chunks = golden_chat()
    client = QueueClient([{"nonsense": True}, {"still": "bad"}])
    with pytest.raises(ModelError):
        extract_kind(
            client, kind="event", chunks=chunks, group_timezone=TZ,
            source_created_at=datetime(2026, 10, 21, 23, 2),
        )


def test_ambiguous_date_keeps_all_readings():
    """A chunk sent Sunday speaking of "this Saturday": the draft
    carries BOTH readings and no resolved date — the resolver's
    honesty survives the pipeline intact."""
    chunks = [chunk(0, "Ana", datetime(2026, 10, 25, 10, 0),
                    "Lovely time this Saturday, same again this Saturday?")]
    answer = {"items": [{"fields": {
        "title": field("Meetup", 0.9, "Lovely time"),
        "starts": field("this Saturday", 0.8, "this Saturday",
                        date_phrase="this Saturday", time_phrase=None),
    }}]}
    driver = FixtureDriver()
    record(driver, "extraction", chunks, "UTC", answer)
    (candidate,) = extract_kind(
        driver, kind="event", chunks=chunks, group_timezone="UTC",
        source_created_at=datetime(2026, 10, 25, 10, 0),
    )
    starts = candidate.draft["fields"]["starts"]
    assert starts["date_readings"] == ["2026-10-24", "2026-10-31"]
    assert starts["date_ambiguous"] is True
    assert starts["resolved_date"] is None
