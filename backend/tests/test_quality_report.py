"""B-081 — the data-quality report: one endpoint, four lists,
one count. The golden shape is a mixed-quality group: an event
missing its place, an event missing its host, a low-confidence
field waiting in the queue, and an unresolved contradiction —
needs_attention counts them all, and the frontend's badge is
that number. A draft missing its place is unfinished, not a
defect, and stays out of the lists.
"""

import json

from gpa.models.events import Event, EventContradiction
from gpa.models.groups import Candidate
from conftest import bearer, make_group, register


def _event(client, owner, gid, **overrides):
    body = {
        "title": "Event",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:30:00-05:00",
        "place_name": "Village Hall",
        "host_label": "Ana",
    }
    body.update(overrides)
    created = client.post(
        f"/api/v1/groups/{gid}/events", json=body, headers=bearer(owner)
    )
    assert created.status_code == 201, created.text
    return created.json()


def _report(client, owner, gid):
    response = client.get(
        f"/api/v1/groups/{gid}/quality-report", headers=bearer(owner)
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_group_reports_all_zeros(client):
    owner = register(client, "qr-owner@example.org")
    gid = make_group(client, owner)["id"]
    report = _report(client, owner, gid)
    assert report["missing_place"] == []
    assert report["missing_host"] == []
    assert report["low_confidence_fields"] == []
    assert report["unresolved_contradictions"] == []
    assert report["needs_attention"] == 0


def test_golden_mixed_quality_group(client, db_session):
    owner = register(client, "qr-owner2@example.org")
    gid = make_group(client, owner, home_timezone="America/New_York")["id"]
    no_place = _event(client, owner, gid, title="Placeless", place_name="")
    no_host = _event(client, owner, gid, title="Hostless", host_label="")
    _event(client, owner, gid, title="Complete")  # counts for nothing
    drafty = _event(client, owner, gid, title="Drafty", place_name="")
    draft_row = db_session.get(Event, drafty["id"])
    draft_row.status = "draft"  # a draft may be unfinished
    db_session.commit()

    candidate = Candidate(
        group_id=gid, kind="event", title="Wobbly picnic",
        status="pending", source_id=None, created_by="system:extraction",
        draft_json="{}", flags_json=json.dumps(["low_confidence:starts"]),
        confidence=0.5,
    )
    db_session.add(candidate)
    db_session.flush()
    contra = EventContradiction(
        event_id=no_host["id"], group_id=gid, field="starts_at",
        values_json=json.dumps(
            [
                {"value": "a", "source_ids": ["s1"], "current": True},
                {"value": "b", "source_ids": ["s2"], "current": False},
            ]
        ),
        status="open",
    )
    db_session.add(contra)
    db_session.commit()

    report = _report(client, owner, gid)
    assert [row["event_id"] for row in report["missing_place"]] == [no_place["id"]]
    assert [row["event_id"] for row in report["missing_host"]] == [no_host["id"]]
    assert report["low_confidence_fields"] == [
        {
            "candidate_id": candidate.id,
            "title": "Wobbly picnic",
            "field": "starts",
        }
    ]
    assert report["unresolved_contradictions"] == [
        {
            "event_id": no_host["id"],
            "event_title": "Hostless",
            "contradiction_id": contra.id,
            "field": "starts_at",
        }
    ]
    assert report["needs_attention"] == 4
