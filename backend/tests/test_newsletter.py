"""Newsletter ingest tests (roadmap B-026).

The golden newsletter carries twelve events as twelve headed
sections; ingest must keep them twelve, in order, with links kept
whole (anchor + URL) and images replaced by their alt text — or by
the marked placeholder when the sender gave none.
"""

from pathlib import Path

from conftest import bearer, make_group, register

GOLDEN = Path(__file__).parent / "golden" / "newsletter.html"

EVENT_TITLES = [
    "Event One — Harvest Supper",
    "Event Two — Projector Night",
    "Event Three — Garden Tidy",
    "Event Four — Book Club",
    "Event Five — Choir Practice",
    "Event Six — Repair Café",
    "Event Seven — Lantern Walk",
    "Event Eight — Baking Morning",
    "Event Nine — Story Night",
    "Event Ten — River Clean-Up",
    "Event Eleven — Games Evening",
    "Event Twelve — Winter Planning",
]


def _upload(client, owner, group, data: bytes, filename="newsletter.html"):
    return client.post(
        f"/api/v1/groups/{group['id']}/sources/newsletter",
        params={"filename": filename},
        content=data,
        headers={**bearer(owner), "Content-Type": "text/html"},
    )


def test_golden_newsletter_twelve_events_stay_twelve_sections(client):
    owner = register(client)
    group = make_group(client, owner)
    response = _upload(client, owner, group, GOLDEN.read_bytes())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "newsletter"
    chunks = body["chunks"]
    # The greeting paragraph is its own preamble section; after it,
    # the twelve events are twelve sections, in the email's order.
    assert chunks[0]["text"].startswith("Hello everyone")
    event_chunks = chunks[1:]
    assert len(event_chunks) == 12
    for chunk, title in zip(event_chunks, EVENT_TITLES):
        assert chunk["text"].splitlines()[0] == title


def test_newsletter_links_kept_with_anchor_text(client):
    owner = register(client)
    group = make_group(client, owner)
    body = _upload(client, owner, group, GOLDEN.read_bytes()).json()
    projector_night = body["chunks"][2]["text"]
    assert "See the film list (https://example.org/films)" in projector_night


def test_newsletter_images_become_alt_text_or_marked_placeholder(client):
    owner = register(client)
    group = make_group(client, owner)
    body = _upload(client, owner, group, GOLDEN.read_bytes()).json()
    projector_night = body["chunks"][2]["text"]
    garden_tidy = body["chunks"][3]["text"]
    # The described image speaks its description; the undescribed
    # one is a visible, marked gap — never silently absent.
    assert "[image: A poster of a harvest moon over the hall]" in projector_night
    assert "[image: no description]" in garden_tidy


def test_newsletter_script_and_style_never_become_chunks(client):
    owner = register(client)
    group = make_group(client, owner)
    body = _upload(client, owner, group, GOLDEN.read_bytes()).json()
    whole = "\n".join(c["text"] for c in body["chunks"])
    assert "tracking" not in whole
    assert "font-family" not in whole


def test_newsletter_rejects_plain_text_and_empty(client):
    owner = register(client)
    group = make_group(client, owner)
    plain = _upload(client, owner, group, b"just words, no html at all")
    assert plain.status_code == 422
    empty = _upload(client, owner, group, b"")
    assert empty.status_code == 422
    # A refused newsletter leaves no source behind.
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert listed.json() == []
