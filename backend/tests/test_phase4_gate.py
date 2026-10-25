"""The Phase 4 gate (roadmap B-062).

The full path on golden data, end to end in one group:

    flyer photo in → the group's chat extracted → the event
    candidate approved in the queue → a real PUBLISHED event,
    carrying the flyer → its .ics downloaded and parsed back
    to the same times → discussion live under it → an RSVP
    answered → the agenda and the tonight view showing it.

(The flyer's own vision reading is Phase 5; the gate links the
flyer to the event the way Phase 5's pipeline will, and asserts
everything Phase 4 owes around that link.)
"""

import io

from PIL import Image

from conftest import bearer
from extraction_world import build_world, candidates_by_kind, run_extraction_job
from gpa.models.events import Event
from gpa.services import ical as ical_service


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), color=(90, 60, 30)).save(buffer, "PNG")
    return buffer.getvalue()


def test_gate_full_path(client, app, db_session):
    world = build_world(client, app, db_session)
    gid = world["group"]["id"]
    headers = bearer(world["owner"])

    # A flyer arrives (Phase 2's road) — undescribed, as the
    # alt-text law requires it to say.
    flyer = client.post(
        f"/api/v1/groups/{gid}/sources/image?filename=club-night.png",
        content=_image_bytes(), headers=headers,
    )
    assert flyer.status_code == 201, flyer.text
    flyer_id = flyer.json()["flyer"]["id"]
    assert flyer.json()["flyer"]["alt_text_status"] == "awaiting_description"

    # The chat is extracted and the event approved (Phases 2–3).
    run_extraction_job(client, app, db_session, world)
    candidate = candidates_by_kind(client, world)["event"]
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    event_id = approved.json()["resulting_entity_id"]
    assert event_id

    # Phase 4: a real, published event.
    event = client.get(f"/api/v1/groups/{gid}/events/{event_id}", headers=headers)
    assert event.status_code == 200
    assert event.json()["status"] == "published"
    assert event.json()["title"] == "Club night"
    assert event.json()["starts_at"] == "2026-10-24T23:00:00Z"

    # The flyer joins the event (the link Phase 5's vision
    # pipeline will set when it reads the poster).
    row = db_session.get(Event, event_id)
    row.flyer_id = flyer_id
    db_session.commit()

    # Its .ics downloads and parses back to the same times.
    ics = client.get(f"/api/v1/groups/{gid}/events/{event_id}/ics", headers=headers)
    assert ics.status_code == 200
    (parsed,) = ical_service.parse_calendar(ics.text)
    assert parsed["summary"] == "Club night"
    assert parsed["dtstart"]["local"].isoformat() == "2026-10-24T19:00:00"
    assert parsed["dtstart"]["tzid"] == "America/New_York"

    # Discussion is live under it.
    comment = client.post(
        f"/api/v1/groups/{gid}/events/{event_id}/comments",
        json={"body": "Doors at seven — see you there"},
        headers=headers,
    )
    assert comment.status_code == 201, comment.text

    # An RSVP is answered.
    rsvp = client.put(
        f"/api/v1/groups/{gid}/events/{event_id}/rsvp",
        json={"status": "coming", "guests": 1}, headers=headers,
    )
    assert rsvp.status_code == 200, rsvp.text
    assert rsvp.json()["waitlisted"] is False

    # And the calendar surfaces show it: the agenda for the
    # weekend, and Saturday night itself — flyer attached, its
    # undescribed state honestly flagged.
    agenda = client.get(
        f"/api/v1/groups/{gid}/agenda"
        "?from=2026-10-24T00:00:00Z&to=2026-10-25T05:00:00Z",
        headers=headers,
    ).json()
    assert [row["title"] for row in agenda["items"]] == ["Club night"]
    tonight = client.get(
        f"/api/v1/groups/{gid}/tonight?date=2026-10-24", headers=headers
    ).json()
    assert [row["title"] for row in tonight] == ["Club night"]
    assert tonight[0]["flyer"]["flyer_id"] == flyer_id
    assert tonight[0]["flyer"]["alt_text_status"] == "awaiting_description"
