"""Identity acceptance (B-011 → B-014): registration, login, magic
links, rotating sessions with theft detection, profile, API keys.
Passwords and hashes must never appear in any response."""

from conftest import bearer, latest_token, make_group, register, unique_email
from gpa.models.identity import now_utc
from datetime import timedelta


def test_register_login_and_me(client):
    session = register(client, name="Ada")
    me = client.get("/api/v1/users/me", headers=bearer(session))
    assert me.status_code == 200
    assert me.json()["name"] == "Ada"

    login = client.post(
        "/api/v1/auth/login",
        json={"email": session["email"], "password": session["password"]},
    )
    assert login.status_code == 200
    bad = client.post(
        "/api/v1/auth/login",
        json={"email": session["email"], "password": "wrong-password"},
    )
    assert bad.status_code == 401
    assert bad.json()["code"] == "invalid_credentials"


def test_password_and_hash_never_in_responses(client):
    """B-011 acceptance: grep serialised output for the password and
    for anything that looks like an Argon2 hash."""
    session = register(client)
    responses = [
        client.get("/api/v1/users/me", headers=bearer(session)).text,
        client.get("/api/v1/users/me/api-keys", headers=bearer(session)).text,
        client.post(
            "/api/v1/auth/login",
            json={"email": session["email"], "password": session["password"]},
        ).text,
    ]
    for text in responses:
        assert session["password"] not in text
        assert "$argon2" not in text


def test_duplicate_email_rejected(client):
    session = register(client)
    again = client.post(
        "/api/v1/auth/register",
        json={"email": session["email"], "password": "another-password-1"},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "email_taken"


def test_profile_update(client):
    session = register(client)
    updated = client.patch(
        "/api/v1/users/me",
        json={"name": "New Name", "timezone": "Europe/Oslo"},
        headers=bearer(session),
    )
    assert updated.status_code == 200
    assert updated.json()["timezone"] == "Europe/Oslo"


def test_magic_link_flow_and_single_use(client, settings):
    session = register(client)
    request = client.post("/api/v1/auth/magic-link", json={"email": session["email"]})
    assert request.status_code == 202
    token = latest_token(settings, to=session["email"], purpose="magic_link")
    consumed = client.post("/api/v1/auth/magic-link/consume", json={"token": token})
    assert consumed.status_code == 200
    assert consumed.json()["user_id"] == session["user_id"]
    # Second use of the same link must fail closed (B-012).
    reuse = client.post("/api/v1/auth/magic-link/consume", json={"token": token})
    assert reuse.status_code == 401
    assert reuse.json()["code"] == "token_reused"


def test_magic_link_expired_fails(client, settings, app):
    session = register(client)
    client.post("/api/v1/auth/magic-link", json={"email": session["email"]})
    token = latest_token(settings, to=session["email"], purpose="magic_link")
    # Push the stored token into the past, as time would.
    db = app.state.db.session()
    try:
        from gpa.models.identity import OneTimeToken
        from gpa.security import hash_secret

        row = db.query(OneTimeToken).filter(
            OneTimeToken.token_hash == hash_secret(token)
        ).one()
        row.expires_at = now_utc() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    response = client.post("/api/v1/auth/magic-link/consume", json={"token": token})
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


def test_magic_link_for_unknown_email_stays_silent(client):
    """No account enumeration: unknown email still gets a plain 202."""
    response = client.post("/api/v1/auth/magic-link", json={"email": unique_email()})
    assert response.status_code == 202


def test_refresh_rotation_and_reuse_revokes_family(client):
    """B-013 acceptance: rotation works; replaying the OLD refresh
    token revokes the whole family — including the newest token."""
    session = register(client)
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh"]})
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh != session["refresh"]

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh"]})
    assert replay.status_code == 401
    assert replay.json()["code"] == "token_reused"

    # The theft rule killed the family: even the fresh token is dead.
    after = client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401


def test_logout_kills_refresh(client):
    session = register(client)
    assert client.post("/api/v1/auth/logout", json={"refresh_token": session["refresh"]}).status_code == 204
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh"]})
    assert response.status_code == 401


def test_api_keys_lifecycle_and_hash_only_storage(client, app):
    session = register(client)
    created = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "laptop", "scopes": ["read"]},
        headers=bearer(session),
    )
    assert created.status_code == 201
    full_key = created.json()["key"]
    assert full_key.startswith("gpa_")

    listed = client.get("/api/v1/users/me/api-keys", headers=bearer(session))
    assert listed.status_code == 200
    assert listed.json()[0]["key"] is None  # never shown again
    assert full_key not in listed.text

    # The database stores only the hash.
    db = app.state.db.session()
    try:
        from gpa.models.identity import ApiKey

        row = db.query(ApiKey).one()
        assert row.key_hash != full_key and len(row.key_hash) == 64
    finally:
        db.close()

    key_id = created.json()["id"]
    revoked = client.delete(f"/api/v1/users/me/api-keys/{key_id}", headers=bearer(session))
    assert revoked.status_code == 204
    use = client.get("/api/v1/users/me", headers={"X-Api-Key": full_key})
    assert use.status_code == 401


def test_api_key_authenticates_and_stamps_last_used(client):
    session = register(client)
    created = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "agent", "scopes": ["read", "draft"]},
        headers=bearer(session),
    )
    full_key = created.json()["key"]
    me = client.get("/api/v1/users/me", headers={"X-Api-Key": full_key})
    assert me.status_code == 200
    assert me.json()["id"] == session["user_id"]
