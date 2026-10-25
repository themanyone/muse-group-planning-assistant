"""The Samtal client boundary (roadmap B-123, B-124).

Samtal is this team's own chat village — a real, live system
of Volmarr's, running on his own hardware. This module is the
ONLY shape through which the Group Planning Assistant ever
speaks to it, and it is a boundary in the same sense as the
mail transport (B-095) and the AI adapter (B-035):

- The **protocol** below is all the domain code knows: fetch
  a hall's new messages since a cursor; read a calendar; put
  an event on a calendar. Wire format in, plain dicts out.
- The **fixture double** is the deliverable in this
  environment: it serves RECORDED hall transcripts and
  calendars from data a test hands it, and remembers what
  was published to it, so the round-trip (B-124) is exercised
  end to end. Its data is invented fixture data — it has
  never seen the real village.
- The **live client** is NOT built here and nothing in this
  codebase pretends otherwise: no code in this repository
  connects to, polls, or writes to the real Samtal. When a
  deployment wires one, it will implement this protocol
  behind the same registry — a config act, not a code change
  in the domain. Until then the process client is the
  *unavailable* client, whose every call refuses in plain
  words; the sync doors surface that refusal honestly
  instead of inventing a connection.

Message shape (the hall's export shape, and the fixture's):
``{"id", "sender_id", "sender_name", "text", "sent_at"}``
with ``sent_at`` an ISO-8601 string. ``sender_id`` is the
speaker's stable id in the hall — the only thing an account
link may key on (B-123's identity law). Calendar event shape:
``{"id", "title", "starts_at", "ends_at", "place"}`` with
ISO-8601 instants, plus ``"cancelled": true`` when the
hall-side copy was cancelled there.
"""

from typing import Protocol


class SamtalUnavailable(Exception):
    """No live Samtal connection exists in this deployment.

    Raised by the unavailable client; services translate it
    into a plain-words refusal at their door. It is a
    configuration fact, not a failure of the village.
    """


class SamtalClient(Protocol):
    """What the assistant may ask Samtal, and nothing more."""

    def ping(self) -> bool:
        """True when the connection answers."""
        ...

    def fetch_messages(self, hall_id: str, since_id: str = "") -> tuple[list[dict], str]:
        """New messages in one hall since ``since_id`` (the id
        of the last message taken; empty = from the start).
        Returns (messages, cursor) — the cursor is the id of
        the last message returned, or the incoming one when
        nothing is new."""
        ...

    def list_calendar_events(self, calendar_id: str) -> list[dict]:
        """The events currently on one Samtal calendar."""
        ...

    def upsert_calendar_event(self, calendar_id: str, payload: dict) -> str:
        """Create or replace one event on the calendar;
        returns the calendar-side id. ``payload`` carries the
        assistant's own event id as ``ref`` so repeat pushes
        update the same outside copy."""
        ...


class UnavailableSamtalClient:
    """The default process client: no live connection exists.

    Every call raises SamtalUnavailable with the same plain
    sentence. This is deliberate — the alternative (silently
    succeeding against nothing) would fake an integration
    the team has not wired, and faking is the one thing this
    project's boundaries forbid.
    """

    MESSAGE = (
        "No live Samtal connection is configured in this "
        "deployment — the bridge runs against recorded "
        "fixtures until a deployment wires the real village."
    )

    def ping(self) -> bool:
        raise SamtalUnavailable(self.MESSAGE)

    def fetch_messages(self, hall_id: str, since_id: str = "") -> tuple[list[dict], str]:
        raise SamtalUnavailable(self.MESSAGE)

    def list_calendar_events(self, calendar_id: str) -> list[dict]:
        raise SamtalUnavailable(self.MESSAGE)

    def upsert_calendar_event(self, calendar_id: str, payload: dict) -> str:
        raise SamtalUnavailable(self.MESSAGE)


class FixtureSamtalClient:
    """The recorded-fixture double (tests + offline demos).

    Constructed from recorded data — ``halls`` maps a hall id
    to its message list, ``calendars`` maps a calendar id to
    its event list — and behaves the way the protocol says
    the real village behaves: cursors advance by message id,
    calendar upserts key on the caller's ``ref``, and a
    Samtal-side edit is just a change to the recorded data
    (tests make one through :meth:`edit_calendar_event`, the
    way a person in the hall would). Nothing here performs
    I/O of any kind.
    """

    def __init__(
        self,
        *,
        halls: dict[str, list[dict]] | None = None,
        calendars: dict[str, list[dict]] | None = None,
    ) -> None:
        # Deep-enough copies: a test's fixture literals must
        # not be mutated by the double's calendar writes.
        self._halls = {
            hall_id: [dict(m) for m in messages]
            for hall_id, messages in (halls or {}).items()
        }
        self._calendars = {
            calendar_id: [dict(e) for e in events]
            for calendar_id, events in (calendars or {}).items()
        }
        self._counter = 0

    # -- the protocol ------------------------------------------------

    def ping(self) -> bool:
        return True

    def fetch_messages(self, hall_id: str, since_id: str = "") -> tuple[list[dict], str]:
        messages = self._halls.get(hall_id, [])
        start = 0
        if since_id:
            for index, message in enumerate(messages):
                if message["id"] == since_id:
                    start = index + 1
                    break
            else:
                # An unknown cursor reads from the start —
                # re-ingesting is safe (the sync dedupes by
                # nothing but position; a fresh source starts
                # empty), and silently skipping history is not.
                start = 0
        fresh = messages[start:]
        cursor = fresh[-1]["id"] if fresh else since_id
        return [dict(m) for m in fresh], cursor

    def list_calendar_events(self, calendar_id: str) -> list[dict]:
        return [dict(e) for e in self._calendars.get(calendar_id, [])]

    def upsert_calendar_event(self, calendar_id: str, payload: dict) -> str:
        events = self._calendars.setdefault(calendar_id, [])
        ref = payload.get("ref", "")
        for event in events:
            if event.get("ref") == ref and ref:
                event.update(
                    {
                        "title": payload.get("title", event.get("title", "")),
                        "starts_at": payload.get("starts_at", event.get("starts_at")),
                        "ends_at": payload.get("ends_at", event.get("ends_at")),
                        "place": payload.get("place", event.get("place", "")),
                        "cancelled": bool(payload.get("cancelled", False)),
                    }
                )
                return event["id"]
        self._counter += 1
        external_id = f"samtal-evt-{self._counter}"
        events.append(
            {
                "id": external_id,
                "ref": ref,
                "title": payload.get("title", ""),
                "starts_at": payload.get("starts_at"),
                "ends_at": payload.get("ends_at"),
                "place": payload.get("place", ""),
                "cancelled": bool(payload.get("cancelled", False)),
            }
        )
        return external_id

    # -- the Samtal-side acts a test performs -------------------------

    def post_message(self, hall_id: str, message: dict) -> None:
        """A person speaks in the hall (fixture-side)."""
        self._halls.setdefault(hall_id, []).append(dict(message))

    def edit_calendar_event(self, calendar_id: str, external_id: str, **fields) -> None:
        """A person edits the calendar in Samtal itself —
        the act B-124's return path must never silently
        absorb: the next sync drafts it as a change
        candidate instead."""
        for event in self._calendars.get(calendar_id, []):
            if event["id"] == external_id:
                event.update(fields)
                return
        raise KeyError(f"no fixture calendar event {external_id}")


# --- The process registry -------------------------------------------
# One client per process, exactly like the AI adapter and the
# mail transport: create_app leaves the unavailable client in
# place; tests and demos install the fixture double; a real
# deployment installs its live client here — and nowhere else
# needs to know.

_client: SamtalClient = UnavailableSamtalClient()


def set_client(client: SamtalClient | None) -> None:
    """Install the process Samtal client (None = unavailable)."""
    global _client
    _client = client if client is not None else UnavailableSamtalClient()


def get_client() -> SamtalClient:
    """The process Samtal client."""
    return _client
