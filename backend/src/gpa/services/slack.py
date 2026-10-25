"""The Slack client boundary (roadmap B-158).

Slack is the second wave of live sources, beside Discord:
a B-032 permission record first, then a poll through THIS
boundary and no other — a new DRIVER behind the B-123/B-127
framework, which did not change to admit it.

- The **protocol** is the live-chat shape the intake already
  speaks: fetch a channel's new messages since a cursor, in
  the hall's export shape — ``{"id", "sender_id",
  "sender_name", "text", "sent_at"}`` — so chunks land
  identically whatever the source. On real Slack a message's
  stable id is its ``ts`` timestamp and a speaker's stable id
  is their Slack user id; the live client a deployment wires
  maps those onto ``id`` / ``sender_id`` HERE, at the
  boundary, so nothing downstream learns Slack's spellings.
  The account-link law is unchanged: links key on
  ``sender_id`` — never on a display name.
- The **fixture double** serves recorded channels from data
  a test hands it. Its data is invented; it has never seen a
  real workspace.
- The **live client** is NOT built here: no Slack app or bot
  token exists in this environment and no workspace has been
  connected (connecting one is a deployment act with a
  person's grant). The process client is the *unavailable*
  client, refusing in plain words; a deployment wires its
  app behind this registry — a config act, not a code change
  in the domain.
"""

from typing import Protocol


class SlackUnavailable(Exception):
    """No live Slack connection exists in this deployment.

    Raised by the unavailable client; the sync door records
    it on the source's sync state in plain words.
    """


class SlackClient(Protocol):
    """What the assistant may ask Slack, and nothing more."""

    def ping(self) -> bool:
        """True when the connection answers."""
        ...

    def fetch_messages(
        self, channel_id: str, since_id: str = ""
    ) -> tuple[list[dict], str]:
        """New messages in one channel since ``since_id`` (the
        id of the last message taken; empty = from the start).
        Returns (messages, cursor) — the cursor is the id of
        the last message returned, or the incoming one when
        nothing is new."""
        ...


class UnavailableSlackClient:
    """The default process client: no app is connected.

    Every call refuses in plain words — the honest stand-in
    for a connection that does not exist, exactly as the
    other boundaries in this codebase behave.
    """

    MESSAGE = (
        "No live Slack connection is configured in this "
        "deployment — Slack sources run against recorded "
        "fixtures until a deployment connects an app with a "
        "person's grant."
    )

    def ping(self) -> bool:
        raise SlackUnavailable(self.MESSAGE)

    def fetch_messages(
        self, channel_id: str, since_id: str = ""
    ) -> tuple[list[dict], str]:
        raise SlackUnavailable(self.MESSAGE)


class FixtureSlackClient:
    """The recorded-fixture double (tests + offline demos).

    Constructed from recorded data — ``channels`` maps a
    channel id to its message list — and behaves the way the
    protocol says the real service behaves: cursors advance
    by message id, and an unknown cursor reads from the start
    (re-ingesting is safe; silently skipping history is not).
    Nothing here performs I/O of any kind.
    """

    def __init__(self, *, channels: dict[str, list[dict]] | None = None) -> None:
        self._channels = {
            channel_id: [dict(m) for m in messages]
            for channel_id, messages in (channels or {}).items()
        }

    def ping(self) -> bool:
        return True

    def fetch_messages(
        self, channel_id: str, since_id: str = ""
    ) -> tuple[list[dict], str]:
        messages = self._channels.get(channel_id, [])
        start = 0
        if since_id:
            for index, message in enumerate(messages):
                if message["id"] == since_id:
                    start = index + 1
                    break
            else:
                start = 0
        fresh = messages[start:]
        cursor = fresh[-1]["id"] if fresh else since_id
        return [dict(m) for m in fresh], cursor

    # -- the Slack-side act a test performs ---------------------

    def post_message(self, channel_id: str, message: dict) -> None:
        """A person speaks in the channel (fixture-side)."""
        self._channels.setdefault(channel_id, []).append(dict(message))


# --- The process registry (the samtal.py pattern) ---------------

_client: SlackClient = UnavailableSlackClient()


def set_client(client: SlackClient | None) -> None:
    """Install the process Slack client (None = unavailable)."""
    global _client
    _client = client if client is not None else UnavailableSlackClient()


def get_client() -> SlackClient:
    """The process Slack client."""
    return _client
