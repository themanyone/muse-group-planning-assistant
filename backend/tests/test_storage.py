"""Storage acceptance (B-007): content addressing, dedupe, atomics,
and the path-traversal proofs."""

import pytest

from gpa.errors import AppError
from gpa.storage.local import LocalStorage


@pytest.fixture()
def store(tmp_path):
    return LocalStorage(tmp_path / "store")


def test_put_get_round_trip(store):
    key = store.put_bytes(b"hello planning", extension="txt")
    assert store.exists(key)
    assert store.get_bytes(key) == b"hello planning"


def test_same_content_same_key(store):
    """Content addressing: identical bytes are stored once."""
    assert store.put_bytes(b"same") == store.put_bytes(b"same")


def test_missing_key_is_not_found(store):
    with pytest.raises(AppError) as excinfo:
        store.get_bytes("0" * 64)
    assert excinfo.value.code == "not_found"


@pytest.mark.parametrize(
    "attack",
    [
        "../etc/passwd",
        "..\\windows\\system32",
        "/etc/passwd",
        "a" * 63,  # too short to be a hash
        "g" * 64,  # right length, not hex
        "A" * 64 + "/../x",  # traversal tail
        "",
    ],
)
def test_traversal_attacks_rejected(store, attack):
    """Anything that is not exactly a content address is refused
    before a filesystem is ever touched (B-007 acceptance)."""
    with pytest.raises(AppError):
        store.get_bytes(attack)
    with pytest.raises(AppError):
        store.exists(attack)
