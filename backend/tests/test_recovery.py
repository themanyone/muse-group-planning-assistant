"""Recovery acceptance (B-020 + B-021): password reset, password
change, email change — sessions die when credentials change — and
the auth rate limiter's hammer test."""

from conftest import bearer, latest_token, make_settings, register
from fastapi.testclient import TestClient
from gpa.main import create_app


def test_password_reset_flow_and_old_sessions_die(client, settings):
    session = register(client)
    old_refresh = session["refresh"]
    assert client.post(
        "/api/v1/auth/password-reset", json={"email": session["email"]}
    ).status_code == 202
    token = latest_token(settings, to=session["email"], purpose="password_reset")
    confirmed = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-password-7"},
    )
    assert confirmed.status_code == 204
    # Old password dead, new password works.
    assert client.post(
        "/api/v1/auth/login",
        json={"email": session["email"], "password": session["password"]},
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"email": session["email"], "password": "brand-new-password-7"},
    ).status_code == 200
    # And the pre-reset refresh token died with the reset (B-020).
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    ).status_code == 401


def test_password_change_revokes_sessions(client):
    session = register(client)
    changed = client.post(
        "/api/v1/users/me/password",
        json={"current_password": session["password"], "new_password": "even-newer-password-8"},
        headers=bearer(session),
    )
    assert changed.status_code == 204
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session["refresh"]}
    ).status_code == 401
    wrong = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "not-the-password", "new_password": "whatever-12345"},
        headers=bearer(session),
    )
    assert wrong.status_code == 401


def test_email_change_flow(client, settings):
    session = register(client)
    new_email = "moved@example.org"
    started = client.post(
        "/api/v1/users/me/email-change",
        json={"new_email": new_email},
        headers=bearer(session),
    )
    assert started.status_code == 202
    # The token went to the NEW address (mail outbox proves routing).
    token = latest_token(settings, to=new_email, purpose="email_change")
    confirmed = client.post(
        "/api/v1/users/me/email-change/confirm", json={"token": token}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["email"] == new_email
    # Sign-in now works under the new address.
    assert client.post(
        "/api/v1/auth/login",
        json={"email": new_email, "password": session["password"]},
    ).status_code == 200


def test_auth_rate_limit_hammer(tmp_path):
    """B-021 acceptance: hammering an auth route earns a 429 in
    problem+json with a Retry-After header."""
    settings = make_settings(tmp_path, auth_rate_per_minute=3)
    app = create_app(settings)
    with TestClient(app) as client:
        codes = [
            client.post(
                "/api/v1/auth/login",
                json={"email": "a@example.org", "password": "nope-nope-nope"},
            ).status_code
            for _ in range(5)
        ]
    assert 429 in codes
    # The limited response itself wears the standard shape.
    settings2 = make_settings(tmp_path / "second", auth_rate_per_minute=1)
    app2 = create_app(settings2)
    with TestClient(app2) as client2:
        client2.post("/api/v1/auth/login", json={"email": "a@example.org", "password": "x" * 9})
        limited = client2.post(
            "/api/v1/auth/login", json={"email": "a@example.org", "password": "x" * 9}
        )
        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
        assert limited.headers.get("retry-after")
