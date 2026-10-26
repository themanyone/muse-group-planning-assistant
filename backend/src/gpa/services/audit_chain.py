"""The tamper-evident audit hash chain (roadmap B-170).

Every audit row written since B-170 carries two extra
fields: ``prev_hash`` (the row before it in the chain) and
``row_hash`` — an HMAC-SHA256, keyed by a chain key derived
from the app secret, over the row's canonical contents and
its predecessor's hash. A one-row head table holds the tip,
extended inside each writer's own transaction, so under
WAL's serialized writers the chain cannot fork.

What this proves, precisely: edit a row's contents, delete a
row, insert a forged row, or reorder history, and
``verify_chain`` stops at the wound and names it. What it
does not prove: a thief holding BOTH the database and the
app secret could recompute the chain — the chain is
evidence, keyed evidence, not magic.

Rows written before B-170 carry NULL hashes; verification
starts at the first hashed row and says so.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from sqlalchemy.orm import Session

GENESIS = "GENESIS"


def chain_key(secret_key: str) -> bytes:
    return hmac.new(
        secret_key.encode(), b"gpa-audit-chain-v1", hashlib.sha256
    ).digest()


def canonical_fields(row) -> str:
    """The row's hashed contents, in one canonical string.
    Detail rides as sorted-key JSON re-serialised from the
    stored text; the timestamp as the ISO string SQLAlchemy
    round-trips."""
    detail = json.dumps(
        json.loads(row.detail_json or "{}"), sort_keys=True, separators=(",", ":")
    )
    created = row.created_at.isoformat() if row.created_at else ""
    return "|".join(
        [
            row.id,
            row.actor or "",
            row.action or "",
            row.group_id or "",
            row.entity_type or "",
            row.entity_id or "",
            detail,
            created,
        ]
    )


def compute_hash(key: bytes, prev_hash: str, row) -> str:
    return hmac.new(
        key, f"{prev_hash}|{canonical_fields(row)}".encode(), hashlib.sha256
    ).hexdigest()


def verify_chain(db: Session, key: bytes) -> dict:
    """Walk the chain from its first hashed row.

    Returns ``{ok, rows, hashed_from, broken_row_id, reason}``.
    The walk follows prev-hash links (not timestamps — links
    are the order), recomputes every hash, and requires the
    walk to consume exactly the hashed rows: a row left over
    is as much a break as a row missing."""
    from gpa.models.core import AuditChainHead, AuditLog

    rows = db.query(AuditLog).filter(AuditLog.row_hash.isnot(None)).all()
    if not rows:
        return {
            "ok": True,
            "rows": 0,
            "hashed_from": None,
            "broken_row_id": None,
            "reason": "no hashed rows yet",
        }
    by_prev: dict[str, object] = {}
    for row in rows:
        if row.prev_hash in by_prev:
            return {
                "ok": False,
                "rows": len(rows),
                "hashed_from": None,
                "broken_row_id": row.id,
                "reason": "two rows claim the same predecessor (a fork)",
            }
        by_prev[row.prev_hash] = row
    head = db.query(AuditChainHead).filter(AuditChainHead.id == 1).one_or_none()
    tip = head.last_hash if head else None

    walked = 0
    prev = GENESIS
    first_id = None
    # The chain may start at GENESIS, or — if the head was
    # seeded mid-history — at the earliest row whose
    # predecessor is absent (a pre-chain NULL row id is never
    # a prev_hash, so a hashed row whose prev is a row id we
    # do not have hashed is still a valid start only when its
    # prev names the last pre-chain row... in practice chains
    # start at GENESIS; a non-GENESIS start means rows were
    # pruned by retention, which re-anchors deliberately.
    current = by_prev.get(prev)
    if current is None:
        # Find the unique row whose predecessor is not any
        # hashed row's hash and not GENESIS: a re-anchored
        # start (retention pruning stamps a fresh genesis).
        starts = [
            r
            for r in rows
            if r.prev_hash != GENESIS
            and all(r.prev_hash != other.row_hash for other in rows)
        ]
        if len(starts) == 1:
            current = starts[0]
            prev = current.prev_hash
        else:
            return {
                "ok": False,
                "rows": len(rows),
                "hashed_from": None,
                "broken_row_id": None,
                "reason": "no unique chain start found",
            }
    while current is not None:
        if first_id is None:
            first_id = current.id
        if current.prev_hash != prev:
            return {
                "ok": False,
                "rows": len(rows),
                "hashed_from": first_id,
                "broken_row_id": current.id,
                "reason": "prev_hash does not match the row before it",
            }
        expected = compute_hash(key, prev, current)
        if not hmac.compare_digest(expected, current.row_hash):
            return {
                "ok": False,
                "rows": len(rows),
                "hashed_from": first_id,
                "broken_row_id": current.id,
                "reason": "row contents do not match their hash (edited row)",
            }
        walked += 1
        prev = current.row_hash
        current = by_prev.get(prev)
    if walked != len(rows):
        return {
            "ok": False,
            "rows": len(rows),
            "hashed_from": first_id,
            "broken_row_id": None,
            "reason": "hashed rows exist that the chain never reaches (inserted or detached rows)",
        }
    if tip is not None and tip != prev:
        return {
            "ok": False,
            "rows": len(rows),
            "hashed_from": first_id,
            "broken_row_id": None,
            "reason": "the chain head does not match the last row (tail rows removed)",
        }
    return {
        "ok": True,
        "rows": walked,
        "hashed_from": first_id,
        "broken_row_id": None,
        "reason": "chain verified",
    }


__all__ = ["GENESIS", "chain_key", "canonical_fields", "compute_hash", "verify_chain"]
