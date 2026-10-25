"""Cross-source matching (roadmap B-075, Doc 07 §6).

A new candidate is scored against the group's existing events
on title, time, place, and host. At or above the threshold the
queue shows "likely the same event" and offers the merge; below
it, the candidate is a new event and the machine says nothing
more. These tests pin the scoring arithmetic (the Doc 07 triple
story turns on it), the detail-view surface, and the refusals:
a bare title resemblance never proposes, a decided candidate
matches nothing, and other kinds do not match at all.
"""

import json

from conftest import bearer, make_group, register
from gpa.models.groups import Candidate, Group
from gpa.services import matching

GROUP_TZ = "America/New_York"


def _field(value, **extra):
    return {"value": value, "confidence": 0.9, "quote": "words", "supported": True, **extra}


def _candidate(db_session, group_id, *, title, fields, kind="event", status="pending"):
    """A queue candidate with a pipeline-shaped draft, planted
    directly (the pipeline's own tests own how drafts are made;
    matching owns only how they are compared)."""
    draft = {"kind": kind, "fields": fields}
    for name, entry in fields.items():
        if entry.get("value") is not None:
            draft[name] = entry["value"]
    row = Candidate(
        group_id=group_id, kind=kind, title=title,
        created_by="system:extraction", status=status,
        draft_json=json.dumps(draft), confidence=0.9,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _event(client, owner, group_id, **overrides):
    body = {
        "title": "Harvest Supper",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:30:00-05:00",
        "place_name": "Village Hall",
        "host_label": "Ana",
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/groups/{group_id}/events", json=body, headers=bearer(owner)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_title_similarity_words():
    assert matching.title_similarity("Harvest Supper", "Harvest Supper") == 1.0
    # One shared word of two on each side: half Jaccard (1/3),
    # half containment (1/2) — the Doc 07 pair.
    assert matching.title_similarity("Supper club", "Harvest Supper") == 0.5 * (1 / 3) + 0.5 * 0.5
    assert matching.title_similarity("Quiz night", "Harvest Supper") == 0.0
    assert matching.title_similarity("", "Harvest Supper") is None
    assert matching.title_similarity("Harvest Supper", "") is None


def test_place_similarity_containment():
    assert matching.place_similarity("Village Hall", "Village Hall") == 1.0
    assert matching.place_similarity("hall", "Village Hall") == 0.9
    assert matching.place_similarity("School gym", "Village Hall") == 0.0
    assert matching.place_similarity("", "Village Hall") is None


def test_detail_shows_likely_match_best_first(client, db_session):
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    event = _event(client, owner, gid)
    other = _event(
        client, owner, gid, title="Quiz night",
        starts_at="2026-11-13T19:00:00-05:00", ends_at="2026-11-13T21:00:00-05:00",
        place_name="School gym", host_label="Ben",
    )
    candidate = _candidate(
        db_session, gid, title="Harvest Supper",
        fields={
            "title": _field("Harvest Supper"),
            "starts": _field("Friday 6 November 6.30pm", resolved_date="2026-11-06"),
            "place": _field("Village Hall"),
        },
    )
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}", headers=bearer(owner)
    )
    assert detail.status_code == 200, detail.text
    matches = detail.json()["matches"]
    assert matches[0]["event_id"] == event["id"]
    assert matches[0]["likely"] is True
    assert matches[0]["score"] == 1.0
    assert matches[0]["components"] == {
        "title": 1.0, "time": 1.0, "place": 1.0, "host": None,
    }
    quiz = next(row for row in matches if row["event_id"] == other["id"])
    assert quiz["likely"] is False
    assert quiz["score"] == 0.0


def test_triple_story_chat_draft_matches_the_event(client, db_session):
    """The Doc 07 shape: the chat said "Supper club ... this
    Friday ... hall" — a partial title and a short place, but
    the same Friday. The score still clears the threshold."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    event = _event(client, owner, gid)
    candidate = _candidate(
        db_session, gid, title="Supper club",
        fields={
            "title": _field("Supper club"),
            "starts": _field("this Friday", resolved_date="2026-11-06"),
            "place": _field("the hall"),
        },
    )
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}", headers=bearer(owner)
    ).json()
    (top,) = [row for row in detail["matches"] if row["event_id"] == event["id"]]
    assert top["likely"] is True
    assert top["score"] > matching.MATCH_THRESHOLD
    assert top["components"]["time"] == 1.0
    assert top["components"]["place"] == 0.9


def test_ambiguous_date_reading_still_counts(client, db_session):
    """An ambiguous date phrase keeps its readings; the event's
    date among them earns the 0.85 time credit (Doc 07 §6)."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    event = _event(client, owner, gid)
    candidate = _candidate(
        db_session, gid, title="Harvest Supper",
        fields={
            "title": _field("Harvest Supper"),
            "starts": _field(
                "Friday week", resolved_date=None,
                date_readings=["2026-11-06", "2026-11-13"],
            ),
            "place": _field("Village Hall"),
        },
    )
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}", headers=bearer(owner)
    ).json()
    (top,) = [row for row in detail["matches"] if row["event_id"] == event["id"]]
    assert top["components"]["time"] == 0.85
    assert top["likely"] is True


def test_bare_title_resemblance_never_proposes(client, db_session):
    """The same title with no time and no place is a hint, not a
    proposal: the score is halved below the threshold."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    event = _event(client, owner, gid)
    candidate = _candidate(
        db_session, gid, title="Harvest Supper",
        fields={"title": _field("Harvest Supper")},
    )
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}", headers=bearer(owner)
    ).json()
    (top,) = [row for row in detail["matches"] if row["event_id"] == event["id"]]
    assert top["score"] == 0.5
    assert top["likely"] is False


def test_decided_and_other_kind_candidates_have_no_matches(client, db_session):
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    _event(client, owner, gid)
    fields = {
        "title": _field("Harvest Supper"),
        "starts": _field("x", resolved_date="2026-11-06"),
    }
    decided = _candidate(db_session, gid, title="Harvest Supper", fields=fields, status="approved")
    task = _candidate(
        db_session, gid, title="Bring chairs", kind="task",
        fields={"title": _field("Bring chairs")},
    )
    group_row = db_session.get(Group, gid)
    assert matching.match_candidate(db_session, group_row, decided) == []
    assert matching.match_candidate(db_session, group_row, task) == []
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{task.id}", headers=bearer(owner)
    ).json()
    assert detail["matches"] == []
