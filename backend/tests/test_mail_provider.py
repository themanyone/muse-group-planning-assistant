"""The real mail provider driver (roadmap B-133).

What is proven here: the SMTP driver behind the B-095
boundary — message shape, TLS + login when configured, a
refusal becoming a TransportError the outbox can retry, an
end-to-end notification delivered through it, the startup
refusal of a half-configured smtp deployment, and the
provider webhook's HMAC signature check (B-102's receiver,
signed at last).

What is NOT claimed: a real digest in a real inbox. No mail
provider is configured in this environment; that acceptance
is a pre-submission checklist item (DEPLOYMENT.md), recorded
in the test report as not run — never assumed.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from conftest import bearer, make_group, make_settings, register
from gpa.config import Settings
from gpa.main import create_app
from gpa.services import jobs as job_service
from gpa.services import mailer
from gpa.services import notifications as notify_service


class FakeSmtpServer:
    """A recording stub SMTP server (the stub transport the
    roadmap's stubbed-transport discipline allows): the
    driver's wire behaviour is asserted against it."""

    def __init__(self):
        self.tls_started = False
        self.login_seen = None
        self.messages = []
        self.quit_called = False

    def starttls(self):
        self.tls_started = True

    def login(self, username, password):
        self.login_seen = (username, password)

    def send_message(self, message):
        self.messages.append(message)

    def quit(self):
        self.quit_called = True


def smtp_settings(tmp_path, **overrides):
    values = dict(
        mail_transport="smtp",
        mail_smtp_host="smtp.provider.example",
        mail_smtp_port=587,
        mail_smtp_username="village-account",
        mail_smtp_password="provider-password-not-real",
        mail_from_address="planning@village.example",
    )
    values.update(overrides)
    return make_settings(tmp_path, **values)


def test_smtp_driver_sends_a_real_message_shape(tmp_path):
    server = FakeSmtpServer()
    transport = mailer.SmtpTransport(
        smtp_settings(tmp_path), connect=lambda host, port: server
    )
    message_id = transport.send(
        to="ana@example.org", subject="Tonight at the hall",
        body_text="Doors at seven.", body_html="<p>Doors at seven.</p>",
    )
    assert message_id.startswith("<") and message_id.endswith(">")
    assert server.tls_started is True
    assert server.login_seen == (
        "village-account", "provider-password-not-real"
    )
    assert server.quit_called is True
    (message,) = server.messages
    assert message["From"] == "planning@village.example"
    assert message["To"] == "ana@example.org"
    assert message["Subject"] == "Tonight at the hall"
    assert message["Message-ID"] == message_id
    assert "Doors at seven." in message.get_body(("plain",)).get_content()
    assert "Doors at seven." in message.get_body(("html",)).get_content()


def test_smtp_refusal_becomes_a_transport_error(tmp_path):
    def refused(host, port):
        raise OSError("connection refused")

    transport = mailer.SmtpTransport(
        smtp_settings(tmp_path), connect=refused
    )
    with pytest.raises(mailer.TransportError):
        transport.send(to="a@example.org", subject="x", body_text="y")


def test_smtp_configuration_fails_startup_in_plain_words(tmp_path):
    with pytest.raises(ValueError) as err:
        mailer.build_transport(smtp_settings(tmp_path, mail_smtp_host=""))
    assert "GPA_MAIL_SMTP_HOST" in str(err.value)
    with pytest.raises(ValueError) as err:
        mailer.build_transport(
            smtp_settings(tmp_path, mail_from_address="")
        )
    assert "GPA_MAIL_FROM_ADDRESS" in str(err.value)


def test_outbox_delivers_through_the_smtp_driver(client, tmp_path, db_session, app):
    owner = register(client)
    group = make_group(client, owner)
    server = FakeSmtpServer()
    settings = smtp_settings(tmp_path)
    mailer.set_transport(
        mailer.SmtpTransport(settings, connect=lambda host, port: server)
    )
    try:
        from gpa.models.groups import Group
        from gpa.models.identity import User

        row = notify_service.create_notification(
            db_session,
            group=db_session.get(Group, group["id"]),
            recipient_user=db_session.get(User, owner["user_id"]),
            recipient_label=f"user:{owner['user_id']}",
            kind="digest",
            subject="Your week at the hall",
            body_text="One social, two rehearsals.",
        )
        db_session.commit()
        assert row is not None and row.send_email is True
        assert job_service.process_one(db_session, app.state.settings, "w-mail")
        delivered = db_session.get(type(row), row.id)
        assert delivered.status == "sent"
        (message,) = server.messages
        assert message["Subject"] == "Your week at the hall"
        assert message["To"] == owner["email"]
    finally:
        mailer.set_transport(None)


def _signed_client(tmp_path):
    settings = make_settings(tmp_path, mail_webhook_secret="hook-secret-9")
    return TestClient(create_app(settings)), settings


def test_mail_webhook_requires_a_verifying_signature(tmp_path):
    hook_client, _settings = _signed_client(tmp_path)
    payload = {"email": "ana@example.org", "event_type": "bounce"}
    raw = json.dumps(payload).encode("utf-8")

    unsigned = hook_client.post(
        "/api/v1/webhooks/mail/testprovider",
        content=raw, headers={"Content-Type": "application/json"},
    )
    assert unsigned.status_code == 401, unsigned.text
    assert unsigned.json()["code"] == "token_invalid"

    wrong = hook_client.post(
        "/api/v1/webhooks/mail/testprovider",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "x-gpa-mail-signature": "0" * 64,
        },
    )
    assert wrong.status_code == 401

    signature = hmac.new(b"hook-secret-9", raw, hashlib.sha256).hexdigest()
    signed = hook_client.post(
        "/api/v1/webhooks/mail/testprovider",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "x-gpa-mail-signature": signature,
        },
    )
    assert signed.status_code == 201, signed.text
    assert signed.json()["event_type"] == "bounce"
    assert signed.json()["provider"] == "testprovider"
