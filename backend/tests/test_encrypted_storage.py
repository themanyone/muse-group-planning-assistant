"""Encrypted file storage tests (roadmap B-168).

The acceptance: *a raw-upload grep test — an uploaded
picture's bytes and a planted text file's contents are absent
from disk storage.* Plus the driver's own laws: the B-007
contract survives (content-addressed keys, dedup), legacy
objects still read, and a wrong master key opens nothing.
"""

import hashlib

import pytest

from conftest import make_settings
from gpa.errors import AppError
from gpa.storage.encrypted import EncryptedStorage, build_storage
from gpa.storage.local import LocalStorage

PLANTED_TEXT = b"the lantern festival budget is hidden in this note"
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"UNIQUEPIXELS" * 64


def _driver(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    inner = LocalStorage(tmp_path / "store")
    return EncryptedStorage(inner, settings), inner, settings


def _all_disk_bytes(root) -> bytes:
    blob = b""
    for path in sorted(root.rglob("*")):
        if path.is_file():
            blob += path.read_bytes()
    return blob


def test_raw_upload_grep_finds_nothing(tmp_path):
    """THE B-168 acceptance test: a planted text file and a
    picture's bytes go through the driver; the disk holds
    neither, while reads return both whole."""
    driver, inner, _ = _driver(tmp_path)
    text_key = driver.put_bytes(PLANTED_TEXT, "txt")
    png_key = driver.put_bytes(FAKE_PNG, "png")

    # The contract first: content-addressed keys, as always.
    assert text_key == hashlib.sha256(PLANTED_TEXT).hexdigest() + ".txt"
    assert png_key == hashlib.sha256(FAKE_PNG).hexdigest() + ".png"

    disk = _all_disk_bytes(tmp_path / "store")
    assert PLANTED_TEXT not in disk
    assert b"lantern festival budget" not in disk
    assert FAKE_PNG not in disk
    assert b"UNIQUEPIXELS" not in disk
    assert disk.startswith(b"GPAE1") or b"GPAE1" in disk  # the seal is what's there

    assert driver.get_bytes(text_key) == PLANTED_TEXT
    assert driver.get_bytes(png_key) == FAKE_PNG


def test_dedup_and_legacy_objects_survive(tmp_path):
    """Identical bytes stored twice: one object, both reads
    whole. An object written by the plaintext driver (before
    sealing) still reads through the encrypted one."""
    driver, inner, settings = _driver(tmp_path)
    key1 = driver.put_bytes(PLANTED_TEXT, "txt")
    key2 = driver.put_bytes(PLANTED_TEXT, "txt")
    assert key1 == key2
    files = [p for p in (tmp_path / "store").rglob("*") if p.is_file()]
    assert len(files) == 1

    legacy_key = inner.put_bytes(b"written before the seal", "txt")
    assert driver.get_bytes(legacy_key) == b"written before the seal"


def test_wrong_master_opens_nothing(tmp_path):
    """A different deployment's master key — a stolen disk's
    fate — gets a tamper refusal, not data."""
    driver, inner, _ = _driver(tmp_path)
    key = driver.put_bytes(PLANTED_TEXT, "txt")
    other = EncryptedStorage(
        inner, make_settings(tmp_path, secret_key="a-different-secret")
    )
    with pytest.raises(AppError) as caught:
        other.get_bytes(key)
    assert caught.value.code == "internal_tampered"


def test_build_storage_honours_the_flag(tmp_path):
    plain = build_storage(make_settings(tmp_path))
    assert type(plain) is LocalStorage
    sealed = build_storage(make_settings(tmp_path, encrypt_storage=True))
    assert isinstance(sealed, EncryptedStorage)


def test_app_level_upload_is_sealed_on_disk(tmp_path):
    """Through the real app: an encrypted-storage deployment
    takes an upload, the file on disk is sealed, and the
    product serves it back whole."""
    from conftest import bearer, make_group, register
    from fastapi.testclient import TestClient
    from gpa.main import create_app

    settings = make_settings(tmp_path, encrypt_storage=True)
    app = create_app(settings)
    with TestClient(app) as client:
        owner = register(client)
        group = make_group(client, owner)
        up = client.post(
            f"/api/v1/groups/{group['id']}/sources/upload",
            params={"filename": "note.txt"},
            content=PLANTED_TEXT,
            headers={**bearer(owner), "Content-Type": "application/octet-stream"},
        )
        assert up.status_code == 201, up.text
        disk = _all_disk_bytes(settings.storage_dir)
        assert b"lantern festival budget" not in disk
        assert b"GPAE1" in disk
        # The asset's key, read from the source's row as
        # storage's callers do, opens to the planted words.
        from gpa.models.sources import Source

        session = app.state.db.session()
        try:
            source = session.query(Source).filter(Source.id == up.json()["id"]).one()
            key = source.storage_key
        finally:
            session.close()
        assert key and app.state.storage.get_bytes(key) == PLANTED_TEXT
