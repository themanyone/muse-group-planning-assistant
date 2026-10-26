"""Replay protection (roadmap §6A.4, B-164).

A signed message is genuine — but a genuine message SENT
TWICE is a replay, and signature math alone cannot tell the
original from the copy. Every internal message therefore
carries a timestamp and a single-use nonce, and the door
keeps a seen-nonce store:

* a message whose timestamp sits outside the window
  (5 minutes, either direction — clock skew is not a licence
  to reuse) is refused;
* a (signing key, nonce) pair seen before is refused;
* both refusals are ``internal_replayed``.

The store is per door process, in memory, pruned by expiry:
entries only need to live as long as the window, because a
message older than the window is refused by its timestamp
alone. Honest bound, recorded deliberately: a door RESTART
clears the store, so a message captured within the window
could be replayed once across that restart — the residual
closes when a persistent nonce table lands with the
durability work; the window keeps it small and brief either
way.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from gpa.errors import AppError

# The roadmap's window: five minutes.
DEFAULT_WINDOW_SECONDS = 300


class ReplayGuard:
    """The seen-nonce store + window check for one door."""

    def __init__(
        self,
        *,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        clock=time.time,
    ) -> None:
        self.window_seconds = window_seconds
        self._clock = clock
        # (key id, nonce) -> the epoch after which the entry
        # may be forgotten (message time + window).
        self._seen: dict[tuple[str, str], float] = {}

    def check(self, *, key_id: str, nonce: str, timestamp: str) -> None:
        """Refuse a replayed or out-of-window message.

        ``internal_replayed`` either way: from the caller's
        side both are the same fact — this exact message has
        had its one life."""
        now = self._clock()
        self._prune(now)
        try:
            moment = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise AppError(
                "internal_replayed",
                detail="An internal message carried no readable timestamp.",
            ) from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        epoch = moment.timestamp()
        if abs(now - epoch) > self.window_seconds:
            raise AppError(
                "internal_replayed",
                detail="An internal message arrived outside its replay window.",
            )
        pair = (key_id, nonce)
        if pair in self._seen:
            raise AppError(
                "internal_replayed",
                detail="An internal message's nonce was already seen.",
            )
        self._seen[pair] = epoch + self.window_seconds

    def _prune(self, now: float) -> None:
        expired = [pair for pair, expiry in self._seen.items() if expiry < now]
        for pair in expired:
            del self._seen[pair]


__all__ = ["DEFAULT_WINDOW_SECONDS", "ReplayGuard"]
