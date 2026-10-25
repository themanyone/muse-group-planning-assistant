"""File ingest acceptance (B-025): sniffing beats extension, the
virus hook refuses hostile bytes, a FileAsset row + stored bytes +
extracted chunks land together, and each readable kind extracts."""

import hashlib
import zlib

from conftest import bearer, make_group, register


def _upload(client, owner, group_id, data: bytes, filename: str = "notes.txt"):
    return client.post(
        f"/api/v1/groups/{group_id}/sources/upload",
        params={"filename": filename},
        content=data,
        headers={**bearer(owner), "Content-Type": "application/octet-stream"},
    )


def _minimal_pdf(text: str) -> bytes:
    """A tiny honest PDF whose text layer says ``text`` (one Tj)."""
    stream = f"BT /F1 12 Tf ({text}) Tj ET".encode("latin-1")
    compressed = zlib.compress(stream)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(compressed)).encode() + b" /Filter /FlateDecode >>\n"
        b"stream\n" + compressed + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF"
    ).encode()
    return bytes(out)


def test_text_upload_creates_asset_source_and_chunks(client, app):
    owner = register(client)
    group = make_group(client, owner)
    data = b"First paragraph about Saturday.\n\nSecond paragraph about chairs."
    response = _upload(client, owner, group["id"], data, "notes.txt")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "file"
    assert [c["text"] for c in body["chunks"]] == [
        "First paragraph about Saturday.",
        "Second paragraph about chairs.",
    ]
    # The bytes are really in storage, under their content address,
    # and the source names the asset that describes them.
    # (Superseded at B-141: the response no longer carries the
    # storage key — keys are internal locators, D-17 — so the
    # key is read from the source's own row, as storage's
    # callers do.)
    from gpa.models.sources import Source

    session = app.state.db.session()
    try:
        row = session.get(Source, body["id"])
        assert row.storage_key is not None
        assert app.state.storage.get_bytes(row.storage_key) == data
    finally:
        session.close()
    assert body["file_asset_id"] is not None

    from gpa.models.core import FileAsset

    session = app.state.db.session()
    asset = session.get(FileAsset, body["file_asset_id"])
    assert asset.sha256 == hashlib.sha256(data).hexdigest()
    assert asset.size == len(data)
    session.close()


def test_html_upload_extracts_readable_text(client):
    owner = register(client)
    group = make_group(client, owner)
    html = (
        b"<html><head><style>.x{color:red}</style></head><body>"
        b"<h1>Hall Meetup</h1><p>See you <b>Saturday</b>.</p>"
        b"<script>var secret = 1;</script></body></html>"
    )
    response = _upload(client, owner, group["id"], html, "page.html")
    assert response.status_code == 201, response.text
    texts = [c["text"] for c in response.json()["chunks"]]
    assert "Hall Meetup" in texts
    assert "See you Saturday ." in texts or "See you Saturday." in texts
    assert all("secret" not in t for t in texts)


def test_pdf_upload_reads_the_text_layer(client):
    owner = register(client)
    group = make_group(client, owner)
    response = _upload(client, owner, group["id"], _minimal_pdf("Hall meetup Saturday"), "flyer.pdf")
    assert response.status_code == 201, response.text
    assert any("Hall meetup Saturday" in c["text"] for c in response.json()["chunks"])


def test_ics_upload_makes_one_chunk_per_event(client):
    owner = register(client)
    group = make_group(client, owner)
    ics = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        b"SUMMARY:Hall Meetup\r\nDTSTART:20261024T183000Z\r\n"
        b"LOCATION:Village Hall\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    response = _upload(client, owner, group["id"], ics, "events.ics")
    assert response.status_code == 201, response.text
    texts = [c["text"] for c in response.json()["chunks"]]
    assert len(texts) == 1 and "Hall Meetup" in texts[0] and "Village Hall" in texts[0]


def test_disguised_executable_is_rejected_by_content_not_extension(client):
    """The B-025 law: MZ bytes named .txt are a program, refused."""
    owner = register(client)
    group = make_group(client, owner)
    response = _upload(client, owner, group["id"], b"MZ\x90\x00 fake program bytes", "notes.txt")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert listed.json() == []  # A refused file leaves nothing behind.


def test_virus_signature_hook_refuses_eicar(client):
    owner = register(client)
    group = make_group(client, owner)
    payload = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    response = _upload(client, owner, group["id"], payload, "innocent.txt")
    assert response.status_code == 422


def test_oversize_upload_is_refused(client, settings):
    owner = register(client)
    group = make_group(client, owner)
    response = _upload(client, owner, group["id"], b"x" * (settings.max_upload_bytes + 1), "big.txt")
    assert response.status_code == 422
