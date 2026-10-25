"""Image ingest tests (roadmap B-027, B-028).

A flyer photo is normalised (EXIF orientation applied — a rotated
phone photo reads upright), thumbnailed, and described-honestly:
its alt text starts as the placeholder and its status says so.
A board photo is ONE source awaiting segmentation, with one
placeholder chunk holding its regions' place.
"""

import io
import json

from PIL import Image

from conftest import bearer, make_group, register


def _jpeg(width: int, height: int, *, orientation: int | None = None) -> bytes:
    """A small JPEG made in-test; optionally carrying an EXIF
    orientation tag, like a sideways phone photo does."""
    image = Image.new("RGB", (width, height), (180, 40, 40))
    buffer = io.BytesIO()
    if orientation is None:
        image.save(buffer, "JPEG")
    else:
        exif = Image.Exif()
        exif[274] = orientation  # The Orientation tag.
        image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def _upload(client, owner, group, data: bytes, filename="flyer.jpg", **params):
    return client.post(
        f"/api/v1/groups/{group['id']}/sources/image",
        params={"filename": filename, **params},
        content=data,
        headers={**bearer(owner), "Content-Type": "image/jpeg"},
    )


def test_flyer_upload_creates_flyer_with_awaiting_description(client, app):
    owner = register(client)
    group = make_group(client, owner)
    response = _upload(client, owner, group, _jpeg(80, 40))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "flyer"
    assert body["status"] == "ready"
    flyer = body["flyer"]
    assert flyer is not None
    assert (flyer["width"], flyer["height"]) == (80, 40)
    # The visible defect, on purpose: no real description yet.
    assert flyer["alt_text"] == "awaiting description"
    assert flyer["alt_text_status"] == "awaiting_description"
    # The thumbnail really exists in storage.
    assert flyer["thumbnail_key"]
    assert app.state.storage.exists(flyer["thumbnail_key"])
    assert app.state.storage.exists(body["storage_key"])


def test_rotated_phone_photo_reads_upright(client):
    owner = register(client)
    group = make_group(client, owner)
    # Stored pixels are 80 wide x 40 tall, but the EXIF orientation
    # says "rotate me": as a person sees it, the photo is 40 x 80.
    response = _upload(client, owner, group, _jpeg(80, 40, orientation=6))
    assert response.status_code == 201, response.text
    flyer = response.json()["flyer"]
    assert (flyer["width"], flyer["height"]) == (40, 80)


def test_board_photo_is_one_source_awaiting_segmentation(client):
    owner = register(client)
    group = make_group(client, owner)
    response = _upload(client, owner, group, _jpeg(120, 90), board="true")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "board_photo"
    assert body["status"] == "awaiting_segmentation"
    assert body["stage"] == "awaiting_segmentation"
    # One placeholder chunk holds the regions' place — not twelve
    # fake sources, not zero trace of the board's content.
    assert body["chunk_count"] == 1
    marker = json.loads(body["chunks"][0]["region_json"])
    assert marker == {"placeholder": True}
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert len(listed.json()) == 1


def test_image_ingest_rejects_a_program_wearing_a_jpg_name(client):
    owner = register(client)
    group = make_group(client, owner)
    response = _upload(client, owner, group, b"MZ" + b"\x00" * 64, filename="flyer.jpg")
    assert response.status_code == 422
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert listed.json() == []
