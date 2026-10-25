"""The Messenger notification transport (roadmap B-134).

Where B-125's access exists, a reminder can ride Messenger
to a member who opted in there. **B-125's access does not
exist** — it was never granted to this project — so this
boundary's only real driver is the unavailable one, and the
slice ships exactly what the roadmap orders for that case: a
clean ``driver_unavailable``. The email and in-app channels
carry the load; a notification that asked for Messenger
records ``messenger_status = "driver_unavailable"`` on its
row and is delivered by its other channels exactly as if
Messenger had never been asked.

The shape mirrors services/mailer.py (B-095's discipline):
one protocol, a process registry, capture/failing drivers
for tests. If access is granted one day, a real driver lands
behind ``build_transport`` and nothing else changes — the
preference switches, the quiet-hours hold, and the delivery
record are already channel-shaped and shared (that sharing
is this slice's acceptance, proven by its tests).
"""

from typing import Protocol

from gpa.config import Settings


class MessengerUnavailable(Exception):
    """The Messenger driver cannot run: access not granted.

    This is B-125's state arriving at the delivery door. The
    notification service records it as the row's
    ``driver_unavailable`` messenger status — a fact on the
    record, never a failed notification."""


class MessengerTransportError(Exception):
    """A configured driver tried and failed (refused, timed
    out). Recorded as the row's "failed" messenger status;
    the notification's primary channels are unaffected."""


class MessengerTransport(Protocol):
    """What a Messenger driver is: one send to one linked
    Messenger identity, returning the platform's message id."""

    def send(self, *, messenger_ref: str, subject: str, body_text: str) -> str:
        ...


class UnavailableMessengerTransport:
    """This environment's driver: access was never granted.

    Every send raises MessengerUnavailable with B-125's own
    sentence — the refusal a member's record should carry,
    in words, not a code nobody can read."""

    MESSAGE = (
        "Messenger access has not been granted to this "
        "project, so no Messenger message can be sent — the "
        "email and in-app channels carry this notification."
    )

    def send(self, *, messenger_ref: str, subject: str, body_text: str) -> str:
        raise MessengerUnavailable(self.MESSAGE)


class CaptureMessengerTransport:
    """Test driver: remembers sends instead of delivering."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, *, messenger_ref: str, subject: str, body_text: str) -> str:
        self.sent.append(
            {"messenger_ref": messenger_ref, "subject": subject,
             "body_text": body_text}
        )
        return f"capture-msg-{len(self.sent)}"


class FailingMessengerTransport:
    """Test driver: a configured driver that always refuses."""

    def __init__(self, message: str = "messenger platform refused the send"):
        self._message = message
        self.attempts = 0

    def send(self, *, messenger_ref: str, subject: str, body_text: str) -> str:
        self.attempts += 1
        raise MessengerTransportError(self._message)


def build_transport(settings: Settings) -> MessengerTransport:
    """The configured driver. There is exactly one honest
    answer today — access is not granted, so the unavailable
    driver — and this function is where a granted future
    changes its mind."""
    return UnavailableMessengerTransport()


# The process registry (the mailer pattern). It STARTS as
# the unavailable driver rather than empty: "no driver" and
# "driver unavailable" are the same fact in this deployment,
# and the delivery door should record that fact, not trip
# over a missing registry entry.
_transport: MessengerTransport = UnavailableMessengerTransport()


def set_transport(transport: MessengerTransport | None) -> None:
    global _transport
    _transport = transport if transport is not None else UnavailableMessengerTransport()


def get_transport() -> MessengerTransport:
    return _transport
