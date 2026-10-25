"""The mail transport boundary (roadmap B-095, B-133).

Every notification email leaves through ONE protocol,
``MailTransport.send`` — the same adapter discipline as the AI
client (B-035): the outbox machinery (services/notifications.py)
is real around it, and the driver behind it is a deployment
choice, never a code change:

- ``FileTransport`` — the dev driver. A send writes the message
  as a JSON file into the outbox directory, through
  services/mail.py — exactly where an operator (or a test) can
  read it. This is the default (``GPA_MAIL_TRANSPORT=file``).
- ``CaptureTransport`` — in-memory, for tests: records every
  send so a test can assert what WOULD have gone out.
- ``FailingTransport`` — always raises ``TransportError``: the
  dead mail server of B-095's kill test, as a driver.
- ``SmtpTransport`` — the real provider driver (B-133): plain
  stdlib SMTP to whatever provider the operator configured,
  TLS + login when set, returning the message's Message-ID.

**The honest boundary, kept:** no mail provider is configured
in this environment, so no test here claims a real email
reached a real inbox. The roadmap's acceptance for B-133 — a
real digest landing in a real inbox — is a **pre-submission
checklist item, recorded, not assumed** (see TEST_REPORT's
Phase 12 addendum). What ships here is the driver, fully
wired and proven against a stub SMTP server, plus the
SPF/DKIM/DMARC notes in DEPLOYMENT.md that make a real
deployment's mail land.
"""

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from gpa.config import Settings
from gpa.services import mail as mail_service


class TransportError(Exception):
    """The transport could not take the message (server down,
    refused, timed out). The outbox treats this as retryable:
    the notification is marked failed and its job backs off —
    the failure NEVER propagates into a user request."""


class MailTransport(Protocol):
    """What a mail driver is: one send, returning a provider id."""

    def send(
        self, *, to: str, subject: str, body_text: str, body_html: str = ""
    ) -> str:
        """Deliver one message; return the provider's message id.
        Raises TransportError when the message was not taken."""
        ...


class FileTransport:
    """The dev file driver: a send is a JSON file in the outbox."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def send(
        self, *, to: str, subject: str, body_text: str, body_html: str = ""
    ) -> str:
        # The rendered HTML rides in the body the file driver
        # stores (its ``body`` field is the readable text; the
        # html is appended in a marked section so one file is
        # the whole message a mail client would have shown).
        body = body_text
        if body_html:
            body = f"{body_text}\n\n----- HTML version -----\n{body_html}"
        return mail_service.send_mail(
            self._settings, to=to, subject=subject, body=body, purpose="notification"
        )


class CaptureTransport:
    """Test driver: remembers sends instead of delivering them."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(
        self, *, to: str, subject: str, body_text: str, body_html: str = ""
    ) -> str:
        self.sent.append(
            {"to": to, "subject": subject, "body_text": body_text, "body_html": body_html}
        )
        return f"capture-{len(self.sent)}"


class FailingTransport:
    """Test driver: the mail server is down, every time."""

    def __init__(self, message: str = "mail server unreachable"):
        self._message = message
        self.attempts = 0

    def send(
        self, *, to: str, subject: str, body_text: str, body_html: str = ""
    ) -> str:
        self.attempts += 1
        raise TransportError(self._message)


class SmtpTransport:
    """The real provider driver (B-133): stdlib SMTP out.

    Talks to the operator's configured provider (any
    SMTP-speaking service) with STARTTLS and login when the
    settings carry them. The connection is made through an
    injectable factory — production gets ``smtplib.SMTP``;
    tests hand in a recording stub server, so the driver's
    wire behaviour (message shape, TLS, login, refusal →
    TransportError) is proven without a network or a claim
    that any real inbox received anything.
    """

    def __init__(self, settings: Settings, *, connect=None):
        self._settings = settings
        self._connect = connect or (
            lambda host, port: smtplib.SMTP(host, port, timeout=30)
        )

    def send(
        self, *, to: str, subject: str, body_text: str, body_html: str = ""
    ) -> str:
        settings = self._settings
        message = EmailMessage()
        message["From"] = settings.mail_from_address
        message["To"] = to
        message["Subject"] = subject
        message_id = make_msgid()
        message["Message-ID"] = message_id
        message.set_content(body_text)
        if body_html:
            message.add_alternative(body_html, subtype="html")
        try:
            server = self._connect(settings.mail_smtp_host, settings.mail_smtp_port)
            try:
                if settings.mail_smtp_use_tls:
                    server.starttls()
                if settings.mail_smtp_username:
                    server.login(
                        settings.mail_smtp_username, settings.mail_smtp_password
                    )
                server.send_message(message)
            finally:
                try:
                    server.quit()
                except Exception:
                    pass  # The send already decided the outcome.
        except TransportError:
            raise
        except Exception as err:
            # Any SMTP failure is a transport failure in the
            # outbox's vocabulary: retryable, recorded, never
            # propagated into a user request.
            raise TransportError(f"SMTP send failed: {err}") from err
        return message_id


def build_transport(settings: Settings) -> MailTransport:
    """The configured driver: "file" (dev) or "smtp" (B-133's
    provider driver). An smtp setting without its host or
    From address fails startup in plain words — a deployment
    that cannot send must say so at boot, not at 2 a.m."""
    if settings.mail_transport == "file":
        return FileTransport(settings)
    if settings.mail_transport == "smtp":
        missing = [
            name
            for name, value in (
                ("GPA_MAIL_SMTP_HOST", settings.mail_smtp_host),
                ("GPA_MAIL_FROM_ADDRESS", settings.mail_from_address),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "GPA_MAIL_TRANSPORT=smtp needs "
                + " and ".join(missing)
                + " set. See DEPLOYMENT.md for the provider setup."
            )
        return SmtpTransport(settings)
    raise ValueError(
        f"Unknown GPA_MAIL_TRANSPORT '{settings.mail_transport}'. "
        "Known drivers: file, smtp."
    )


# The process-wide registry — the ai.client pattern (B-035):
# create_app and the worker set the configured transport here
# so job handlers (which receive only db + payload) reach the
# same driver. Tests swap in capture/failing transports here.
_transport: MailTransport | None = None


def set_transport(transport: MailTransport | None) -> None:
    global _transport
    _transport = transport


def get_transport() -> MailTransport:
    if _transport is None:
        raise TransportError(
            "No mail transport is configured in this process. "
            "create_app (or the worker) sets one from settings."
        )
    return _transport
