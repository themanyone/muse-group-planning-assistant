"""B-082 — the Phase 6 gate: the whole story as ONE scripted
integration test. This is the submission video's second act,
proven by a test instead of narrated over a hope:

  Act 1  the same supper is announced three times — a chat
         message, a newsletter, a flyer — and the group ends
         up with ONE event, every field of it able to say
         where it came from (B-075/B-076/B-079);
  Act 2  "supper moved to 7.30" arrives and lands as a change
         on that event, not a fourth supper (B-077);
  Act 3  the re-announce draft names the move, old → new
         (B-078);
  Act 4  "the supper is cancelled" cancels the one event
         there ever was (B-077).
"""

from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.models.sources import Source
from gpa.services import jobs as job_service
from gpa.services import sources as source_service
from conftest import bearer, make_group, register
from test_change_candidates import CANCEL_ANSWER, MOVE_ANSWER
from test_extraction import field

CHAT_TEXT = (
    "[02/11/2026, 09:00:00] Ana: Supper club is on! this Friday "
    "at the hall, 6.30pm\n"
)
NEWSLETTER_TEXT = (
    "[04/11/2026, 08:00:00] Parish news: The Harvest Supper — "
    "Village Hall, Friday 6 November, 6.30pm\n"
)
FLYER_TEXT = (
    "[04/11/2026, 09:30:00] Noticeboard: HARVEST SUPPER — "
    "Village Hall — Fri 6 Nov — 6.30pm — £6 a head\n"
)
MOVE_TEXT = (
    "[05/11/2026, 09:00:00] Ana: Harvest Supper update — "
    "supper moved to 7.30!\n"
)
CANCEL_TEXT = (
    "[05/11/2026, 10:00:00] Ben: Bad news — the Harvest Supper "
    "is cancelled, the hall is double-booked\n"
)

CHAT_ANSWER = {
    "items": [
        {
            "fields": {
                "title": field("Supper club", 0.95, "Supper club is on!"),
                "starts": field(
                    "this Friday 6.30pm", 0.9, "this Friday",
                    date_phrase="this Friday", time_phrase="6.30pm",
                ),
                "place": field("the hall", 0.85, "at the hall"),
            }
        }
    ]
}
NEWSLETTER_ANSWER = {
    "items": [
        {
            "fields": {
                "title": field("Harvest Supper", 0.95, "The Harvest Supper"),
                "starts": field(
                    "6 November 6.30pm", 0.92, "Friday 6 November, 6.30pm",
                    date_phrase="6 November", time_phrase="6.30pm",
                ),
                "place": field("Village Hall", 0.9, "Village Hall"),
            }
        }
    ]
}
FLYER_ANSWER = {
    "items": [
        {
            "fields": {
                "title": field("Harvest Supper", 0.95, "HARVEST SUPPER"),
                "starts": field(
                    "6 November 6.30pm", 0.9, "6.30pm",
                    date_phrase="6 November", time_phrase="6.30pm",
                ),
                "place": field("Village Hall", 0.9, "Village Hall"),
                "cost": field("£6", 0.9, "£6 a head"),
            }
        }
    ]
}


def _paste_and_extract(client, app, db_session, owner, gid, title, text,
                       prompt_name, answer, kinds):
    pasted = client.post(
        f"/api/v1/groups/{gid}/sources/paste",
        json={"title": title, "text": text},
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text
    source_id = pasted.json()["id"]
    chunks = source_service.list_chunks(
        db_session, db_session.get(Source, source_id)
    )
    driver = FixtureDriver()
    driver.add_json(
        build_prompt(prompt_name, chunks, "America/New_York"),
        promptlib.load_prompt(prompt_name).schema,
        answer,
    )
    ai_client.set_client(driver)
    response = client.post(
        f"/api/v1/groups/{gid}/sources/{source_id}/extract",
        json={"kinds": kinds}, headers=bearer(owner),
    )
    assert response.status_code == 202, response.text
    assert job_service.process_one(db_session, app.state.settings, "test-worker")
    return source_id


def _candidate(client, owner, gid, source_id, kind):
    rows = client.get(
        f"/api/v1/groups/{gid}/candidates?include_decided=true",
        headers=bearer(owner),
    ).json()
    mine = [
        row for row in rows
        if row["source_id"] == source_id and row["kind"] == kind
    ]
    assert len(mine) == 1, rows
    return mine[0]


def _events(client, owner, gid):
    return client.get(f"/api/v1/groups/{gid}/events", headers=bearer(owner)).json()


def test_the_whole_phase6_story(client, app, db_session):
    owner = register(client, "gate-owner@example.org")
    gid = make_group(client, owner, home_timezone="America/New_York")["id"]

    # --- Act 1: three announcements, one supper ----------------
    chat_source = _paste_and_extract(
        client, app, db_session, owner, gid, "Chat", CHAT_TEXT,
        "extraction", CHAT_ANSWER, ["event"],
    )
    chat_candidate = _candidate(client, owner, gid, chat_source, "event")
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{chat_candidate['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    event_id = approved.json()["resulting_entity_id"]
    assert len(_events(client, owner, gid)) == 1

    newsletter_source = _paste_and_extract(
        client, app, db_session, owner, gid, "Newsletter", NEWSLETTER_TEXT,
        "extraction", NEWSLETTER_ANSWER, ["event"],
    )
    news_candidate = _candidate(client, owner, gid, newsletter_source, "event")
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{news_candidate['id']}",
        headers=bearer(owner),
    ).json()
    assert detail["matches"][0]["event_id"] == event_id
    assert detail["matches"][0]["likely"] is True
    merged = client.post(
        f"/api/v1/groups/{gid}/candidates/{news_candidate['id']}/merge-event",
        json={"event_id": event_id}, headers=bearer(owner),
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["applied_fields"] == ["place_name"]
    assert len(merged.json()["contradiction_ids"]) == 1  # the title

    flyer_source = _paste_and_extract(
        client, app, db_session, owner, gid, "Flyer", FLYER_TEXT,
        "extraction", FLYER_ANSWER, ["event"],
    )
    flyer_candidate = _candidate(client, owner, gid, flyer_source, "event")
    merged = client.post(
        f"/api/v1/groups/{gid}/candidates/{flyer_candidate['id']}/merge-event",
        json={"event_id": event_id}, headers=bearer(owner),
    )
    assert merged.status_code == 200, merged.text
    assert "cost_text" in merged.json()["filled_fields"]
    assert "title" in merged.json()["applied_fields"]

    events = _events(client, owner, gid)
    assert len(events) == 1  # three announcements, ONE supper
    event = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=bearer(owner)
    ).json()
    assert event["title"] == "Harvest Supper"
    assert event["place_name"] == "Village Hall"
    assert event["cost_text"] == "£6"
    assert event["starts_at"] == "2026-11-06T23:30:00Z"
    assert event["contradictions"] == []  # the title dispute settled itself
    links = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}/sources",
        headers=bearer(owner),
    ).json()
    assert {row["field"] for row in links} >= {"title", "starts", "place", "cost"}
    assert {row["source_id"] for row in links} == {
        chat_source, newsletter_source, flyer_source,
    }

    # --- Act 2: "moved to 7.30" changes the supper -------------
    move_source = _paste_and_extract(
        client, app, db_session, owner, gid, "Update", MOVE_TEXT,
        "extraction_changes", MOVE_ANSWER, ["event_change"],
    )
    move = _candidate(client, owner, gid, move_source, "event_change")
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{move['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    event = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=bearer(owner)
    ).json()
    assert event["starts_at"] == "2026-11-07T00:30:00Z"
    assert event["needs_reannounce"] is True
    assert len(_events(client, owner, gid)) == 1  # still one supper

    # --- Act 3: the draft names the move ------------------------
    draft = client.post(
        f"/api/v1/groups/{gid}/events/{event_id}/reannounce-drafts",
        headers=bearer(owner),
    )
    assert draft.status_code == 201, draft.text
    body = draft.json()["body"]
    assert "06:30 PM" in body and "07:30 PM" in body

    # --- Act 4: the cancellation lands on the same event --------
    cancel_source = _paste_and_extract(
        client, app, db_session, owner, gid, "Bad news", CANCEL_TEXT,
        "extraction_changes", CANCEL_ANSWER, ["event_change"],
    )
    cancel = _candidate(client, owner, gid, cancel_source, "event_change")
    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{cancel['id']}/approve",
        headers=bearer(owner),
    )
    assert approved.status_code == 200, approved.text
    event = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=bearer(owner)
    ).json()
    assert event["status"] == "cancelled"
    assert len(_events(client, owner, gid)) == 1  # there was only ever one
