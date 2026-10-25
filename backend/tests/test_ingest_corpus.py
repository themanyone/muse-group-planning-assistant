"""The Phase 2 golden corpus (roadmap B-034, the phase gate).

One group receives one of everything Phase 2 can ingest — a chat
paste, the golden newsletter, three flyer images (one of them a
sideways phone photo), a board photo, and a voice note — and the
whole pile must land clean: right kinds, right counts, right
states, one permission record apiece. If any ingest regresses,
this is the test that says so in one place.
"""

import io
import wave
from collections import Counter
from pathlib import Path

from PIL import Image

from conftest import bearer, make_group, register

GOLDEN_NEWSLETTER = Path(__file__).parent / "golden" / "newsletter.html"

OCTET = {"Content-Type": "application/octet-stream"}


def _image(fmt: str, size=(80, 40), orientation: int | None = None) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color=(90, 60, 30))
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        image.save(buffer, fmt, exif=exif)
    else:
        image.save(buffer, fmt)
    return buffer.getvalue()


def _wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 40)
    return buffer.getvalue()


def test_the_whole_corpus_ingests_clean(client):
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    headers = {**bearer(owner), **OCTET}
    created = []

    # 1. A chat paste (WhatsApp shape, two speakers).
    paste = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={
            "text": (
                "[12/10/26, 9:00:00 AM] Ana: Supper is Saturday at seven\n"
                "[12/10/26, 9:01:12 AM] Ben: I will bring the projector"
            )
        },
        headers=bearer(owner),
    )
    assert paste.status_code == 201, paste.text
    assert paste.json()["chunk_count"] == 2
    created.append(paste.json())

    # 2. The golden newsletter — twelve events, twelve sections.
    newsletter = client.post(
        f"/api/v1/groups/{gid}/sources/newsletter?filename=october.html",
        content=GOLDEN_NEWSLETTER.read_bytes(),
        headers=headers,
    )
    assert newsletter.status_code == 201, newsletter.text
    assert newsletter.json()["chunk_count"] == 13  # Greeting + twelve.
    created.append(newsletter.json())

    # 3. Three flyers: PNG, JPEG, and a sideways phone photo.
    for name, data in (
        ("supper.png", _image("PNG")),
        ("walk.jpg", _image("JPEG")),
        ("sideways.jpg", _image("JPEG", orientation=6)),
    ):
        flyer = client.post(
            f"/api/v1/groups/{gid}/sources/image?filename={name}",
            content=data,
            headers=headers,
        )
        assert flyer.status_code == 201, flyer.text
        body = flyer.json()
        assert body["flyer"]["alt_text_status"] == "awaiting_description"
        created.append(body)
    # The sideways photo reads upright: 80x40 stored as 40x80.
    assert created[-1]["flyer"]["width"] == 40
    assert created[-1]["flyer"]["height"] == 80

    # 4. One board photo: one source, one placeholder chunk.
    board = client.post(
        f"/api/v1/groups/{gid}/sources/image?filename=board.jpg&board=true",
        content=_image("JPEG", size=(200, 120)),
        headers=headers,
    )
    assert board.status_code == 201, board.text
    assert board.json()["status"] == "awaiting_segmentation"
    assert board.json()["chunk_count"] == 1
    created.append(board.json())

    # 5. One voice note, waiting honestly for transcription.
    voice = client.post(
        f"/api/v1/groups/{gid}/sources/voice?filename=note.wav",
        content=_wav(),
        headers=headers,
    )
    assert voice.status_code == 201, voice.text
    assert voice.json()["transcription_status"] == "queued"
    created.append(voice.json())

    # The whole pile, seen as the group sees it.
    listed = client.get(f"/api/v1/groups/{gid}/sources", headers=bearer(owner))
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 7
    assert Counter(r["kind"] for r in rows) == Counter(
        {"chat_paste": 1, "newsletter": 1, "flyer": 3, "board_photo": 1, "voice": 1}
    )
    # Every item carries its permission record and a stage.
    assert all(r["granted_by"] == f"user:{owner['user_id']}" for r in rows)
    assert all(r["stage"] for r in rows)
