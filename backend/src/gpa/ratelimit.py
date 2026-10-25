"""Rate limiting, v1 (roadmap B-021).

What this file is for: a small in-process token bucket that guards
the auth routes. Each key (an IP, or IP+account) holds a bucket of
attempts that refills steadily; an empty bucket means "wait", and
the caller gets a 429 in the standard problem+json shape with a
``Retry-After`` header.

In-process and in-memory is a deliberate v1 choice: the hackathon
deployment is one process, and the interface (allow(key)) is what a
shared store would replace later without touching the routes.
"""

import time

from fastapi import Request

from gpa.errors import AppError


class TokenBucketLimiter:
    """Fixed-capacity token buckets, one per key, refilled per minute."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        # key -> (tokens remaining, last refill timestamp)
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> None:
        """Consume one attempt for ``key`` or raise a 429 AppError."""
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (float(self.per_minute), now))
        # Refill proportionally to elapsed time (per_minute per 60s).
        tokens = min(float(self.per_minute), tokens + (now - last) * self.per_minute / 60.0)
        if tokens < 1.0:
            # Whole seconds until at least one token is back.
            retry_after = max(1, int((1.0 - tokens) * 60.0 / self.per_minute) + 1)
            raise AppError(
                "rate_limited",
                detail="Too many attempts — please wait a little and try again.",
                headers={"Retry-After": str(retry_after)},
            )
        self._buckets[key] = (tokens - 1.0, now)


def auth_rate_limit(request: Request) -> None:
    """FastAPI dependency: rate-limit the caller of an auth route.

    Keyed by client IP; the limiter itself lives on the app instance
    so each app (and each test) gets its own buckets.
    """
    limiter: TokenBucketLimiter = request.app.state.auth_limiter
    client_ip = request.client.host if request.client else "unknown"
    limiter.allow(f"auth:{client_ip}")
