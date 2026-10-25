"""Change alerts (B-097) + event invites (B-098).

B-097: approving a re-announcement tells EXACTLY the event's
RSVP'd people — members directly, invite guests at the address
they answered from, a member's +guests through the member —
and clears needs_reannounce. Approval is once-only.

B-098: an invite link is a bearer token (hash at rest, shown
once). It opens the event's public face, answers without an
account as ``guest:<email>``, and a tampered token is a 404
that creates nothing.
"""

import pytest

from conftest import bearer, make_group, register, unique_email
from gpa.models.notifications import Notification
from test_groups import invite_and_accept

NY = "America/New_York"


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner, home_timezone=NY)
    people = {"owner": owner}
    for name in ("ana", "ben", "cleo"):
        people[name] = invite_and_accept(
            client, owner, group["id"], unique_email(), "member"
        )
    created = client.post(
        f"/api/v1/groups/{group['id']}/events",
        json={
            "title": "Bonfire Night", "starts_at": "2026-11-05T19:00:00-05:00",
            "ends_at": "2026-11-05T22:00:00-05:00", "timezone": NY,
            "place_name": "The Old Field",
        },
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    return {"group": group, "people": people, "event": created.json()}


def answer(client, world, who, status="coming"):
    response = client.put(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/rsvp",
        json={"status": status}, headers=bearer(who),
    )
    assert response.status_code == 200, response.text
    return response.json()


def make_invite(client, world, email):
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/invites",
        json={"email": email}, headers=bearer(world["people"]["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def token_of(invite):
    return invite["link"].rstrip("/").split("/")[-1]


def alerts(db_session, event_id):
    return (
        db_session.query(Notification)
        .filter(
            Notification.event_id == event_id,
            Notification.kind == "change.announcement",
        )
        .all()
    )


# --- B-097 ----------------------------------------------------------


def test_approval_alerts_only_rsvpd_members(client, world, db_session):
    ppl = world["people"]
    answer(client, world, ppl["ana"])                    # coming
    answer(client, world, ppl["ben"], status="maybe")    # maybe — still told
    answer(client, world, ppl["cleo"], status="cant")    # can't — NOT told
    # Dan never answered; he is not in this world at all.
    event = world["event"]
    current = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        headers=bearer(ppl["owner"]),
    ).json()
    moved = client.patch(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        json={"place_name": "The New Field"},
        headers={**bearer(ppl["owner"]), "If-Match": f'"{current["etag"]}"'},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["needs_reannounce"] is True

    draft = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}/reannounce-drafts",
        headers=bearer(ppl["owner"]),
    )
    assert draft.status_code == 201, draft.text
    draft = draft.json()
    assert draft["sent_at"] is None
    assert "The New Field" in draft["body"]

    approved = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}"
        f"/reannounce-drafts/{draft['id']}/approve",
        headers=bearer(ppl["owner"]),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["sent_at"] is not None
    assert approved.json()["sent_by"] == f"user:{ppl['owner']['user_id']}"

    rows = alerts(db_session, event["id"])
    recipients = {row.recipient_label for row in rows}
    assert recipients == {
        f"user:{ppl['ana']['user_id']}", f"user:{ppl['ben']['user_id']}",
    }
    after = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        headers=bearer(ppl["owner"]),
    ).json()
    assert after["needs_reannounce"] is False

    # Approval is once-only.
    again = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}"
        f"/reannounce-drafts/{draft['id']}/approve",
        headers=bearer(ppl["owner"]),
    )
    assert again.status_code == 409


def test_guest_alerted_at_their_invited_address(client, world, db_session):
    invite = make_invite(client, world, "guest@example.org")
    responded = client.post(
        f"/api/v1/invites/{token_of(invite)}/rsvp", json={"status": "coming"}
    )
    assert responded.status_code == 201, responded.text
    event = world["event"]
    current = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        headers=bearer(world["people"]["owner"]),
    ).json()
    client.patch(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}",
        json={"place_name": "The New Field"},
        headers={**bearer(world["people"]["owner"]), "If-Match": f'"{current["etag"]}"'},
    )
    draft = client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}/reannounce-drafts",
        headers=bearer(world["people"]["owner"]),
    ).json()
    client.post(
        f"/api/v1/groups/{world['group']['id']}/events/{event['id']}"
        f"/reannounce-drafts/{draft['id']}/approve",
        headers=bearer(world["people"]["owner"]),
    )
    rows = alerts(db_session, event["id"])
    assert {row.recipient_label for row in rows} == {"guest:guest@example.org"}
    assert rows[0].recipient_email == "guest@example.org"


# --- B-098 ----------------------------------------------------------


def test_invite_public_face_and_guest_rsvp(client, world, db_session):
    invite = make_invite(client, world, "walker@example.org")
    assert invite["status"] == "pending"
    assert invite["token_prefix"] and invite["link"]

    face = client.get(f"/api/v1/invites/{token_of(invite)}")
    assert face.status_code == 200, face.text
    face = face.json()
    assert face["title"] == "Bonfire Night"
    assert face["account_offered"] is True
    # The public projection: no host, no internals.
    assert "host_label" not in face and "created_by" not in face

    responded = client.post(
        f"/api/v1/invites/{token_of(invite)}/rsvp",
        json={"status": "coming", "guests": 1},
    )
    assert responded.status_code == 201, responded.text
    body = responded.json()
    assert body["principal_label"] == "guest:walker@example.org"
    assert body["account_offered"] is True

    listing = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/rsvps",
        headers=bearer(world["people"]["owner"]),
    ).json()
    guests = [r for r in listing["rsvps"] if r["principal_label"].startswith("guest:")]
    assert len(guests) == 1 and guests[0]["status"] == "coming"
    assert guests[0]["guests"] == 1

    invites = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/invites",
        headers=bearer(world["people"]["owner"]),
    ).json()
    assert invites[0]["status"] == "accepted" and invites[0]["rsvp_id"] == body["rsvp_id"]
    # Listings never carry the token again.
    assert invites[0]["link"] is None


def test_tampered_token_fails_and_creates_nothing(client, world, db_session):
    invite = make_invite(client, world, "victim@example.org")
    token = token_of(invite)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert client.get(f"/api/v1/invites/{tampered}").status_code == 404
    assert client.post(
        f"/api/v1/invites/{tampered}/rsvp", json={"status": "coming"}
    ).status_code == 404
    listing = client.get(
        f"/api/v1/groups/{world['group']['id']}/events/{world['event']['id']}/rsvps",
        headers=bearer(world["people"]["owner"]),
    ).json()
    assert listing["rsvps"] == []


def test_member_invite_also_notifies_the_member(client, world, db_session):
    ana = world["people"]["ana"]
    invite = make_invite(client, world, ana["email"])
    assert invite["user_id"] == ana["user_id"]
    rows = (
        db_session.query(Notification)
        .filter(Notification.kind == "invite", Notification.recipient_user_id == ana["user_id"])
        .all()
    )
    assert len(rows) == 1 and rows[0].in_app is True
    face = client.get(f"/api/v1/invites/{token_of(invite)}").json()
    assert face["account_offered"] is False
