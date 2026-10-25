"""The mail transport boundary (roadmap B-095).

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

The real provider driver (SMTP/API) lands in Phase 12 (B-133)
behind this same protocol. Until then, nothing in this codebase
claims a real email reached a real inbox — the file IS the
mailbox in dev, and the boundary is named wherever it matters.
"""

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


def build_transport(settings: Settings) -> MailTransport:
    """The configured driver. Only "file" exists as a setting
    today; the real provider arrives in Phase 12 (B-133)."""
    if settings.mail_transport == "file":
        return FileTransport(settings)
    raise ValueError(
        f"Unknown GPA_MAIL_TRANSPORT '{settings.mail_transport}'. "
        "Known drivers: file (the provider driver lands in Phase 12)."
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
