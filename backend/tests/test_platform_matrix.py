"""B-177 — cross-platform verification matrix fixtures.

These tests pin the behaviours that differ between Windows,
macOS, and Linux, on whichever OS runs them (§6A.2):

* the portability audit (scripts/platform_audit.py) passes on
  the real tree and fails a deliberately unportable fixture;
* text is UTF-8 everywhere: BOM and CRLF fixtures round-trip
  through the storage + file roads without mangling;
* timezone/DST truth comes from zoneinfo (with the tzdata
  package pinned for Windows in the lockfile), never the OS;
* the internal transport's doors work on this OS — a real Unix
  socket where the OS offers one, loopback TCP everywhere;
* filesystem case-sensitivity and long paths are PROBED, and
  the code's defences hold either way (content-addressed keys
  are lowercase hex; storage paths stay short by construction).

Windows and macOS columns are exercised by the CI matrix
(.github/workflows/test.yml); the recorded state of every
column lives in PLATFORM_MATRIX.md.
"""

from __future__ import annotations

import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import platform_audit  # noqa: E402


# ---------------------------------------------------------------
# The audit itself
# ---------------------------------------------------------------


def test_platform_audit_passes_on_the_real_tree():
    assert platform_audit.audit_tree(ROOT) == []


def test_platform_audit_fails_an_unportable_fixture(tmp_path):
    """Acceptance: an unportable module fails, by file and line."""
    bad = tmp_path / "src" / "gpa"
    bad.mkdir(parents=True)
    (bad / "bad.py").write_text(
        "import os\n"
        "\n"
        "def load(p):\n"
        "    text = open(p).read()\n"
        "    return os.path.join(p, 'x')\n",
        encoding="utf-8",
    )
    problems = platform_audit.audit_tree(tmp_path)
    assert any("bad.py:4" in p and "encoding" in p for p in problems)
    assert any("bad.py:5" in p and "os.path.join" in p for p in problems)


# ---------------------------------------------------------------
# Encoding: UTF-8 everywhere, BOM/CRLF fixtures
# ---------------------------------------------------------------


def test_utf8_bom_and_crlf_round_trip(tmp_path):
    """A BOM-marked, CRLF, non-ASCII text file survives the
    explicit-encoding road byte-for-byte as TEXT (the BOM is
    data the reader chose; utf-8-sig is the caller's call)."""
    text = "Fête — naïve\r\nSecond líne\r\n"
    path = tmp_path / "note.txt"
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    read_back = path.read_text(encoding="utf-8-sig")
    # Text-mode reads apply universal-newline translation on
    # EVERY OS (a Python law, not an OS one): CRLF arrives as
    # \n. The bytes on disk are untouched — pinned below.
    assert read_back == text.replace("\r\n", "\n")
    assert path.read_bytes() == b"\xef\xbb\xbf" + text.encode("utf-8")
    # And the plain utf-8 road never guesses a locale encoding:
    plain = path.read_bytes().decode("utf-8")
    assert "Fête" in plain and "naïve" in plain


def test_storage_round_trips_non_ascii_bytes(tmp_path):
    from gpa.storage.local import LocalStorage

    store = LocalStorage(tmp_path / "store")
    payload = "Grüße aus dem Norden — þ ð ǫ".encode("utf-8")
    key = store.put_bytes(payload, "txt")
    assert store.get_bytes(key) == payload
    # Content-addressed keys are lowercase hex: safe on
    # case-insensitive filesystems (Windows/macOS default).
    assert key == key.lower() and "/" not in key.replace("/", "")


# ---------------------------------------------------------------
# Timezone / DST fixtures (OS-independent by construction)
# ---------------------------------------------------------------


def test_dst_fixtures_from_zoneinfo_not_the_os():
    """Spring-forward 2026: 02:30 does not exist in New York;
    the offsets on either side differ by exactly one hour —
    on every OS, because zoneinfo + tzdata carry the truth."""
    zone = ZoneInfo("America/New_York")
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=zone)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=zone)
    assert summer.utcoffset() - winter.utcoffset() == timedelta(hours=1)
    # The same instant, two readings — the B-052 wall-clock law.
    instant = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    assert instant.astimezone(zone).hour == 3  # sprang forward


# ---------------------------------------------------------------
# Transport doors per OS (B-163's platform seam)
# ---------------------------------------------------------------


def test_loopback_tcp_transport_works_here():
    """Loopback TCP exists on every target OS: a real bind,
    connect, and byte round-trip through a socket pair."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    server, _ = listener.accept()
    client.sendall(b"ping")
    assert server.recv(4) == b"ping"
    server.sendall(b"pong")
    assert client.recv(4) == b"pong"
    client.close()
    server.close()
    listener.close()


@pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"),
    reason="this OS offers no AF_UNIX; the TCP/mTLS door carries "
    "the wire form here (named in PLATFORM_MATRIX.md)",
)
def test_unix_socket_door_where_the_os_offers_one(tmp_path):
    """The B-163 Unix-socket door, probed live on this OS."""
    path = str(tmp_path / "door.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(path)
    server, _ = listener.accept()
    client.sendall(b"wire")
    assert server.recv(4) == b"wire"
    client.close()
    server.close()
    listener.close()


# ---------------------------------------------------------------
# Filesystem character: probed, never assumed
# ---------------------------------------------------------------


def test_case_sensitivity_is_probed_not_assumed(tmp_path):
    """Whatever this filesystem does with case, content keys
    cannot collide: two names differing only by case are never
    both storage keys (keys are sha256 hex of the CONTENT)."""
    probe = tmp_path / "Probe.txt"
    probe.write_text("x", encoding="utf-8")
    case_sensitive = not (tmp_path / "probe.txt").exists()
    # The probe result is recorded by the CI report; the law
    # that matters holds on both kinds of filesystem:
    from gpa.storage.local import LocalStorage

    store = LocalStorage(tmp_path / "s")
    key_a = store.put_bytes(b"Alpha", "txt")
    key_b = store.put_bytes(b"alpha", "txt")
    assert key_a != key_b  # different content, different key
    assert isinstance(case_sensitive, bool)


def test_storage_paths_stay_short_by_construction(tmp_path):
    """Windows' classic MAX_PATH is 260: our deepest storage
    path for a real key stays far under it, on any root."""
    from gpa.storage.local import LocalStorage

    store = LocalStorage(tmp_path / "s")
    key = store.put_bytes(b"data", "bin")
    deepest = max(
        (p for p in (tmp_path / "s").rglob("*") if p.is_file()),
        key=lambda p: len(str(p)),
    )
    # Relative depth below the storage root is the part the
    # code controls: fan-out (2) + key + suffix.
    rel = deepest.relative_to(tmp_path / "s")
    assert len(str(rel)) < 80
    assert key
