"""The merge service + contradictions (roadmap B-076, B-079).

The field-level merge rule — the better-sourced value wins —
worked through its cases: fills land, the fuller form refines,
agreement is recorded by links, a tie keeps the event's value
and raises a visible contradiction, a second independent
source settles the dispute by evidence, an unsupported claim
never moves anything, and resolving a contradiction applies
the chosen value through the change history. The phase's
golden shape closes the file: the Doc 07 triple announcement
(chat + newsletter + flyer) resolving to ONE event that can
answer "where did this come from?" for every field.
"""

import json

from conftest import bearer, make_group, register
from gpa.models.groups import Candidate
from gpa.models.sources import Source
from gpa.services import sources as source_service
from test_groups import invite_and_accept

GROUP_TZ = "America/New_York"


def _source(client, owner, gid, title, text):
    response = client.post(
        f"/api/v1/groups/{gid}/sources",
        json={
            "kind": "chat_paste", "title": title,
            "granted_scope": "group-history",
            "chunks": [{"text": text, "sender": "Ana"}],
        },
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _candidate(db_session, gid, *, source_id, title, fields, kind="event", status="pending"):
    """A pipeline-shaped candidate planted directly, with real
    provenance rows for its supported, quoted fields — the
    merge reads exactly what extraction would have written."""
    draft_fields = {}
    draft = {"kind": kind, "fields": draft_fields}
    for name, spec in fields.items():
        entry = {
            "value": spec.get("value"),
            "confidence": spec.get("confidence", 0.9),
            "quote": spec.get("quote", ""),
            "supported": spec.get("supported", True),
        }
        if spec.get("resolved_date"):
            entry["resolved_date"] = spec["resolved_date"]
        if spec.get("resolved_time"):
            entry["resolved_time"] = spec["resolved_time"]
        draft_fields[name] = entry
        if spec.get("value") is not None:
            draft[name] = spec["value"]
    starts = fields.get("starts") or {}
    if starts.get("resolved_date"):
        draft["starts_date"] = starts["resolved_date"]
        if starts.get("resolved_time"):
            draft["starts_at"] = f"{starts['resolved_date']}T{starts['resolved_time']}"
    row = Candidate(
        group_id=gid, kind=kind, title=title,
        created_by="system:extraction", status=status,
        draft_json=json.dumps(draft), confidence=0.9, source_id=source_id,
    )
    db_session.add(row)
    db_session.flush()
    source = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source)
    for name, spec in fields.items():
        if spec.get("supported", True) and spec.get("quote"):
            source_service.add_provenance(
                db_session, candidate_id=row.id, chunk=chunks[0],
                field=name, quote=spec["quote"],
            )
    db_session.commit()
    return row


def _event(client, owner, gid, **overrides):
    body = {
        "title": "Supper",
        "starts_at": "2026-11-06T18:30:00-05:00",
        "ends_at": "2026-11-06T21:30:00-05:00",
        "place_name": "hall",
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/groups/{gid}/events", json=body, headers=bearer(owner)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _merge(client, owner, gid, candidate_id, event_id):
    return client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate_id}/merge-event",
        json={"event_id": event_id}, headers=bearer(owner),
    )


def _approve(client, owner, gid, candidate_id):
    response = client.post(
        f"/api/v1/groups/{gid}/candidates/{candidate_id}/approve",
        headers=bearer(owner),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------
# Fills, refinement, agreement (B-076)
# ------------------------------------------------------------------


def test_merge_fills_refines_and_agrees(client, db_session):
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    event = _event(client, owner, gid)
    source_id = _source(client, owner, gid, "Supper thread", "Supper at the Village Hall, £6, bring a dish, Ana hosting")
    candidate = _candidate(
        db_session, gid, source_id=source_id, title="Supper",
        fields={
            "title": {"value": "Supper", "quote": "Supper"},
            "place": {"value": "Village Hall", "quote": "Village Hall"},
            "cost": {"value": "£6", "quote": "£6"},
            "bring": {"value": "a dish", "quote": "bring a dish"},
            "host": {"value": "Ana", "quote": "Ana hosting"},
        },
    )
    response = _merge(client, owner, gid, candidate.id, event["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agreed_fields"] == ["title"]
    assert body["applied_fields"] == ["place_name"]  # "hall" refined to the fuller form
    assert set(body["filled_fields"]) == {"cost_text", "bring_list", "host_label"}
    merged = body["event"]
    assert merged["place_name"] == "Village Hall"
    assert merged["cost_text"] == "£6"
    assert merged["host_label"] == "Ana"

    # The changes are history, like any edit.
    changes = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/changes", headers=bearer(owner)
    ).json()
    changed = {row["field"] for row in changes}
    assert {"place_name", "cost_text", "bring_list", "host_label"} <= changed

    # The candidate's evidence became the event's links.
    links = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/sources", headers=bearer(owner)
    ).json()
    assert {row["field"] for row in links} >= {"title", "place", "cost", "bring", "host"}
    field_links = [row for row in links if row["field"]]
    assert all(row["source_id"] == source_id for row in field_links)

    # And the candidate is decided, pointing at its event.
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{candidate.id}", headers=bearer(owner)
    ).json()
    assert detail["status"] == "merged"
    assert detail["draft"]["merged_into_event_id"] == event["id"]


# ------------------------------------------------------------------
# Ties, contradictions, resolution (B-079)
# ------------------------------------------------------------------

_CLUB_STARTS = {
    "value": "this Saturday 7pm", "quote": "this Saturday",
    "resolved_date": "2026-10-24", "resolved_time": "19:00:00",
}


def _club_night_world(client, db_session):
    """An event minted from one source: Club night, Sat Oct 24,
    19:00 New York time (23:00Z), at the hall."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    source_id = _source(client, owner, gid, "Club chat", "Club night is on! this Saturday at the hall")
    candidate = _candidate(
        db_session, gid, source_id=source_id, title="Club night",
        fields={
            "title": {"value": "Club night", "quote": "Club night is on!"},
            "starts": dict(_CLUB_STARTS),
            "place": {"value": "the hall", "quote": "at the hall"},
        },
    )
    _approve(client, owner, gid, candidate.id)
    events = client.get(f"/api/v1/groups/{gid}/events", headers=bearer(owner)).json()
    assert len(events) == 1
    return owner, gid, events[0], source_id


def test_merge_tie_keeps_value_and_raises_contradiction(client, db_session):
    """7pm from one source, 8pm from another, neither
    supersedes: the event keeps 7pm and CARRIES the
    disagreement until a person resolves it (B-079)."""
    owner, gid, event, source_one = _club_night_world(client, db_session)
    assert event["starts_at"] == "2026-10-24T23:00:00Z"
    source_two = _source(client, owner, gid, "Poster photo", "Club night — Saturday, doors 8pm, the hall")
    candidate = _candidate(
        db_session, gid, source_id=source_two, title="Club night",
        fields={
            "title": {"value": "Club night", "quote": "Club night"},
            "starts": {
                "value": "Saturday 8pm", "quote": "doors 8pm",
                "resolved_date": "2026-10-24", "resolved_time": "20:00:00",
            },
            "place": {"value": "the hall", "quote": "the hall"},
        },
    )
    response = _merge(client, owner, gid, candidate.id, event["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kept_fields"] == ["starts_at"]
    assert len(body["contradiction_ids"]) == 1
    assert body["event"]["starts_at"] == "2026-10-24T23:00:00Z"  # unchanged

    contradictions = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/contradictions",
        headers=bearer(owner),
    ).json()
    assert len(contradictions) == 1
    row = contradictions[0]
    assert row["field"] == "starts_at" and row["status"] == "open"
    by_value = {entry["value"]: entry for entry in row["values"]}
    assert by_value["2026-10-24T23:00:00Z"]["current"] is True
    assert by_value["2026-10-24T23:00:00Z"]["source_ids"] == [source_one]
    assert by_value["2026-10-25T00:00:00Z"]["current"] is False
    assert by_value["2026-10-25T00:00:00Z"]["source_ids"] == [source_two]

    # The organiser's event face carries it too.
    detail = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    ).json()
    assert [c["id"] for c in detail["contradictions"]] == [row["id"]]


def test_resolve_contradiction_applies_the_chosen_value(client, db_session):
    owner, gid, event, _src = _club_night_world(client, db_session)
    source_two = _source(client, owner, gid, "Poster", "Club night Saturday 8pm hall")
    candidate = _candidate(
        db_session, gid, source_id=source_two, title="Club night",
        fields={
            "title": {"value": "Club night", "quote": "Club night"},
            "starts": {
                "value": "Saturday 8pm", "quote": "8pm",
                "resolved_date": "2026-10-24", "resolved_time": "20:00:00",
            },
        },
    )
    merged = _merge(client, owner, gid, candidate.id, event["id"]).json()
    (contradiction_id,) = merged["contradiction_ids"]

    # A value no source said is refused in plain words.
    bogus = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/contradictions/{contradiction_id}/resolve",
        json={"value": "2026-10-25T01:00:00Z"}, headers=bearer(owner),
    )
    assert bogus.status_code == 422

    resolved = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/contradictions/{contradiction_id}/resolve",
        json={"value": "2026-10-25T00:00:00Z", "note": "the poster was right"},
        headers=bearer(owner),
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolved_by"] == f"user:{owner['user_id']}"
    assert body["resolution"] == "2026-10-25T00:00:00Z"

    after = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    ).json()
    assert after["starts_at"] == "2026-10-25T00:00:00Z"
    assert after["needs_reannounce"] is True  # a material change
    assert after["contradictions"] == []
    changes = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/changes", headers=bearer(owner)
    ).json()
    starts_rows = [row for row in changes if row["field"] == "starts_at"]
    assert starts_rows[-1]["old_value"] == "2026-10-24T23:00:00Z"
    assert starts_rows[-1]["new_value"] == "2026-10-25T00:00:00Z"

    again = client.post(
        f"/api/v1/groups/{gid}/events/{event['id']}/contradictions/{contradiction_id}/resolve",
        json={"value": "2026-10-25T00:00:00Z"}, headers=bearer(owner),
    )
    assert again.status_code == 409


def test_a_second_source_settles_the_dispute(client, db_session):
    """The hall (one source) versus the school gym (one
    source) is a standoff; when a THIRD source also says the
    gym, the merge carries the better-sourced value and the
    contradiction closes — evidence, not seniority."""
    owner, gid, event, _src = _club_night_world(client, db_session)

    def gym_candidate(source_title, quote):
        source_id = _source(client, owner, gid, source_title, quote)
        return _candidate(
            db_session, gid, source_id=source_id, title="Club night",
            fields={
                "title": {"value": "Club night", "quote": "Club night"},
                "place": {"value": "the school gym", "quote": quote},
            },
        )

    first = gym_candidate("Newsletter", "Club night at the school gym")
    body = _merge(client, owner, gid, first.id, event["id"]).json()
    assert body["kept_fields"] == ["place_name"]
    assert len(body["contradiction_ids"]) == 1

    second = gym_candidate("Flyer", "Club night — the school gym")
    body = _merge(client, owner, gid, second.id, event["id"]).json()
    assert body["applied_fields"] == ["place_name"]
    after = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}", headers=bearer(owner)
    ).json()
    assert after["place_name"] == "the school gym"
    open_rows = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/contradictions",
        headers=bearer(owner),
    ).json()
    assert open_rows == []
    history = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/contradictions",
        params={"include_resolved": True}, headers=bearer(owner),
    ).json()
    assert len(history) == 1 and history[0]["status"] == "resolved"
    assert history[0]["resolution"] == "the school gym"


def test_unsupported_claim_never_fills(client, db_session):
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    event = _event(client, owner, gid, place_name="")
    source_id = _source(client, owner, gid, "Rumour", "maybe the village hall?")
    candidate = _candidate(
        db_session, gid, source_id=source_id, title="Supper",
        fields={
            "title": {"value": "Supper", "quote": "Supper"},
            "place": {"value": "Village Hall", "quote": "invented words", "supported": False},
        },
    )
    body = _merge(client, owner, gid, candidate.id, event["id"]).json()
    assert body["skipped_fields"] == ["place_name"]
    assert body["event"]["place_name"] == ""


# ------------------------------------------------------------------
# Guards
# ------------------------------------------------------------------


def test_merge_guards(client, db_session):
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]
    member = invite_and_accept(client, owner, gid, "mem@example.org", "member")
    event = _event(client, owner, gid)
    source_id = _source(client, owner, gid, "Thread", "Supper at the hall")
    candidate = _candidate(
        db_session, gid, source_id=source_id, title="Supper",
        fields={"title": {"value": "Supper", "quote": "Supper"}},
    )
    # A plain member cannot merge.
    denied = _merge(client, member, gid, candidate.id, event["id"])
    assert denied.status_code == 403
    # A task candidate cannot merge into an event.
    task = _candidate(
        db_session, gid, source_id=source_id, title="Bring chairs",
        fields={"title": {"value": "Bring chairs", "quote": "chairs"}},
        kind="task",
    )
    wrong_kind = _merge(client, owner, gid, task.id, event["id"])
    assert wrong_kind.status_code == 422
    # A decided candidate cannot merge again.
    decided = _candidate(
        db_session, gid, source_id=source_id, title="Supper",
        fields={"title": {"value": "Supper", "quote": "Supper"}},
        status="approved",
    )
    already = _merge(client, owner, gid, decided.id, event["id"])
    assert already.status_code == 409
    # A cancelled event takes no merges.
    cancelled = _event(client, owner, gid, title="Off")
    client.post(
        f"/api/v1/groups/{gid}/events/{cancelled['id']}/cancel",
        json={"reason": "called off"}, headers=bearer(owner),
    )
    fresh = _candidate(
        db_session, gid, source_id=source_id, title="Supper",
        fields={"title": {"value": "Supper", "quote": "Supper"}},
    )
    into_cancelled = _merge(client, owner, gid, fresh.id, cancelled["id"])
    assert into_cancelled.status_code == 409


# ------------------------------------------------------------------
# The golden triple (B-075 + B-076 acceptance)
# ------------------------------------------------------------------


def test_triple_announcement_resolves_to_one_event(client, db_session):
    """Chat, newsletter, flyer — one supper. The first
    approval mints it; the other two announcements MERGE in;
    the title dispute (Supper club vs Harvest Supper) settles
    when the third voice repeats the second's words; and the
    survivor answers for every field."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    gid = group["id"]

    chat_source = _source(client, owner, gid, "Chat", "Supper club is back — Friday at the hall, bring a dish to share")
    chat = _candidate(
        db_session, gid, source_id=chat_source, title="Supper club",
        fields={
            "title": {"value": "Supper club", "quote": "Supper club is back"},
            "starts": {
                "value": "Friday 6.30pm", "quote": "Friday",
                "resolved_date": "2026-11-06", "resolved_time": "18:30:00",
            },
            "place": {"value": "the hall", "quote": "at the hall"},
            "bring": {"value": "a dish to share", "quote": "bring a dish to share"},
        },
    )
    _approve(client, owner, gid, chat.id)
    events = client.get(f"/api/v1/groups/{gid}/events", headers=bearer(owner)).json()
    assert len(events) == 1
    event = events[0]
    assert event["title"] == "Supper club"
    assert event["starts_at"] == "2026-11-06T23:30:00Z"  # 18:30 EST

    news_source = _source(client, owner, gid, "Newsletter", "Harvest Supper — Friday 6 November, Village Hall, 6.30pm")
    newsletter = _candidate(
        db_session, gid, source_id=news_source, title="Harvest Supper",
        fields={
            "title": {"value": "Harvest Supper", "quote": "Harvest Supper"},
            "starts": {
                "value": "Friday 6 November 6.30pm", "quote": "Friday 6 November",
                "resolved_date": "2026-11-06", "resolved_time": "18:30:00",
            },
            "place": {"value": "Village Hall", "quote": "Village Hall"},
        },
    )
    # B-075: the queue proposes this merge before anyone acts.
    detail = client.get(
        f"/api/v1/groups/{gid}/candidates/{newsletter.id}", headers=bearer(owner)
    ).json()
    (match,) = [row for row in detail["matches"] if row["event_id"] == event["id"]]
    assert match["likely"] is True

    body = _merge(client, owner, gid, newsletter.id, event["id"]).json()
    assert body["applied_fields"] == ["place_name"]
    assert body["kept_fields"] == ["title"]
    assert len(body["contradiction_ids"]) == 1

    flyer_source = _source(client, owner, gid, "Flyer photo", "HARVEST SUPPER Fri Nov 6 6.30pm Village Hall £6 bring a dish to share")
    flyer = _candidate(
        db_session, gid, source_id=flyer_source, title="Harvest Supper",
        fields={
            "title": {"value": "Harvest Supper", "quote": "HARVEST SUPPER"},
            "starts": {
                "value": "Fri Nov 6 6.30pm", "quote": "Fri Nov 6 6.30pm",
                "resolved_date": "2026-11-06", "resolved_time": "18:30:00",
            },
            "place": {"value": "Village Hall", "quote": "Village Hall"},
            "cost": {"value": "£6", "quote": "£6"},
            "bring": {"value": "a dish to share", "quote": "bring a dish to share"},
        },
    )
    body = _merge(client, owner, gid, flyer.id, event["id"]).json()
    assert body["filled_fields"] == ["cost_text"]
    assert body["applied_fields"] == ["title"]  # two voices out-sourced one

    events = client.get(f"/api/v1/groups/{gid}/events", headers=bearer(owner)).json()
    assert len(events) == 1  # still exactly one supper
    final = events[0]
    assert final["title"] == "Harvest Supper"
    assert final["place_name"] == "Village Hall"
    assert final["cost_text"] == "£6"
    assert final["bring_list"] == "a dish to share"

    # B-076's acceptance: every field answers for itself.
    links = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/sources", headers=bearer(owner)
    ).json()
    by_field = {}
    for row in links:
        by_field.setdefault(row["field"], set()).add(row["source_id"])
    for field in ("title", "starts", "place", "cost", "bring"):
        assert by_field.get(field), f"no source answers for {field}"
    assert {chat_source, news_source, flyer_source} <= {
        row["source_id"] for row in links
    }
    # The title dispute closed itself by evidence.
    open_rows = client.get(
        f"/api/v1/groups/{gid}/events/{event['id']}/contradictions",
        headers=bearer(owner),
    ).json()
    assert open_rows == []
