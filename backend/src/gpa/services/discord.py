"""The Discord client boundary (roadmap B-158).

Discord is the second wave of live sources: groups that talk
in a Discord channel get the same road in as the Samtal hall
(B-123) — a B-032 permission record first, then a poll through
THIS boundary and no other. The framework did not change to
admit Discord; this module is a new DRIVER behind it:

- The **protocol** is the live-chat shape the intake already
  speaks: fetch a channel's new messages since a cursor.
  Message shape is the hall's export shape —
  ``{"id", "sender_id", "sender_name", "text", "sent_at"}`` —
  so chunks land identically whatever the source (the B-123
  law: downstream cannot tell a Discord channel from a paste).
  ``sender_id`` is the speaker's stable Discord user id — the
  only thing an account link may key on.
- The **fixture double** serves recorded channels from data a
  test hands it. Its data is invented; it has never seen a
  real server.
- The **live client** is NOT built here: no Discord bot token
  exists in this environment and no server has been connected
  (connecting one is a deployment act with a person's grant,
  like every live leg in this project). The process client is
  the *unavailable* client, refusing in plain words; a
  deployment wires its bot behind this registry — a config
  act, not a code change in the domain.
"""

from typing import Protocol


class DiscordUnavailable(Exception):
    """No live Discord connection exists in this deployment.

    Raised by the unavailable client; the sync door records
    it on the source's sync state in plain words.
    """


class DiscordClient(Protocol):
    """What the assistant may ask Discord, and nothing more."""

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


class UnavailableDiscordClient:
    """The default process client: no bot is connected.

    Every call refuses in plain words — the honest stand-in
    for a connection that does not exist, exactly as the
    other boundaries in this codebase behave.
    """

    MESSAGE = (
        "No live Discord connection is configured in this "
        "deployment — Discord sources run against recorded "
        "fixtures until a deployment connects a bot with a "
        "person's grant."
    )

    def ping(self) -> bool:
        raise DiscordUnavailable(self.MESSAGE)

    def fetch_messages(
        self, channel_id: str, since_id: str = ""
    ) -> tuple[list[dict], str]:
        raise DiscordUnavailable(self.MESSAGE)


class FixtureDiscordClient:
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

    # -- the Discord-side act a test performs -------------------

    def post_message(self, channel_id: str, message: dict) -> None:
        """A person speaks in the channel (fixture-side)."""
        self._channels.setdefault(channel_id, []).append(dict(message))


# --- The process registry (the samtal.py pattern) ---------------

_client: DiscordClient = UnavailableDiscordClient()


def set_client(client: DiscordClient | None) -> None:
    """Install the process Discord client (None = unavailable)."""
    global _client
    _client = client if client is not None else UnavailableDiscordClient()


def get_client() -> DiscordClient:
    """The process Discord client."""
    return _client
