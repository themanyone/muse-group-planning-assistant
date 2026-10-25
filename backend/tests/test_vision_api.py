"""Vision API tests (roadmap Phase 5: B-063 → B-071).

Through the real doors: upload → POST read → the durable job →
the review queue. A scripted driver (tests/vision_world.py)
stands behind the adapter; the fixture-driver road is the
phase gate's (test_phase5_gate.py).
"""

import pytest

from conftest import bearer, make_group, register
from gpa.ai import client as ai_client
from gpa.ai.dates import resolve_date_phrase
from gpa.models.core import AuditLog
from vision_world import (
    ScriptedVisionDriver,
    board_bytes,
    flyer_answer,
    poster_bytes,
    request_read,
    run_one_job,
    upload_image,
)

RED = (208, 32, 32)
GREEN = (32, 128, 48)
BLUE = (48, 64, 192)
GOLD = (240, 176, 32)

HARVEST_TRANSCRIPT = (
    "HARVEST SUPPER — Fri Nov 6, 6.30pm, Village Hall. £6. "
    "Bring a dish to share! Full details at www.example.org/supper"
)
HARVEST_ALT = (
    "A cream paper poster with a red headline 'Harvest Supper' and a "
    "drawn pumpkin border; text gives Friday Nov 6, 6.30pm, Village "
    "Hall, £6, bring a dish to share."
)


@pytest.fixture()
def driver():
    scripted = ScriptedVisionDriver()
    ai_client.set_client(scripted)
    return scripted


@pytest.fixture()
def world(client, driver):
    owner = register(client)
    group = make_group(client, owner, home_timezone="America/New_York")
    return {"owner": owner, "group": group, "headers": bearer(owner)}


def _harvest_reading(**overrides):
    answer = flyer_answer(
        title="Harvest Supper", starts_phrase="Nov 6", starts_time="6.30pm",
        place="Village Hall", cost="£6", bring="a dish to share",
        transcript=HARVEST_TRANSCRIPT, alt_text=HARVEST_ALT,
        links=[{"url": "https://example.org/rsvp", "kind": "qr",
                "region": [0.7, 0.8, 0.2, 0.15]}],
    )
    answer["items"][0].update(overrides)
    return answer


def _read_single(client, app, db_session, world, colour=RED, answer=None):
    """Upload one flyer, ask for its reading, run the job.
    Returns (source_detail_after, flyer_id)."""
    world["driver"].readings[colour] = answer or _harvest_reading()
    detail = upload_image(client, world["headers"], world["group"]["id"],
                          poster_bytes(colour))
    request_read(client, world["headers"], world["group"]["id"], detail["id"])
    assert run_one_job(app, db_session) is True
    after = client.get(
        f"/api/v1/groups/{world['group']['id']}/sources/{detail['id']}",
        headers=world["headers"],
    ).json()
    return after, detail["flyer"]["id"]


@pytest.fixture()
def driver_into_world(world, driver):
    world["driver"] = driver
    return world


# --- B-063: a flyer becomes a region-provenanced candidate ---------


def test_flyer_reading_lands_in_the_queue_with_regions(client, app, db_session, driver_into_world):
    world = driver_into_world
    after, flyer_id = _read_single(client, app, db_session, world)

    # The flyer learned its words and its description (B-065).
    assert after["flyer"]["transcript"] == HARVEST_TRANSCRIPT
    assert after["flyer"]["alt_text"] == HARVEST_ALT
    assert after["flyer"]["alt_text_status"] == "complete"
    assert after["stage"] == "done"

    queue = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates",
        headers=world["headers"],
    ).json()
    assert len(queue) == 1
    candidate = queue[0]
    assert candidate["created_by"] == "system:vision"
    assert candidate["prompt_name"] == "flyer_reading"
    assert candidate["prompt_version"] == 2
    assert candidate["title"] == "Harvest Supper"

    detail = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates/{candidate['id']}",
        headers=world["headers"],
    ).json()
    provenance = {row["field"]: row for row in detail["provenance"]}
    # Region provenance: the title's evidence is a rectangle.
    assert provenance["title"]["region"] == [0.08, 0.05, 0.8, 0.12]
    assert provenance["title"]["supported"] is True
    assert provenance["title"]["quote"] == ""
    # The regionless bring field is unsupported, not hidden.
    assert provenance["bring"]["supported"] is False
    assert detail["draft"]["fields"]["bring"]["value"] == "a dish to share"
    # The date resolved from the photo's context (the source's
    # creation instant), by the deterministic resolver (D-22).
    source = client.get(
        f"/api/v1/groups/{world['group']['id']}/sources/{candidate['source_id']}",
        headers=world["headers"],
    ).json()
    from datetime import datetime

    created = datetime.fromisoformat(source["created_at"])
    expected = resolve_date_phrase(
        "Nov 6", sent_at=created, group_tz="America/New_York"
    ).readings[0].date.isoformat()
    assert detail["draft"]["starts_date"] == expected
    assert detail["draft"]["starts_at"] == f"{expected}T18:30:00"


def test_approval_mints_the_event_with_its_flyer(client, app, db_session, driver_into_world):
    """Phase 4's by-hand stand-in, made real: approval sets
    Event.flyer_id from the candidate's flyer source."""
    world = driver_into_world
    _after, flyer_id = _read_single(client, app, db_session, world)
    candidate = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates",
        headers=world["headers"],
    ).json()[0]
    approved = client.post(
        f"/api/v1/groups/{world['group']['id']}/candidates/{candidate['id']}/approve",
        headers=world["headers"],
    )
    assert approved.status_code == 200, approved.text
    event_id = approved.json()["resulting_entity_id"]
    event = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{event_id}",
        headers=world["headers"],
    ).json()
    assert event["status"] == "published"
    assert event["title"] == "Harvest Supper"
    assert event["flyer"]["flyer_id"] == flyer_id
    assert event["flyer"]["generated"] is False
    assert event["flyer"]["alt_text_status"] == "complete"


def test_read_request_refuses_a_text_source(client, app, db_session, driver_into_world):
    world = driver_into_world
    pasted = client.post(
        f"/api/v1/groups/{world['group']['id']}/sources/paste",
        json={"title": "chat", "text": "Ana: hello"},
        headers=world["headers"],
    ).json()
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/sources/{pasted['id']}/read",
        headers=world["headers"],
    )
    assert response.status_code == 422
    assert "no picture to read" in response.json()["detail"]


# --- B-066: links from codes and from print -------------------------


def test_candidate_carries_qr_and_printed_links(client, app, db_session, driver_into_world):
    world = driver_into_world
    _read_single(client, app, db_session, world)
    candidate = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates",
        headers=world["headers"],
    ).json()[0]
    links = candidate["draft"]["links"]
    by_url = {link["url"]: link for link in links}
    # The QR the model decoded, with its region…
    assert by_url["https://example.org/rsvp"]["kind"] == "qr"
    assert by_url["https://example.org/rsvp"]["region"] == [0.7, 0.8, 0.2, 0.15]
    # …and the address printed in the transcript, found by the
    # deterministic half (the model never listed it).
    assert by_url["https://www.example.org/supper"]["kind"] == "printed"


# --- B-065: the alt-text law, as behaviour --------------------------


def test_alt_law_placeholder_is_always_flagged(client, app, db_session, driver_into_world):
    """No flyer response ships placeholder alt text unflagged:
    wherever the placeholder stands, the status says awaiting."""
    world = driver_into_world
    detail = upload_image(client, world["headers"], world["group"]["id"],
                          poster_bytes(BLUE))
    flyer = detail["flyer"]
    assert flyer["alt_text"] == "awaiting description"
    assert flyer["alt_text_status"] == "awaiting_description"
    wall = client.get(
        f"/api/v1/groups/{world['group']['id']}/flyers",
        headers=world["headers"],
    ).json()
    for entry in wall["entries"]:
        if entry["alt_text"] == "awaiting description":
            assert entry["alt_text_status"] == "awaiting_description"
        if entry["alt_text_status"] == "complete":
            assert len(entry["alt_text"]) >= 20


def test_a_thin_machine_alt_draft_does_not_complete(client, app, db_session, driver_into_world):
    """A vision draft of "red poster" is not a description; the
    defect stays visible rather than painted green."""
    world = driver_into_world
    after, _flyer_id = _read_single(
        client, app, db_session, world, colour=GREEN,
        answer=_harvest_reading(alt_text="red poster"),
    )
    assert after["flyer"]["transcript"] == HARVEST_TRANSCRIPT  # words landed
    assert after["flyer"]["alt_text_status"] == "awaiting_description"
    assert after["flyer"]["alt_text"] == "awaiting description"


def test_a_person_can_describe_a_flyer(client, app, db_session, driver_into_world):
    world = driver_into_world
    detail = upload_image(client, world["headers"], world["group"]["id"],
                          poster_bytes(GOLD))
    flyer_id = detail["flyer"]["id"]
    refused = client.patch(
        f"/api/v1/groups/{world['group']['id']}/flyers/{flyer_id}",
        json={"alt_text": "poster"}, headers=world["headers"],
    )
    assert refused.status_code == 422  # not a description
    described = client.patch(
        f"/api/v1/groups/{world['group']['id']}/flyers/{flyer_id}",
        json={"alt_text": "A gold poster for the quiz night, bold black "
                          "letters, a drawing of a question mark, the "
                          "date and hall printed underneath."},
        headers=world["headers"],
    )
    assert described.status_code == 200, described.text
    assert described.json()["alt_text_status"] == "complete"
    assert described.json()["alt_text"].startswith("A gold poster")


# --- B-064 + B-069: boards, both drivers, partial success ----------

BOARD_BOXES = [
    {"box": [0.04, 0.05, 0.44, 0.60], "label": "red poster"},
    {"box": [0.54, 0.05, 0.42, 0.60], "label": "green poster"},
    {"box": [0.04, 0.70, 0.44, 0.26], "label": "blue poster"},
]
BOARD_POSTERS = [(RED, BOARD_BOXES[0]["box"]), (GREEN, BOARD_BOXES[1]["box"]),
                 (BLUE, BOARD_BOXES[2]["box"])]


def _board_world(client, app, db_session, world):
    world["driver"].board_regions = BOARD_BOXES
    for colour, title in ((RED, "Harvest Supper"), (GREEN, "Quiz Night"),
                          (BLUE, "Book Club")):
        world["driver"].readings[colour] = flyer_answer(
            title=title, starts_phrase="Nov 6", starts_time="6.30pm",
            place="Village Hall",
            transcript=f"{title} — Nov 6, Village Hall",
            alt_text=f"A poster for {title} at the Village Hall on Nov 6, "
                     "with the time and place printed in large letters.",
        )
    detail = upload_image(client, world["headers"], world["group"]["id"],
                          board_bytes(BOARD_POSTERS), board=True,
                          filename="board.png")
    return detail


def test_board_segments_and_reads_each_poster(client, app, db_session, driver_into_world):
    world = driver_into_world
    detail = _board_world(client, app, db_session, world)
    gid = world["group"]["id"]
    assert detail["stage"] == "awaiting_segmentation"
    request_read(client, world["headers"], gid, detail["id"])
    assert run_one_job(app, db_session) is True

    board = client.get(
        f"/api/v1/groups/{gid}/sources/{detail['id']}", headers=world["headers"]
    ).json()
    assert board["stage"] == "segmented"
    assert board["progress"] == 100

    # Three child flyers, each read into the queue.
    queue = client.get(f"/api/v1/groups/{gid}/candidates",
                       headers=world["headers"]).json()
    assert sorted(c["title"] for c in queue) == [
        "Book Club", "Harvest Supper", "Quiz Night",
    ]
    wall = client.get(f"/api/v1/groups/{gid}/flyers", headers=world["headers"]).json()
    children = [e for e in wall["entries"] if e["parent_flyer_id"]]
    assert len(children) == 3
    assert all(e["review_status"] == "pending_review" for e in children)
    assert all(e["alt_text_status"] == "complete" for e in children)


def test_board_partial_failure_is_a_state_not_a_silence(client, app, db_session, driver_into_world):
    """B-069: one glare-ruined poster of three — two drafts land,
    the third child says read_failed, the job completes, and the
    audit counts it out loud."""
    world = driver_into_world
    detail = _board_world(client, app, db_session, world)
    gid = world["group"]["id"]
    world["driver"].fail_colours = {BLUE}
    request_read(client, world["headers"], gid, detail["id"])
    assert run_one_job(app, db_session) is True
    job = client.get(
        f"/api/v1/groups/{gid}/candidates", headers=world["headers"]
    ).json()
    assert sorted(c["title"] for c in job) == ["Harvest Supper", "Quiz Night"]

    wall = client.get(f"/api/v1/groups/{gid}/flyers", headers=world["headers"]).json()
    failed = [e for e in wall["entries"] if e["review_status"] == "read_failed"]
    assert len(failed) == 1
    board = client.get(
        f"/api/v1/groups/{gid}/sources/{detail['id']}", headers=world["headers"]
    ).json()
    assert board["progress"] == 66  # 2 of 3, honestly
    partials = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "vision.partial")
        .all()
    )
    assert len(partials) == 1
    import json

    assert json.loads(partials[0].detail_json) == {
        "read": 2, "failed": 1, "total": 3,
    }


def test_board_via_sam_driver_and_vision_fallback(client, app, db_session, driver_into_world):
    """B-064 in the other mode: a SAM-shaped endpoint cuts the
    board (stubbed transport); when SAM refuses, the vision
    fallback cuts instead and the audit names it."""
    import httpx

    from gpa.ai.segmentation import SamSegmenter
    from gpa.services import vision as vision_service

    world = driver_into_world
    detail = _board_world(client, app, db_session, world)
    gid = world["group"]["id"]
    group_row = world.get("group_row")
    from gpa.models.groups import Group
    from gpa.models.sources import Source

    group_row = db_session.get(Group, gid)
    source_row = db_session.get(Source, detail["id"])

    def sam_ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"regions": BOARD_BOXES})

    sam = SamSegmenter(base_url="https://sam.invalid",
                       transport=httpx.MockTransport(sam_ok))
    summary = vision_service.run_vision_read(
        db_session, settings=app.state.settings, group=group_row,
        source=source_row, requested_by="user:test", job_id=None,
        segmenter=sam,
    )
    assert summary["segmenter"] == "sam"
    assert summary["read"] == 3

    # A second board, SAM refusing: the fallback cuts it.
    detail2 = upload_image(client, world["headers"], gid,
                           board_bytes(BOARD_POSTERS), board=True,
                           filename="board2.png")
    source2 = db_session.get(Source, detail2["id"])

    def sam_down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    sam_refusing = SamSegmenter(base_url="https://sam.invalid",
                                transport=httpx.MockTransport(sam_down))
    summary2 = vision_service.run_vision_read(
        db_session, settings=app.state.settings, group=group_row,
        source=source2, requested_by="user:test", job_id=None,
        segmenter=sam_refusing,
    )
    assert summary2["segmenter"] == "vision (fallback: SAM refused)"
    assert summary2["read"] == 3


# --- B-071: the same poster twice ------------------------------------


def test_the_same_poster_twice_earns_a_merge_suggestion(client, app, db_session, driver_into_world):
    world = driver_into_world
    _after1, flyer1 = _read_single(client, app, db_session, world)
    gid = world["group"]["id"]
    # The same poster, photographed again (identical bytes here;
    # the hash is built to survive re-photography too).
    detail2 = upload_image(client, world["headers"], gid, poster_bytes(RED),
                           filename="harvest-again.png")
    request_read(client, world["headers"], gid, detail2["id"])
    assert run_one_job(app, db_session) is True
    queue = client.get(f"/api/v1/groups/{gid}/candidates",
                       headers=world["headers"]).json()
    fresh = [c for c in queue if c["source_id"] == detail2["id"]]
    assert len(fresh) == 1
    assert "possible_duplicate" in fresh[0]["flags"]
    assert fresh[0]["draft"]["duplicate_of"]["flyer_id"] == flyer1


def test_a_different_poster_earns_no_suggestion(client, app, db_session, driver_into_world):
    world = driver_into_world
    _read_single(client, app, db_session, world)  # the red harvest poster
    gid = world["group"]["id"]
    world["driver"].readings[GREEN] = flyer_answer(
        title="Quiz Night", starts_phrase="Nov 13", starts_time="7pm",
        place="Village Hall", transcript="QUIZ NIGHT — Nov 13",
        alt_text="A green poster for a quiz night at the Village Hall "
                 "on Nov 13, with questions and prizes listed.",
    )
    detail = upload_image(client, world["headers"], gid, poster_bytes(GREEN),
                          filename="quiz.png")
    request_read(client, world["headers"], gid, detail["id"])
    assert run_one_job(app, db_session) is True
    queue = client.get(f"/api/v1/groups/{gid}/candidates",
                       headers=world["headers"]).json()
    fresh = [c for c in queue if c["source_id"] == detail["id"]][0]
    assert "possible_duplicate" not in fresh["flags"]
    assert "duplicate_of" not in fresh["draft"]
