"""Review-queue API tests (roadmap B-043, B-044) + its security
suite.

The queue over real extracted candidates: list + filters, detail
with full provenance, edit (flags recomputed by the API),
reject, merge — and the named refusals: no direct event creation
from a candidate payload, no approval by a plain member, no
approval by a draft-scoped key, no decisions by a seat.
Approval's honest boundary is asserted too: it records the
decision and the resulting-entity link stays empty until
Phase 4 mints entities (B-049).
"""

import pytest

from conftest import bearer, register
from extraction_world import build_world, candidates_by_kind, run_extraction_job
from test_groups import invite_and_accept


@pytest.fixture()
def world(client, app, db_session):
    built = build_world(client, app, db_session)
    run_extraction_job(client, app, db_session, built)
    return built


def test_queue_lists_all_four_kinds(world, client):
    rows = candidates_by_kind(client, world)
    assert set(rows) == {"event", "task", "decision", "question"}
    event = rows["event"]
    assert event["title"] == "Club night"
    assert event["status"] == "pending"
    assert event["created_by"] == "system:extraction"
    assert event["prompt_name"] == "extraction" and event["prompt_version"] == 1
    assert event["job_id"]
    assert event["confidence"] == pytest.approx(0.9)
    assert event["flags"] == []


def test_queue_filters(world, client):
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    only_events = client.get(
        f"/api/v1/groups/{gid}/candidates", params={"kind": "event"}, headers=headers
    ).json()
    assert [r["kind"] for r in only_events] == ["event"]
    confident = client.get(
        f"/api/v1/groups/{gid}/candidates",
        params={"min_confidence": 0.95}, headers=headers,
    ).json()
    # Only the decision (0.92)... no: the decision is 0.92, the
    # event 0.90 — nothing clears 0.95, and the filter says so.
    assert confident == []
    some = client.get(
        f"/api/v1/groups/{gid}/candidates",
        params={"min_confidence": 0.85}, headers=headers,
    ).json()
    assert len(some) == 4


def test_detail_carries_full_provenance(world, client):
    rows = candidates_by_kind(client, world)
    event = rows["event"]
    detail = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates/{event['id']}",
        headers=bearer(world["owner"]),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["draft"]["starts_date"] == "2026-10-24"
    assert body["draft"]["starts_at"] == "2026-10-24T19:00:00"
    provenance = {row["field"]: row for row in body["provenance"]}
    assert provenance["starts"]["supported"] is True
    assert provenance["starts"]["quote"] == "this Saturday"
    assert provenance["starts"]["chunk_id"]
    assert provenance["host"]["supported"] is False  # shown, not hidden


def test_edit_recomputes_flags_and_then_approves(world, client):
    """A reviewer corrects the place; the draft turns "edited",
    flags are recomputed server-side, and approval accepts an
    edited draft — and since Phase 4 (B-049) approval MINTS: the
    candidate's entity link names the published event it became.
    (Phase 3 asserted the link stayed empty; that boundary was
    Phase 4's to lift, and this assertion is its lifting.)"""
    rows = candidates_by_kind(client, world)
    event = rows["event"]
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    draft = dict(event["draft"])
    draft["fields"] = dict(draft["fields"])
    draft["fields"]["place"] = {
        **draft["fields"]["place"], "value": "the village hall",
        "confidence": 0.5,
    }
    draft["place"] = "the village hall"
    edited = client.patch(
        f"/api/v1/groups/{gid}/candidates/{event['id']}",
        json={"draft": draft}, headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "edited"
    # The API recomputed: a 0.5-confidence supported field flags.
    assert "low_confidence:place" in edited.json()["flags"]

    approved = client.post(
        f"/api/v1/groups/{gid}/candidates/{event['id']}/approve", headers=headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["resulting_entity_type"] == "event"
    event_id = approved.json()["resulting_entity_id"]
    assert event_id
    minted = client.get(
        f"/api/v1/groups/{gid}/events/{event_id}", headers=headers
    )
    assert minted.status_code == 200, minted.text
    assert minted.json()["status"] == "published"
    assert minted.json()["place_name"] == "the village hall"


def test_reject_keeps_the_draft_and_refuses_later_decisions(world, client):
    rows = candidates_by_kind(client, world)
    task = rows["task"]
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    rejected = client.post(
        f"/api/v1/groups/{gid}/candidates/{task['id']}/reject", headers=headers
    )
    assert rejected.status_code == 200, rejected.text
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{task['id']}", headers=headers
    ).json()
    assert detail["status"] == "rejected"
    assert detail["draft"]["fields"]["title"]["value"] == "Bring the projector"
    approve = client.post(
        f"/api/v1/groups/{gid}/candidates/{task['id']}/approve", headers=headers
    )
    assert approve.status_code == 409


def test_merge_keeps_both_drafts(world, client, db_session):
    """Two drafts of the same evening merge: the loser is marked,
    linked, and whole; the survivor is returned."""
    from gpa.services import candidates as candidate_service
    from gpa.services import groups as group_service

    rows = candidates_by_kind(client, world)
    survivor = rows["event"]
    # A second, hand-drafted event candidate (the B-018 stub path).
    other = group_service.create_candidate(
        db_session, actor_label="user:test", group=world["group_row"],
        kind="event", title="Club night (duplicate)",
    )
    db_session.commit()
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    merged = client.post(
        f"/api/v1/groups/{gid}/candidates/{other.id}/merge",
        json={"into_candidate_id": survivor["id"]}, headers=headers,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["id"] == survivor["id"]
    db_session.expire_all()  # the HTTP call decided in another session
    loser = candidate_service.get_candidate(db_session, world["group_row"], other.id)
    assert loser.status == "merged"
    assert loser.merged_into_id == survivor["id"]


# --- The security suite (B-043) -----------------------------------


def test_no_direct_event_creation_from_a_candidate(world, client):
    """Approval is the ONLY path from a candidate to a published
    entity: a candidate-shaped payload creates nothing, anywhere.

    Phase 3 asserted 404/405 because no events endpoint existed.
    Phase 4 (B-049) added one — direct ORGANISER entry, which
    takes a real event body (title + times), never a candidate
    draft. The security property is unchanged and now asserted
    at full strength: posting the draft is refused (404/405, or
    422 — the draft shape is not an event), no event exists
    afterwards, and the candidate is untouched."""
    rows = candidates_by_kind(client, world)
    gid = world["group"]["id"]
    headers = bearer(world["owner"])
    for path in (f"/api/v1/groups/{gid}/events", f"/api/v1/groups/{gid}/candidates/{rows['event']['id']}/publish"):
        response = client.post(path, json=rows["event"]["draft"], headers=headers)
        assert response.status_code in (404, 405, 422), (path, response.status_code)
    # Nothing was created and the candidate decided nothing.
    assert client.get(f"/api/v1/groups/{gid}/events", headers=headers).json() == []
    queue = client.get(f"/api/v1/groups/{gid}/candidates", headers=headers).json()
    candidate = next(row for row in queue if row["id"] == rows["event"]["id"])
    assert candidate["status"] == "pending"
    assert candidate["resulting_entity_id"] is None


def test_plain_member_cannot_approve_edit_or_extract(world, client):
    member = invite_and_accept(
        client, world["owner"], world["group"]["id"], "member@example.org", "member"
    )
    rows = candidates_by_kind(client, world)
    gid = world["group"]["id"]
    headers = bearer(member)
    assert client.post(
        f"/api/v1/groups/{gid}/candidates/{rows['event']['id']}/approve", headers=headers
    ).status_code == 403
    assert client.patch(
        f"/api/v1/groups/{gid}/candidates/{rows['event']['id']}",
        json={"draft": rows["event"]["draft"]}, headers=headers,
    ).status_code == 403
    assert client.post(
        f"/api/v1/groups/{gid}/sources/{world['source_id']}/extract",
        json={}, headers=headers,
    ).status_code == 403
    # ...but a member CAN read the queue — that part is theirs.
    assert client.get(f"/api/v1/groups/{gid}/candidates", headers=headers).status_code == 200


def test_draft_scoped_key_cannot_approve(world, client):
    """A person's own API key without the publish scope stops at
    the scope door (B-014), before any role question is asked."""
    rows = candidates_by_kind(client, world)
    gid = world["group"]["id"]
    key = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "drafty", "scopes": ["read", "draft"]},
        headers=bearer(world["owner"]),
    )
    assert key.status_code == 201, key.text
    key_headers = {"X-Api-Key": key.json()["key"]}
    response = client.post(
        f"/api/v1/groups/{gid}/candidates/{rows['event']['id']}/approve",
        headers=key_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "insufficient_scope"


def test_cross_group_candidate_is_invisible(world, client):
    """A candidate id from another group reads as not-found —
    the queue never leaks across the wall."""
    rows = candidates_by_kind(client, world)
    stranger = register(client)
    from conftest import make_group

    other_group = make_group(client, stranger)
    response = client.get(
        f"/api/v1/groups/{other_group['id']}/candidates/{rows['event']['id']}",
        headers=bearer(stranger),
    )
    assert response.status_code in (403, 404)


def test_conflicting_sources_flag_each_other(world, client, app, db_session):
    """B-044 across sources: a second chat moves the club night
    to the barn at low confidence — both event drafts earn
    conflicting:place, and the newcomer also earns
    low_confidence:place, all computed by the API."""
    from extraction_world import record_answers
    from gpa.ai.extraction import build_prompt
    from gpa.ai import promptlib
    from gpa.services import jobs as job_service
    from gpa.services import sources as source_service
    from conftest import bearer as _bearer

    text = "[23/10/2026, 12:00:00] Ana: Club night is on! this Saturday at the barn, doors 7pm"
    pasted = client.post(
        f"/api/v1/groups/{world['group']['id']}/sources/paste",
        json={"title": "Update", "text": text}, headers=_bearer(world["owner"]),
    )
    assert pasted.status_code == 201, pasted.text
    from gpa.models.sources import Source

    second = db_session.get(Source, pasted.json()["id"])
    chunks = source_service.list_chunks(db_session, second)
    answer = {"items": [{"fields": {
        "title": {"value": "Club night", "confidence": 0.9, "quote": "Club night is on!"},
        "starts": {"value": "this Saturday", "confidence": 0.9, "quote": "this Saturday",
                   "date_phrase": "this Saturday", "time_phrase": "7pm"},
        "place": {"value": "the barn", "confidence": 0.5, "quote": "at the barn"},
    }}]}
    record_answers(
        world["driver"], chunks,
        {"event": ("extraction", answer)},
    )
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/sources/{second.id}/extract",
        json={"kinds": ["event"]}, headers=_bearer(world["owner"]),
    )
    assert response.status_code == 202, response.text
    assert job_service.process_one(db_session, app.state.settings, "w2") is True

    rows = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates",
        params={"kind": "event"}, headers=_bearer(world["owner"]),
    ).json()
    assert len(rows) == 2
    for row in rows:
        assert "conflicting:place" in row["flags"], row
    newcomer = [r for r in rows if r["source_id"] == second.id][0]
    assert "low_confidence:place" in newcomer["flags"]
