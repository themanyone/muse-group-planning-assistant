"""Vision pipeline tests (roadmap B-063, B-064) — the pure half.

The pipeline (ai/vision.py) and the segmentation boundary
(ai/segmentation.py) with no database in sight: a recorded
vision answer becomes a verified draft whose fields carry
REGION provenance; a region that is not an honest rectangle
marks its field unsupported; dates resolve through the same
deterministic resolver as text (D-22), anchored on the photo's
context instant; malformed answers earn exactly one repair
retry. Segmentation is proven in BOTH driver modes — the SAM
wire client against a stubbed transport, and the vision-boxes
fallback against a fixture recording.
"""

from datetime import datetime

import httpx
import pytest

from gpa.ai import promptlib
from gpa.ai.client import ModelError
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.drivers_mock import MockDriver
from gpa.ai.segmentation import (
    SamSegmenter,
    SegmenterUnavailable,
    VisionBoxSegmenter,
    build_segmenter,
)
from gpa.ai.vision import build_vision_prompt, read_flyer_image, valid_region
from gpa.config import Settings

IMAGE = b"fake-poster-bytes"

HARVEST_ANSWER = {
    "items": [
        {
            "fields": {
                "title": {"value": "Harvest Supper", "confidence": 0.97,
                          "region": [0.41, 0.08, 0.47, 0.14]},
                "starts": {"value": "Fri Nov 6, 6.30pm", "confidence": 0.9,
                           "region": [0.44, 0.30, 0.36, 0.08],
                           "date_phrase": "Nov 6", "time_phrase": "6.30pm"},
                "place": {"value": "Village Hall", "confidence": 0.93,
                          "region": [0.44, 0.40, 0.30, 0.07]},
                "cost": {"value": "£6", "confidence": 0.86,
                         "region": [0.52, 0.61, 0.14, 0.09]},
                "bring": {"value": "a dish to share", "confidence": 0.8,
                          "region": None},
                "host": {"value": None, "confidence": 0.0, "region": None},
            },
            "transcript": "HARVEST SUPPER — Fri Nov 6, 6.30pm, Village Hall. £6. Bring a dish to share! Book at example.org/supper",
            "alt_text": "A cream paper poster with a red headline 'Harvest Supper' and a drawn pumpkin border; the text gives Friday Nov 6, 6.30pm, Village Hall, £6, bring a dish to share.",
            "links": [
                {"url": "https://example.org/supper", "kind": "printed",
                 "region": [0.30, 0.85, 0.40, 0.06]},
                {"url": "https://example.org/supper", "kind": "qr", "region": None},
            ],
        }
    ]
}


def _fixture_driver(answer=HARVEST_ANSWER) -> FixtureDriver:
    driver = FixtureDriver()
    schema = promptlib.load_prompt("flyer_reading").schema
    driver.add_image(build_vision_prompt(), IMAGE, answer, schema=schema)
    return driver


def test_reading_carries_region_provenance_and_resolved_dates():
    reading = read_flyer_image(
        _fixture_driver(),
        image_bytes=IMAGE,
        group_timezone="America/New_York",
        source_created_at=datetime(2026, 10, 21, 12, 0),
    )
    fields = reading.draft["fields"]
    assert fields["title"]["supported"] is True
    assert fields["title"]["region"] == [0.41, 0.08, 0.47, 0.14]
    # The date phrase resolved deterministically from the photo's
    # context (Oct 2026) — Fri Nov 6, 2026, at 18:30.
    assert reading.draft["starts_date"] == "2026-11-06"
    assert reading.draft["starts_at"] == "2026-11-06T18:30:00"
    assert reading.draft["title"] == "Harvest Supper"
    assert reading.confidence > 0.8
    assert reading.prompt_name == "flyer_reading"
    assert reading.prompt_version == 2
    # Provenance rows mirror the fields: region, never a quote.
    by_field = {row["field"]: row for row in reading.field_provenance}
    assert by_field["title"]["region"] == [0.41, 0.08, 0.47, 0.14]
    assert by_field["title"]["supported"] is True


def test_a_regionless_field_is_unsupported_not_hidden():
    reading = read_flyer_image(
        _fixture_driver(),
        image_bytes=IMAGE,
        group_timezone="America/New_York",
        source_created_at=datetime(2026, 10, 21, 12, 0),
    )
    bring = reading.draft["fields"]["bring"]
    assert bring["value"] == "a dish to share"  # the value survives
    assert bring["supported"] is False  # but it has no evidence
    host = reading.draft["fields"]["host"]
    assert host["value"] is None and host["supported"] is False


def test_transcript_alt_and_links_ride_the_reading():
    reading = read_flyer_image(
        _fixture_driver(),
        image_bytes=IMAGE,
        group_timezone="America/New_York",
        source_created_at=datetime(2026, 10, 21, 12, 0),
    )
    assert reading.transcript.startswith("HARVEST SUPPER")
    assert "pumpkin border" in reading.alt_text
    # The same URL offered twice collapses to one link (B-066).
    assert reading.links == [
        {"url": "https://example.org/supper", "kind": "printed",
         "region": [0.30, 0.85, 0.40, 0.06]}
    ]
    assert reading.draft["links"] == reading.links


def test_valid_region_rules():
    assert valid_region([0.0, 0.0, 1.0, 1.0]) is True
    assert valid_region([0.5, 0.5, 0.5, 0.5]) is True
    assert valid_region([0.9, 0.0, 0.2, 0.5]) is False  # off the right edge
    assert valid_region([0.1, 0.1, 0.0, 0.5]) is False  # no area
    assert valid_region([-0.1, 0.0, 0.5, 0.5]) is False  # off the left edge
    assert valid_region([0.1, 0.1, 0.5]) is False  # not a rectangle
    assert valid_region("0.1,0.1,0.5,0.5") is False  # not numbers
    assert valid_region(None) is False


def test_malformed_answer_gets_one_repair_retry_then_fails_loudly():
    class FlakyDriver:
        """Answers garbage first, the golden answer on repair."""

        def __init__(self):
            self.calls = 0

        def read_image(self, *, prompt, image_bytes, schema=None, tier="wonder"):
            from gpa.ai.client import ModelResult

            self.calls += 1
            if self.calls == 1:
                return ModelResult(text="?", data={"nope": True}, model_id="x", tier=tier)
            return ModelResult(
                text="", data=HARVEST_ANSWER, model_id="x", tier=tier
            )

    driver = FlakyDriver()
    reading = read_flyer_image(
        driver, image_bytes=IMAGE, group_timezone="UTC",
        source_created_at=datetime(2026, 10, 21),
    )
    assert driver.calls == 2
    assert reading.draft["title"] == "Harvest Supper"

    class AlwaysBadDriver:
        def read_image(self, *, prompt, image_bytes, schema=None, tier="wonder"):
            from gpa.ai.client import ModelResult

            return ModelResult(text="?", data={"nope": True}, model_id="x", tier=tier)

    with pytest.raises(ModelError):
        read_flyer_image(
            AlwaysBadDriver(), image_bytes=IMAGE, group_timezone="UTC",
            source_created_at=datetime(2026, 10, 21),
        )


def test_mock_driver_reads_an_empty_but_valid_draft():
    """The mock synthesises the smallest schema-valid answer —
    no items — so the machinery runs and drafts nothing."""
    reading = read_flyer_image(
        MockDriver(), image_bytes=IMAGE, group_timezone="UTC",
        source_created_at=datetime(2026, 10, 21),
    )
    assert reading.draft["fields"] == {}
    assert reading.transcript == "" and reading.alt_text == ""


# --- Segmentation: both driver modes (B-064) ----------------------

BOARD = b"fake-board-bytes"
BOXES = [
    {"box": [0.05, 0.05, 0.40, 0.60], "label": "red poster"},
    {"box": [0.55, 0.10, 0.38, 0.45], "label": "timetable"},
    {"box": [2.0, 0.0, 0.5, 0.5], "label": "off the board"},  # invalid: dropped
]


def test_sam_segmenter_against_a_stubbed_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/segment"
        return httpx.Response(200, json={"regions": BOXES})

    segmenter = SamSegmenter(
        base_url="https://sam.invalid", api_key="unused",
        transport=httpx.MockTransport(handler),
    )
    regions = segmenter.segment(image_bytes=BOARD)
    # The honest boxes survive, in order; the off-picture one is
    # dropped, never repaired into a guess.
    assert [r.label for r in regions] == ["red poster", "timetable"]
    assert regions[0].box == [0.05, 0.05, 0.40, 0.60]


def test_sam_segmenter_without_an_endpoint_is_unavailable_in_plain_words():
    with pytest.raises(SegmenterUnavailable):
        SamSegmenter(base_url="").segment(image_bytes=BOARD)


def test_vision_fallback_segmenter_reads_boxes_from_the_model():
    driver = FixtureDriver()
    schema = promptlib.load_prompt("board_segmentation").schema
    prompt = promptlib.render_prompt("board_segmentation")
    driver.add_image(prompt, BOARD, {"regions": BOXES}, schema=schema)
    regions = VisionBoxSegmenter(driver).segment(image_bytes=BOARD)
    assert [r.label for r in regions] == ["red poster", "timetable"]


def test_build_segmenter_defaults_to_the_vision_fallback():
    segmenter = build_segmenter(Settings(), MockDriver())
    assert isinstance(segmenter, VisionBoxSegmenter)
    # The mock's smallest valid answer has no regions: the board
    # machinery runs and cuts nothing — stated, not hidden.
    assert segmenter.segment(image_bytes=BOARD) == []
