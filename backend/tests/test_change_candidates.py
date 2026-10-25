"""B-077 — change detection: words about an existing event
draft as a CHANGE candidate, and approval applies it.

The golden threads (roadmap §6, Doc 07 §6): "supper moved to
7.30" moves the supper — it does not create a second one — and
"the supper is cancelled" cancels it, through the change
history the same event already carries. An unknown target is
flagged, never guessed; a rejected change leaves the event
standing.
"""

from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.models.sources import Source
from gpa.services import jobs as job_service
from gpa.services import sources as source_service
from conftest import bearer, make_group, register
from test_extraction import field

SUPPER_BODY = "£6 a head, bring a bowl."
MOVE_TEXT = (
    "[04/11/2026, 09:00:00] Ana: Harvest Supper update — "
    "supper moved to 7.30!\n"
)
CANCEL_TEXT = (
    "[04/11/2026, 10:00:00] Ben: Bad news — the Harvest Supper "
    "is cancelled, the hall is double-booked\n"
)
UNKNOWN_TEXT = (
    "[04/11/2026, 11:00:00] Ana: The Winter Ball is cancelled\n"
)

MOVE_ANSWER = {
    "items": [
        {
            "fields": {
                "event": field("Harvest Supper", 0.95, "Harvest Supper update"),
                "starts": field(
                    "7.30pm", 0.9, "supper moved to 7.30!",
                    time_phrase="7.30pm",
                ),
            }
        }
    ]
}
CANCEL_ANSWER = {
    "items": [
        {
            "fields": {
                "event": field(
                    "Harvest Supper", 0.95, "the Harvest Supper is cancelled"
                ),
                "cancelled": field(
                    "cancelled", 0.92, "the Harvest Supper is cancelled"
                ),
            }
        }
    ]
}
UNKNOWN_ANSWER = {
    "items": [
        {
            "fields": {
                "event": field("Winter Ball", 0.9, "The Winter Ball is cancelled"),
                "cancelled": field(
                    "cancelled", 0.9, "The Winter Ball is cancelled"
                ),
            }
        }
    ]
}


def _world(client):
    owner = register(client, "cc-owner@example.org")
    gid = make_group(client, owner, home_timezone="America/New_York")["id"]
    created = client.post(
        f"/api/v1/groups/{gid}/events",
        json={
            "title": "Harvest Supper",
            "description": SUPPER_BODY,
            "starts_at": "2026-11-06T18:30:00-05:00",
            "ends_at": "2026-11-06T21:30:00-05:00",
            "place_name": "Village Hall",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return owner, gid, created.json()["id"]


def _extract_change(client, app, db_session, owner, gid, text, answer):
    """Paste the words, record the fixture answer under the
    exact prompt the pipeline renders, run the real job."""
    pasted = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={"title": "Update", "text": text},
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text
    source_id = pasted.json()["id"]
    chunks = source_service.list_chunks(db_session, db_session.get(Source, source_id))
    driver = FixtureDriver()
    driver.add_json(
        build_prompt("extraction_changes", chunks, "America/New_York"),
        promptlib.load_prompt("extraction_changes").schema,
        answer,
    )
    ai_client.set_client(driver)
    response = client.post(
        f"/api/v1/groups/{gid}/sources/{source_id}/extract",
        json={"kinds": ["event_change"]}, headers=bearer(owner),
    )
    assert response.status_code == 202, response.text
    assert job_service.process_one(db_session, app.state.settings, "test-worker")
    rows = client.get(
        f"/api/v1/groups/{gid}/candidates?include_decided=true",
        headers=bearer(owner),
    ).json()
    changes = [row for row in rows if row["kind"] == "event_change"]
    assert len(changes) == 1
    return changes[0]


def _event(client, owner, gid, event_id):
    return client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=bearer(owner)
    ).json()


def _decide(client, owner, gid, candidate_id, action):
    return client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate_id}/{action}",
        headers=bearer(owner),
    )


def test_move_candidate_links_to_its_event(client, app, db_session):
    owner, gid, event_id = _world(client)
    candidate = _extract_change(client, app, db_session, owner, gid, MOVE_TEXT, MOVE_ANSWER)
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}",
        headers=bearer(owner),
    ).json()
    assert detail["draft"]["target_event_id"] == event_id
    assert detail["draft"]["target_title"] == "Harvest Supper"
    assert detail["draft"]["fields"]["starts"]["resolved_time"] == "19:30:00"


def test_golden_move_thread(client, app, db_session):
    owner, gid, event_id = _world(client)
    before = _event(client, owner, gid, event_id)
    assert before["starts_at"] == "2026-11-06T23:30:00Z"
    candidate = _extract_change(client, app, db_session, owner, gid, MOVE_TEXT, MOVE_ANSWER)
    response = _decide(client, owner, gid, candidate["id"], "approve")
    assert response.status_code == 200, response.text
    assert response.json()["resulting_entity_id"] == event_id
    after = _event(client, owner, gid, event_id)
    # 6.30pm → 7.30pm local; the day is kept, the end travels.
    assert after["starts_at"] == "2026-11-07T00:30:00Z"
    assert after["ends_at"] == "2026-11-07T03:30:00Z"
    assert after["needs_reannounce"] is True
    feed = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}/changes",
        headers=bearer(owner),
    ).json()
    moved = [row for row in feed if row["field"] in ("starts_at", "ends_at")]
    assert {row["field"] for row in moved} == {"starts_at", "ends_at"}
    assert all(
        row["reason"].startswith("change candidate") for row in moved
    )
    # Still exactly one supper.
    events = client.get(
        f"/api/v1/groups/{gid}/events", headers=bearer(owner)
    ).json()
    assert [row["title"] for row in events] == ["Harvest Supper"]


def test_golden_cancel_thread(client, app, db_session):
    owner, gid, event_id = _world(client)
    candidate = _extract_change(client, app, db_session, owner, gid, CANCEL_TEXT, CANCEL_ANSWER)
    response = _decide(client, owner, gid, candidate["id"], "approve")
    assert response.status_code == 200, response.text
    after = _event(client, owner, gid, event_id)
    assert after["status"] == "cancelled"
    assert "cancelled" in after["cancelled_reason"]


def test_unknown_target_is_flagged_and_cannot_apply(client, app, db_session):
    owner, gid, event_id = _world(client)
    candidate = _extract_change(client, app, db_session, owner, gid, UNKNOWN_TEXT, UNKNOWN_ANSWER)
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate['id']}",
        headers=bearer(owner),
    ).json()
    assert detail["draft"]["target_event_id"] is None
    assert "change_target_unknown" in detail["flags"]
    response = _decide(client, owner, gid, candidate["id"], "approve")
    assert response.status_code == 422
    queue = client.get(
        f"/api/v1/groups/{gid}/candidates", headers=bearer(owner)
    ).json()
    assert [row["id"] for row in queue] == [candidate["id"]]  # still pending
    # The named event stands untouched (direct entry publishes).
    assert _event(client, owner, gid, event_id)["status"] == "published"


def test_rejected_change_leaves_the_event_standing(client, app, db_session):
    owner, gid, event_id = _world(client)
    candidate = _extract_change(client, app, db_session, owner, gid, MOVE_TEXT, MOVE_ANSWER)
    response = _decide(client, owner, gid, candidate["id"], "reject")
    assert response.status_code == 200
    standing = _event(client, owner, gid, event_id)
    assert standing["starts_at"] == "2026-11-06T23:30:00Z"
    assert standing["needs_reannounce"] is False


def test_change_kinds_are_requestable_not_default(client):
    # Change detection is a named ask, not part of the default
    # four-kind run — first announcements must not draft
    # "changes" out of themselves.
    owner, gid, _event_id = _world(client)
    pasted = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={"title": "Update", "text": MOVE_TEXT},
        headers=bearer(owner),
    )
    source_id = pasted.json()["id"]
    bad = client.post(
        f"/api/v1/groups/{gid}/sources/{source_id}/extract",
        json={"kinds": ["nonsense"]}, headers=bearer(owner),
    )
    assert bad.status_code == 422
    ok = client.post(
        f"/api/v1/groups/{gid}/sources/{source_id}/extract",
        json={"kinds": ["event_change"]}, headers=bearer(owner),
    )
    assert ok.status_code == 202
