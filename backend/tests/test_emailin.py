"""Email-in ingest tests (roadmap B-030).

The channel is the permission: an admin opens it (token shown
once, stored hashed), mail delivered with the token becomes a
source, and a forged token is refused AND logged — an audit row
exists for a mail that created nothing.
"""

from conftest import bearer, make_group, register


def _open_channel(client, owner, group):
    response = client.post(
        f"/api/v1/groups/{group['id']}/inbound-address", headers=bearer(owner)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _deliver(client, token, **body):
    payload = {"from": "news@example.org", "subject": "", "text": "", "html": ""}
    payload.update(body)
    return client.post(f"/api/v1/email-in/{token}", json=payload)


def test_email_in_turns_a_delivered_email_into_a_source(client):
    owner = register(client)
    group = make_group(client, owner)
    channel = _open_channel(client, owner, group)
    assert channel["token"] and channel["address"].startswith("in+")
    assert channel["address"].endswith("@inbox.local")

    response = _deliver(
        client,
        channel["token"],
        subject="October newsletter",
        text="Harvest supper on Saturday.\n\nBring a dish to share.",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "email"
    assert body["title"] == "October newsletter"
    assert body["granted_by"] == "system:email-in"
    whole = "\n".join(c["text"] for c in body["chunks"])
    assert "Harvest supper on Saturday." in whole
    # The sender rides on the chunks — provenance, not decoration.
    assert all(c["sender"] == "news@example.org" for c in body["chunks"])


def test_email_in_html_only_mail_uses_newsletter_sections(client):
    owner = register(client)
    group = make_group(client, owner)
    channel = _open_channel(client, owner, group)
    response = _deliver(
        client,
        channel["token"],
        subject="HTML edition",
        html="<h2>Supper</h2><p>Saturday.</p><h2>Walk</h2><p>Sunday.</p>",
    )
    assert response.status_code == 201, response.text
    chunks = response.json()["chunks"]
    assert len(chunks) == 2
    assert chunks[0]["text"].startswith("Supper")


def test_forged_token_is_refused_and_logged(client, db_session):
    owner = register(client)
    group = make_group(client, owner)
    _open_channel(client, owner, group)
    response = _deliver(client, "a-forged-token-that-opens-nothing", text="Let me in.")
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"

    # Logged: the audit trail holds a rejection row for it…
    from gpa.models.core import AuditLog

    rows = db_session.query(AuditLog).filter(AuditLog.action == "email_in.rejected").all()
    assert len(rows) == 1
    assert rows[0].group_id is None  # The token named no group.
    # …and no source was created for anyone.
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert listed.json() == []


def test_opening_a_channel_needs_admin(client):
    owner = register(client)
    member = register(client)
    group = make_group(client, owner)
    # Invite + accept the second person as a plain member.
    client.post(
        f"/api/v1/groups/{group['id']}/invites",
        json={"email": member["email"], "role": "member"},
        headers=bearer(owner),
    )
    invited = client.post(
        f"/api/v1/groups/{group['id']}/invites/accept", headers=bearer(member)
    )
    assert invited.status_code == 200, invited.text
    refused = client.post(
        f"/api/v1/groups/{group['id']}/inbound-address", headers=bearer(member)
    )
    assert refused.status_code == 403
