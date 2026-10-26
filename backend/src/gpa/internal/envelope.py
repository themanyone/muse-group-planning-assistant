"""The internal envelope — one message shape for every part (B-162).

§6A.4's surface, as data. Every cross-module operation travels
in this envelope whether the owner sits in the same process
(B-166's in-process profile) or across a transport (B-163):
``{operation, caller, scope, payload, nonce, timestamp,
signature}`` — the fields the roadmap names, in the order the
roadmap names them. The result rides back in the matching
shape, with errors in the same problem+json shape the external
API uses (errors.py), so a caller learns one error language for
the whole system.

Two honesty notes, in plain words:

* ``payload`` and ``data`` are plain JSON-shaped dicts. A call
  that cannot state its business in ids and values is not an
  internal-API call — ORM rows never ride the envelope, which
  is what makes the same call shape possible across a process
  boundary later.
* ``signature`` is empty in the plain local profile. B-163/B-164
  fill it (Ed25519, or HMAC-SHA256 under a shared-secret
  profile) for anything crossing a process boundary, and the
  verification pipeline rejects an unsigned message there. The
  field exists from the first day so the shape never changes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """The envelope timestamp format: ISO-8601 UTC, seconds
    precision — the same convention as the audit stream."""
    return datetime.now(timezone.utc).isoformat()


class InternalRequest(BaseModel):
    """One internal call, as it would appear on the wire."""

    operation: str = Field(description="Registered operation name, e.g. notify.request")
    caller: str = Field(description="Calling module's service identity name")
    scope: str = Field(description="The internal scope this call exercises")
    payload: dict = Field(default_factory=dict, description="Operation payload: ids and values, never ORM rows")
    nonce: str = Field(description="Single-use random token; replay protection keys on it (B-164)")
    timestamp: str = Field(description="ISO-8601 UTC moment the envelope was built")
    idempotency_key: str | None = Field(
        default=None,
        description="For operations that create things: a retried call with the same key is one act (D-15)",
    )
    signature: str | None = Field(
        default=None,
        description="Ed25519/HMAC signature over the canonical envelope (B-164); empty in the local profile",
    )


class InternalResult(BaseModel):
    """One internal answer: data on success, problem on failure."""

    ok: bool
    operation: str
    data: dict = Field(default_factory=dict, description="Result values: ids and plain facts, never ORM rows")
    error: dict | None = Field(
        default=None,
        description="problem+json shape {code, title, status, detail} when ok is false",
    )
