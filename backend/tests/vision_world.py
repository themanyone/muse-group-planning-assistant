"""A shared world for Phase 5 API tests: synthetic poster/board
images and a scripted vision driver.

The driver is a test double implementing the MetaModelClient
protocol honestly, the way the pipeline tests' FlakyDriver does:
it answers flyer readings from the poster's dominant colour
(each test poster wears a unique one), board segmentation from
a registered box list, and transcription from a registered
text. It records every call, and it can be told to fail for one
colour — B-069's glare-ruined poster.

The FixtureDriver path (recordings keyed by exact stored bytes)
is exercised by the Phase 5 gate test; this world exists because
a board's child crops are only knowable after segmentation has
run, so API tests dispatch on what a poster LOOKS like instead.
"""

import io

from PIL import Image, ImageDraw

from gpa.ai.client import ModelError, ModelResult
from gpa.ai import promptlib


def poster_bytes(colour: tuple[int, int, int], size=(400, 520)) -> bytes:
    """One synthetic poster: a coloured ground, a pale headline
    band, and dark text-ish bars. Deterministic per colour."""
    image = Image.new("RGB", size, color=colour)
    draw = ImageDraw.Draw(image)
    draw.rectangle([30, 30, size[0] - 30, 110], fill=(245, 240, 225))
    for i in range(6):
        y = 150 + i * 55
        draw.rectangle([40, y, size[0] - 40 - (i % 3) * 60, y + 22], fill=(30, 30, 30))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def board_bytes(posters: list[tuple[tuple[int, int, int], list[float]]],
                size=(1000, 800)) -> bytes:
    """One synthetic noticeboard: each poster pasted at its box
    ([x, y, w, h] fractions of the board). Posters are the same
    synthetic pictures, scaled into their rectangles."""
    board = Image.new("RGB", size, color=(70, 55, 40))
    for colour, box in posters:
        poster = Image.open(io.BytesIO(poster_bytes(colour))).resize(
            (int(box[2] * size[0]), int(box[3] * size[1]))
        )
        board.paste(poster, (int(box[0] * size[0]), int(box[1] * size[1])))
    buffer = io.BytesIO()
    board.save(buffer, "PNG")
    return buffer.getvalue()


def dominant_colour(image_bytes: bytes) -> tuple[int, int, int]:
    """The most common colour in the picture — a poster's
    identity in this world. Channels are quantised to a 16-grid
    because ingest re-encodes as JPEG: a poster's ground survives
    compression a few units off, and the grid absorbs the wobble.
    Test posters wear on-grid colours. (Board crops are dominated
    by their poster's ground; the board's own brown never wins a
    crop because crops are cut to the boxes.)"""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image.thumbnail((64, 64))
    counts: dict[tuple[int, int, int], int] = {}
    for pixel in image.getdata():
        key = tuple(min(255, round(channel / 16) * 16) for channel in pixel)
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get)


class ScriptedVisionDriver:
    """A MetaModelClient that answers from what pictures look
    like. ``readings`` maps a poster colour to its flyer_reading
    answer; ``board_regions`` is the segmentation answer for any
    board; ``fail_colours`` makes read_image raise ModelError —
    the glare-ruined poster of B-069."""

    driver_name = "scripted"

    def __init__(self):
        self.readings: dict[tuple[int, int, int], dict] = {}
        self.board_regions: list[dict] = []
        self.fail_colours: set[tuple[int, int, int]] = set()
        self.transcripts: dict[int, str] = {}  # by audio length
        self.calls: list[str] = []

    # --- the protocol -------------------------------------------
    def complete_text(self, *, prompt: str, tier: str = "spark") -> ModelResult:
        self.calls.append("complete_text")
        return ModelResult(text="(scripted)", model_id="scripted", tier=tier)

    def complete_json(self, *, prompt: str, schema: dict, tier: str = "spark") -> ModelResult:
        self.calls.append("complete_json")
        return ModelResult(
            text="{}", data={"items": []}, model_id="scripted", tier=tier
        )

    def read_image(self, *, prompt: str, image_bytes: bytes, schema=None, tier: str = "wonder") -> ModelResult:
        segmentation_prompt = promptlib.render_prompt("board_segmentation")
        if prompt == segmentation_prompt:
            self.calls.append("segment")
            return ModelResult(
                text="", data={"regions": self.board_regions},
                model_id="scripted", tier=tier,
            )
        self.calls.append("read_image")
        colour = dominant_colour(image_bytes)
        if colour in self.fail_colours:
            raise ModelError(f"scripted glare: the {colour} poster cannot be read")
        answer = self.readings.get(colour)
        if answer is None:
            raise ModelError(f"no scripted reading for poster colour {colour}")
        return ModelResult(text="", data=answer, model_id="scripted", tier=tier)

    def transcribe(self, *, audio_bytes: bytes, mime: str = "audio/wav", tier: str = "spark") -> ModelResult:
        self.calls.append("transcribe")
        text = self.transcripts.get(len(audio_bytes))
        if text is None:
            raise ModelError("no scripted transcript for this audio")
        return ModelResult(text=text, model_id="scripted", tier=tier)

    def generate_image(self, *, prompt: str, tier: str = "wonder") -> ModelResult:
        self.calls.append("generate_image")
        return ModelResult(
            text="", data={"image_bytes": poster_bytes((25, 90, 140)), "format": "png"},
            model_id="scripted", tier=tier,
        )


def flyer_answer(*, title, starts_phrase=None, starts_time=None, place=None,
                 cost=None, bring=None, host=None, transcript="", alt_text="",
                 links=None, confidence=0.9) -> dict:
    """Build one flyer_reading answer in the schema's shape, with
    plausible regions for the fields that carry values."""
    def field(value, region):
        return {"value": value, "confidence": confidence if value else 0.0,
                "region": region if value else None}

    starts = {"value": starts_phrase, "confidence": confidence if starts_phrase else 0.0,
              "region": [0.1, 0.3, 0.6, 0.08] if starts_phrase else None,
              "date_phrase": starts_phrase, "time_phrase": starts_time}
    return {
        "items": [
            {
                "fields": {
                    "title": field(title, [0.08, 0.05, 0.8, 0.12]),
                    "starts": starts,
                    "place": field(place, [0.1, 0.42, 0.5, 0.07]),
                    "cost": field(cost, [0.1, 0.55, 0.2, 0.06]),
                    "bring": field(bring, None),  # regionless on purpose
                    "host": field(host, [0.1, 0.65, 0.4, 0.06]),
                },
                "transcript": transcript,
                "alt_text": alt_text,
                "links": links or [],
            }
        ]
    }


def run_one_job(app, db_session) -> bool:
    """Run the oldest due job the way the worker would."""
    from gpa.services import jobs as job_service

    return job_service.process_one(db_session, app.state.settings, "test-worker")


def upload_image(client, headers, group_id, data: bytes, *, board=False, filename="poster.png"):
    """Upload one photo through the real ingest route."""
    suffix = "&board=true" if board else ""
    response = client.post(
        f"/api/v1/groups/{group_id}/sources/image?filename={filename}{suffix}",
        content=data, headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def request_read(client, headers, group_id, source_id):
    """POST the vision-read request; returns the job id."""
    response = client.post(
        f"/api/v1/groups/{group_id}/sources/{source_id}/read",
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]
