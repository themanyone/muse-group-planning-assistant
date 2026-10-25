"""Upload attack suite (roadmap B-141).

Phase 13 re-attacks the ingest roads B-025/B-027 built:

- content sniffing: executables in costume, the EICAR string,
  archives the reader never agreed to open;
- image re-encode: a picture carrying a payload (bytes after
  the image end, a text chunk) must be stored WITHOUT the
  payload — the stored image is a re-encode, never the
  original bytes;
- per-group upload quotas: refused in plain words, at the
  group's own dial, and no other group feels it;
- downloads: there is no "guess the file" road. Stored bytes
  are reachable only through services acting for a member;
  storage keys are internal locators (D-17) and no longer
  appear in source responses at all.
"""

import io
import wave

from conftest import bearer, make_group, register
from PIL import Image
from PIL.PngImagePlugin import PngInfo

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _upload_file(client, owner, gid, data, filename):
    return client.post(
        f"/api/v1/groups/{gid}/sources/upload",
        params={"filename": filename},
        content=data,
        headers={**bearer(owner), "Content-Type": "application/octet-stream"},
    )


def _upload_image(client, owner, gid, data, filename="poster.png"):
    return client.post(
        f"/api/v1/groups/{gid}/sources/image",
        params={"filename": filename},
        content=data,
        headers={**bearer(owner), "Content-Type": "image/png"},
    )


def _png(payload_after: bytes = b"", text_chunk: str | None = None) -> bytes:
    buffer = io.BytesIO()
    info = PngInfo()
    if text_chunk:
        info.add_text("Comment", text_chunk)
    Image.new("RGB", (24, 24), (200, 40, 40)).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue() + payload_after


def _stored_key(app, source_id: str) -> str:
    from gpa.models.sources import Source

    session = app.state.db.session()
    try:
        return session.get(Source, source_id).storage_key
    finally:
        session.close()


# ------------------------------------------------------------------
# Sniffing: costumes come off
# ------------------------------------------------------------------


def test_executables_in_costume_are_refused(client):
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    attacks = {
        "windows exe as text": (b"MZ\x90\x00" + b"\x00" * 64, "notes.txt"),
        "linux elf as pdf": (b"\x7fELF" + b"\x00" * 64, "agenda.pdf"),
        "eicar in a text file": (b"hello " + EICAR, "greetings.txt"),
        "zip archive as text": (b"PK\x03\x04" + b"\x00" * 64, "bundle.txt"),
    }
    for name, (data, filename) in attacks.items():
        response = _upload_file(client, owner, gid, data, filename)
        assert response.status_code == 422, (name, response.text)
    # Nothing was stored by any of them.
    listed = client.get(f"/api/v1/groups/{gid}/sources", headers=bearer(owner))
    assert listed.json() == []


def test_pdf_script_is_data_never_a_program(client):
    """A PDF may carry a /JavaScript action; here it is inert:
    extraction reads only the text layer, the script words
    never become a chunk, and no route serves the file back
    for anyone's reader to execute."""
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /OpenAction 2 0 R >> endobj\n"
        b"2 0 obj << /S /JavaScript /JS (app.alert('pwned')) >> endobj\n"
        b"3 0 obj << /Length 44 >> stream\n"
        b"BT /F1 12 Tf (Picnic on Saturday) Tj ET\n"
        b"endstream endobj\ntrailer << /Root 1 0 R >>\n%%EOF"
    )
    response = _upload_file(client, owner, gid, pdf, "poster.pdf")
    assert response.status_code == 201, response.text
    texts = [c["text"] for c in response.json()["chunks"]]
    assert any("Picnic" in t for t in texts)
    assert not any("JavaScript" in t or "app.alert" in t for t in texts)


# ------------------------------------------------------------------
# Images: the re-encode strips what rides along
# ------------------------------------------------------------------


def test_image_payloads_do_not_survive_reencode(client, app):
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    payload = b"PAYLOAD-AFTER-IEND-<script>alert(1)</script>"
    response = _upload_image(
        client, owner, gid, _png(payload_after=payload, text_chunk="TEXTCHUNK-PAYLOAD")
    )
    assert response.status_code == 201, response.text
    stored = app.state.storage.get_bytes(_stored_key(app, response.json()["id"]))
    assert payload not in stored
    assert b"TEXTCHUNK-PAYLOAD" not in stored
    # …and the stored bytes are still a readable picture (the
    # pipeline's own normalised re-encode — JPEG on this road).
    opened = Image.open(io.BytesIO(stored))
    assert opened.size == (24, 24)


# ------------------------------------------------------------------
# Quotas: the group's own dial, in plain words
# ------------------------------------------------------------------


def test_group_upload_quota_bites_and_lifts(client):
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    headers = bearer(owner)

    view = client.get(f"/api/v1/groups/{gid}/settings", headers=headers).json()
    assert view["upload_quota_bytes"] == 104_857_600  # the default cap

    first = b"First file, small and welcome."
    assert _upload_file(client, owner, gid, first, "one.txt").status_code == 201

    # Set the quota a little above what's used; a bigger file
    # must now refuse, a smaller one must still land.
    used = len(first)
    response = client.put(
        f"/api/v1/groups/{gid}/settings",
        json={"upload_quota_bytes": used + 50}, headers=headers,
    )
    assert response.status_code == 200, response.text
    refused = _upload_file(client, owner, gid, b"x" * 100, "two.txt")
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "quota_exceeded"
    assert "quota" in refused.json()["detail"].lower()
    assert _upload_file(client, owner, gid, b"tiny", "three.txt").status_code == 201

    # Another group, same server, feels nothing.
    other = make_group(client, owner)
    big = _upload_file(client, owner, other["id"], b"x" * 100, "fine.txt")
    assert big.status_code == 201, big.text

    # And null lifts the cap entirely.
    lifted = client.put(
        f"/api/v1/groups/{gid}/settings",
        json={"upload_quota_bytes": None}, headers=headers,
    )
    assert lifted.status_code == 200
    assert lifted.json()["upload_quota_bytes"] is None
    assert _upload_file(client, owner, gid, b"x" * 100, "four.txt").status_code == 201


def test_voice_upload_respects_the_quota_too(client):
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    client.put(
        f"/api/v1/groups/{gid}/settings",
        json={"upload_quota_bytes": 10}, headers=bearer(owner),
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 100)
    response = client.post(
        f"/api/v1/groups/{gid}/sources/voice",
        params={"filename": "note.wav"},
        content=buffer.getvalue(),
        headers={**bearer(owner), "Content-Type": "audio/wav"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "quota_exceeded"


# ------------------------------------------------------------------
# Downloads: no road guesses a file
# ------------------------------------------------------------------


def test_no_guessed_url_serves_a_stored_file(client, app):
    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    uploaded = _upload_file(client, owner, gid, b"Minutes of the meeting.", "m.txt")
    assert uploaded.status_code == 201
    body = uploaded.json()
    key = _stored_key(app, body["id"])
    # The key itself is gone from the API (B-141/D-17)…
    assert "storage_key" not in body
    detail = client.get(f"/api/v1/groups/{gid}/sources/{body['id']}", headers=bearer(owner))
    assert key not in detail.text
    # …and no plausible URL serves the bytes, even to the owner.
    for path in (
        f"/api/v1/groups/{gid}/files/{body['file_asset_id']}",
        f"/api/v1/groups/{gid}/sources/{body['id']}/download",
        f"/api/v1/groups/{gid}/sources/{body['id']}/file",
        f"/api/v1/files/{key}",
        f"/api/v1/storage/{key}",
        f"/storage/{key}",
        f"/media/{key}",
    ):
        response = client.get(path, headers=bearer(owner))
        assert response.status_code in (404, 405), (path, response.status_code)
        assert "Minutes of the meeting" not in response.text


def test_ics_download_door_equals_the_view_door(client):
    """The one real file download (an event's .ics) opens for
    exactly who may view the event: a draft refuses a member,
    a private group's event refuses a stranger, and the
    organiser's own download works (the positive control)."""
    from test_groups import invite_and_accept

    owner = register(client)
    group = make_group(client, owner)
    gid = group["id"]
    member = invite_and_accept(client, owner, gid, "member-ics@example.org", "member")
    draft = client.post(
        f"/api/v1/groups/{gid}/events",
        json={"title": "Draft", "status": "draft",
              "starts_at": "2026-11-06T18:30:00-05:00",
              "ends_at": "2026-11-06T21:00:00-05:00"},
        headers=bearer(owner),
    ).json()
    published = client.post(
        f"/api/v1/groups/{gid}/events",
        json={"title": "Published",
              "starts_at": "2026-11-06T18:30:00-05:00",
              "ends_at": "2026-11-06T21:00:00-05:00"},
        headers=bearer(owner),
    ).json()
    stranger = register(client)

    draft_ics = f"/api/v1/groups/{gid}/events/{draft['id']}/ics"
    assert client.get(draft_ics, headers=bearer(member)).status_code == 404
    # Drafts refuse for EVERY role, the owner included (B-053's
    # bright line: a file download is a publishing-out road,
    # narrower than the in-app view door on purpose).
    assert client.get(draft_ics, headers=bearer(owner)).status_code == 404
    pub_ics = f"/api/v1/groups/{gid}/events/{published['id']}/ics"
    assert client.get(pub_ics, headers=bearer(stranger)).status_code == 404
    assert client.get(pub_ics, headers=bearer(member)).status_code == 200
    assert client.get(pub_ics, headers=bearer(owner)).status_code == 200
