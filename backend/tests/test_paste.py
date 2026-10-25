"""Paste ingest golden files (B-024): one realistic export per
format — WhatsApp (both shapes), Discord, Slack, Messenger, generic
Name: lines, and plain text — plus the never-drop law and one
end-to-end paste through the API."""

from datetime import datetime

from conftest import bearer, make_group, register
from gpa.services.paste import parse_paste


def test_whatsapp_bracket_golden():
    chunks = parse_paste(
        "[24/10/2026, 18:30:12] Alice Example: See you Saturday\n"
        "[24/10/2026, 18:31:02] Bob Example: Bring chairs\n"
    )
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Alice Example", "See you Saturday"),
        ("Bob Example", "Bring chairs"),
    ]
    assert chunks[0]["sent_at"] == datetime(2026, 10, 24, 18, 30, 12)
    assert chunks[0]["timestamp_text"] == "24/10/2026, 18:30:12"


def test_whatsapp_dash_golden():
    chunks = parse_paste(
        "24/10/2026, 18:30 - Alice Example: See you Saturday\n"
        "24/10/2026, 18:31 - Bob Example: Bring chairs\n"
    )
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Alice Example", "See you Saturday"),
        ("Bob Example", "Bring chairs"),
    ]
    assert chunks[0]["sent_at"] == datetime(2026, 10, 24, 18, 30)


def test_discord_golden():
    chunks = parse_paste(
        "Alice — 24/10/2026 18:30\n"
        "See you all Saturday\n"
        "Bob — 24/10/2026 18:31\n"
        "Will do\n"
    )
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Alice", "See you all Saturday"),
        ("Bob", "Will do"),
    ]
    assert chunks[0]["sent_at"] == datetime(2026, 10, 24, 18, 30)


def test_slack_golden():
    chunks = parse_paste(
        "Alice Example  6:30 PM\n"
        "See you Saturday\n"
        "Bob Example  6:31 PM\n"
        "Bring chairs\n"
    )
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Alice Example", "See you Saturday"),
        ("Bob Example", "Bring chairs"),
    ]
    # A bare clock time has no date: it stays raw words, unparsed —
    # resolving it belongs to the date resolver, not ingest.
    assert chunks[0]["timestamp_text"] == "6:30 PM"
    assert chunks[0]["sent_at"] is None


def test_messenger_golden():
    chunks = parse_paste(
        "Alice Example\n"
        "Are we meeting Saturday?\n"
        "Oct 24, 2026, 6:30 PM\n"
        "Bob Example\n"
        "Yes, at the hall\n"
        "Oct 24, 2026, 6:31 PM\n"
    )
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Alice Example", "Are we meeting Saturday?"),
        ("Bob Example", "Yes, at the hall"),
    ]
    assert chunks[0]["sent_at"] == datetime(2026, 10, 24, 18, 30)


def test_name_colon_golden():
    chunks = parse_paste("Alice: See you Saturday\nBob: Bring chairs\n")
    assert [(c["sender"], c["text"]) for c in chunks] == [
        ("Alice", "See you Saturday"),
        ("Bob", "Bring chairs"),
    ]


def test_wrapped_lines_and_leading_text_are_never_dropped():
    chunks = parse_paste(
        "a note before anyone speaks\n"
        "Alice: This is a long message\n"
        "that wraps onto a second line\n"
    )
    assert chunks[0]["sender"] is None
    assert chunks[0]["text"] == "a note before anyone speaks"
    assert chunks[1]["sender"] == "Alice"
    assert chunks[1]["text"] == "This is a long message\nthat wraps onto a second line"


def test_plain_text_is_preserved_verbatim():
    chunks = parse_paste("just some words\nwith no speakers at all\n")
    assert len(chunks) == 1
    assert chunks[0]["sender"] is None
    assert chunks[0]["text"] == "just some words\nwith no speakers at all"


def test_paste_through_the_api(client):
    owner = register(client)
    group = make_group(client, owner)
    response = client.post(
        f"/api/v1/groups/{group['id']}/sources/paste",
        json={
            "title": "Pasted thread",
            "text": "[24/10/2026, 18:30:12] Alice Example: See you Saturday\n",
            "granted_scope": "this-thread-only",
        },
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "chat_paste"
    assert body["chunks"][0]["sender"] == "Alice Example"
    assert body["chunks"][0]["sent_at"] == "2026-10-24T18:30:12"
