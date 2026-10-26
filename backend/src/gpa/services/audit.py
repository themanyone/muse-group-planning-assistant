"""Audit log writer (roadmap B-019; hash chain B-170).

One function, used by every service that changes something worth
remembering. ``record`` only adds the row — it never commits — so
the audit entry lands in the SAME transaction as the change it
describes: the two can never disagree about whether something
happened. Rows are append-only; nothing in the codebase updates
or deletes them.

Since B-170 each row also extends the tamper-evident hash
chain (gpa.services.audit_chain): record flushes (never
commits) so the row has its id and timestamp, hashes it
against the chain head, and advances the head — all inside
the caller's transaction, so a rolled-back change takes its
chain extension with it.
"""

import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gpa.models.core import AuditChainHead, AuditLog
from gpa.models.identity import now_utc

_chain_secret: str | None = None


def set_chain_secret(secret: str) -> None:
    """Composition wiring: the app secret the chain key
    derives from. Called by every composition root; bare
    service tests fall back to the settings default."""
    global _chain_secret
    _chain_secret = secret


def _chain_key() -> bytes:
    from gpa.services import audit_chain

    if _chain_secret is not None:
        return audit_chain.chain_key(_chain_secret)
    from gpa.config import get_settings

    return audit_chain.chain_key(get_settings().secret_key)


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    group_id: str | None = None,
    entity_type: str = "",
    entity_id: str = "",
    detail: dict | None = None,
) -> AuditLog:
    """Append one audit row inside the caller's transaction.

    ``actor`` is a label — "user:<id>" or "seat:<id>" — so a seat's
    actions are always attributed to the seat, never to the human
    who created it (B-018).
    """
    row = AuditLog(
        group_id=group_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail_json=json.dumps(detail or {}),
    )
    db.add(row)
    # B-170: extend the hash chain inside this transaction.
    # The flush materialises the row's id + created_at (the
    # hashed contents); it is NOT a commit — the change and
    # its audit entry still land or roll back together.
    from gpa.services import audit_chain

    db.flush()
    # Chain extension, made race-safe at the Phase 17 gate.
    # The head is ONE row (id=1) and extending is a dance —
    # read prev, hash against it, advance the head. Two
    # writers dancing at once on PostgreSQL (READ COMMITTED)
    # could both read "no head yet" and both insert id=1, and
    # the loser died on the primary key; or both hash against
    # the same prev and silently FORK the chain (the verifier
    # names that wound, but a fork prevented beats a fork
    # detected). SQLite's database write lock had always
    # serialised the dance, so only the PG gate suite ever
    # saw it: B-003's concurrent-writers acceptance failed
    # about half its PostgreSQL runs on the head's primary
    # key. The fix has two halves:
    #   * the head is read FOR UPDATE, which serialises
    #     extenders on PostgreSQL — the second reader waits
    #     for the first's commit and then sees the TRUE prev.
    #     (SQLite renders FOR UPDATE as a no-op; its own lock
    #     already serialises, so nothing changes there.)
    #   * a missing head is created inside a nested
    #     transaction, so losing the creation race is a
    #     catchable IntegrityError — never a poisoned outer
    #     transaction — and the loop simply re-reads the
    #     winner's head and chains this row onto it instead.
    for _attempt in range(3):
        head = (
            db.query(AuditChainHead)
            .filter(AuditChainHead.id == 1)
            .with_for_update()
            .one_or_none()
        )
        prev = head.last_hash if head is not None else audit_chain.GENESIS
        row.prev_hash = prev
        row.row_hash = audit_chain.compute_hash(_chain_key(), prev, row)
        try:
            with db.begin_nested():
                if head is None:
                    db.add(
                        AuditChainHead(
                            id=1,
                            last_hash=row.row_hash,
                            updated_at=now_utc(),
                        )
                    )
                else:
                    head.last_hash = row.row_hash
                    head.updated_at = now_utc()
                db.flush()
        except IntegrityError:
            continue  # lost the creation race; re-chain and retry
        break
    else:
        raise RuntimeError("audit chain head contention: lost 3 races")
    db.flush()
    return row


def list_for_group(db: Session, group_id: str, limit: int = 100) -> list[AuditLog]:
    """Newest-first audit rows for one group (admin view, B-019)."""
    return (
        db.query(AuditLog)
        .filter(AuditLog.group_id == group_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
