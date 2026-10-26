"""Advanced account security acceptance (roadmap B-174).

TOTP MFA (enrol → challenge → single-use codes), the session
list with per-device revoke, the password policy (length +
local breached list), progressive lockout, the new-device
notice — and the slice's named guarantee: a stolen refresh
token still dies by B-013's rotation rule even with MFA on.
"""

import json
from datetime import timedelta
from pathlib import Path

from conftest import bearer, make_group, register, unique_email
from gpa.models.identity import now_utc
from gpa.services.account_security import totp_code


def _enable_mfa(client, session) -> str:
    """Enrol the session's user in MFA; return the TOTP secret
    (shown once, at setup — the way a person's app receives it)."""
    setup = client.post("/api/v1/users/me/mfa/setup", headers=bearer(session))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["enabled"] is False
    code = totp_code(secret)
    confirmed = client.post(
        "/api/v1/users/me/mfa/confirm", json={"code": code}, headers=bearer(session)
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["enabled"] is True
    return secret


def _login(client, session, **kwargs):
    return client.post(
        "/api/v1/auth/login",
        json={"email": session["email"], "password": session["password"]},
        **kwargs,
    )


def test_totp_rfc6238_vectors():
    """The hand-rolled TOTP reproduces the RFC 6238 SHA-1 test
    vectors — the same arithmetic every authenticator app runs."""
    # ASCII "12345678901234567890" in base32 (the RFC's seed).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, at=59) == "287082"
    assert totp_code(secret, at=1111111109) == "081804"
    assert totp_code(secret, at=1234567890) == "005924"


def test_password_policy(client):
    """Length + the local breached list, in plain 422 words."""
    short = client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": "eleven-char"},
    )
    # "eleven-char" is 11 chars: the schema's floor (8) passes it,
    # the policy's floor (12) refuses it.
    assert short.status_code == 422
    assert short.json()["code"] == "password_too_weak"
    breached = client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": "password1234"},
    )
    assert breached.status_code == 422
    assert breached.json()["code"] == "password_too_weak"


def test_mfa_full_flow_and_code_single_use(client):
    session = register(client)
    secret = _enable_mfa(client, session)

    # Sign-in now stops at the challenge: no session tokens yet.
    first = _login(client, session)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["mfa_required"] is True
    assert body["access_token"] is None
    challenge = body["mfa_challenge"]

    wrong = client.post(
        "/api/v1/auth/login/mfa", json={"challenge_token": challenge, "code": "000000"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "mfa_code_invalid"

    # A mistyped code does not burn the challenge; the right one
    # inside its five minutes mints the session.
    code = totp_code(secret)
    answered = client.post(
        "/api/v1/auth/login/mfa", json={"challenge_token": challenge, "code": code}
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["access_token"]

    # The SAME code against a FRESH challenge is a replay: its
    # time step is already spent.
    second = _login(client, session)
    replay = client.post(
        "/api/v1/auth/login/mfa",
        json={"challenge_token": second.json()["mfa_challenge"], "code": code},
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "mfa_code_invalid"

    # And the spent challenge itself cannot be answered twice.
    again = client.post(
        "/api/v1/auth/login/mfa", json={"challenge_token": challenge, "code": totp_code(secret)}
    )
    assert again.status_code == 401
    assert again.json()["code"] == "token_reused"


def test_mfa_setup_does_not_protect_until_confirmed(client):
    session = register(client)
    setup = client.post("/api/v1/users/me/mfa/setup", headers=bearer(session))
    assert setup.status_code == 200
    status = client.get("/api/v1/users/me/mfa", headers=bearer(session))
    assert status.json() == {
        "enabled": False,
        "setup_pending": True,
        "offer_required": False,
    }
    # Still a plain password sign-in: enrolment alone is not MFA.
    assert _login(client, session).json()["mfa_required"] is False


def test_mfa_offer_required_for_group_owners(client):
    session = register(client)
    before = client.get("/api/v1/users/me/mfa", headers=bearer(session))
    assert before.json()["offer_required"] is False
    make_group(client, session)
    after = client.get("/api/v1/users/me/mfa", headers=bearer(session))
    assert after.json()["offer_required"] is True


def test_disable_mfa_revokes_sessions(client):
    session = register(client)
    secret = _enable_mfa(client, session)
    code = totp_code(secret)
    disabled = client.post(
        "/api/v1/users/me/mfa/disable", json={"code": code}, headers=bearer(session)
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    # The old refresh token died with the disable.
    rotated = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session["refresh"]}
    )
    assert rotated.status_code == 401
    # And sign-in is a plain password road again.
    assert _login(client, session).json()["mfa_required"] is False


def test_stolen_refresh_still_dies_with_mfa_on(client):
    """B-174's named guarantee: MFA does not soften B-013 —
    replaying a rotated refresh token revokes the whole family."""
    session = register(client)
    secret = _enable_mfa(client, session)
    challenge = _login(client, session).json()["mfa_challenge"]
    answered = client.post(
        "/api/v1/auth/login/mfa",
        json={"challenge_token": challenge, "code": totp_code(secret)},
    )
    refresh = answered.json()["refresh_token"]
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert rotated.status_code == 200
    newest = rotated.json()["refresh_token"]
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401
    assert replay.json()["code"] == "token_reused"
    # The theft rule took the newest token down with the family.
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": newest}
    ).status_code == 401


def test_session_list_and_per_device_revoke(client):
    first = register(client)  # device one (the default test UA)
    second = _login(client, first, headers={"user-agent": "phone-browser"})
    assert second.status_code == 200
    second_refresh = second.json()["refresh_token"]

    listed = client.get("/api/v1/users/me/sessions", headers=bearer(first))
    assert listed.status_code == 200
    sessions = listed.json()
    assert len(sessions) == 2
    assert all(s["status"] == "active" for s in sessions)
    # The caller is marked current; the phone session names itself.
    assert sum(1 for s in sessions if s["current"]) == 1
    phone = next(s for s in sessions if s["user_agent"] == "phone-browser")
    assert phone["current"] is False

    revoked = client.request(
        "DELETE", f"/api/v1/users/me/sessions/{phone['family_id']}", headers=bearer(first)
    )
    assert revoked.status_code == 204
    # That device is signed out; this one still rotates fine.
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second_refresh}
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh"]}
    ).status_code == 200
    after = client.get("/api/v1/users/me/sessions", headers=bearer(first))
    states = {s["family_id"]: s["status"] for s in after.json()}
    assert states[phone["family_id"]] == "revoked"

    missing = client.request(
        "DELETE", "/api/v1/users/me/sessions/no-such-family", headers=bearer(first)
    )
    assert missing.status_code == 404


def test_progressive_lockout(client, app):
    session = register(client)
    for _ in range(5):
        bad = client.post(
            "/api/v1/auth/login",
            json={"email": session["email"], "password": "wrong-password-1"},
        )
        assert bad.status_code == 401
        assert bad.json()["code"] == "invalid_credentials"
    # Locked: even the RIGHT password is refused, in plain words.
    locked = _login(client, session)
    assert locked.status_code == 423
    assert locked.json()["code"] == "account_locked"
    # When the lock lifts (time passes — simulated on the row),
    # the right password works and the count is cleared.
    db = app.state.db.session()
    try:
        from gpa.models.identity import User

        user = db.query(User).filter(User.email == session["email"]).one()
        user.locked_until = now_utc() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert _login(client, session).status_code == 200
    one_more_bad = client.post(
        "/api/v1/auth/login",
        json={"email": session["email"], "password": "wrong-password-1"},
    )
    assert one_more_bad.status_code == 401  # counted fresh, not locked


def test_new_device_signin_sends_notice(client, settings):
    email = unique_email()
    first = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-9", "name": "Ada"},
        headers={"user-agent": "home-laptop"},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-9"},
        headers={"user-agent": "stranger-phone"},
    )
    assert second.status_code == 200
    notices = []
    for path in Path(settings.outbox_dir).glob("*.json"):
        message = json.loads(path.read_text(encoding="utf-8"))
        if message["to"] == email and message["purpose"] == "login_new_device":
            notices.append(message)
    assert len(notices) == 1
    assert "stranger-phone" in notices[0]["body"]
    # The same stranger device again is no longer news.
    client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-9"},
        headers={"user-agent": "stranger-phone"},
    )
    again = [
        p for p in Path(settings.outbox_dir).glob("*.json")
        if json.loads(p.read_text(encoding="utf-8"))["purpose"] == "login_new_device"
    ]
    assert len(again) == 1
