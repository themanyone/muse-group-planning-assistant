"""Audit log writer (roadmap B-019).

One function, used by every service that changes something worth
remembering. ``record`` only adds the row — it never commits — so
the audit entry lands in the SAME transaction as the change it
describes: the two can never disagree about whether something
happened. Rows are append-only; nothing in the codebase updates or
deletes them.
"""

import json

from sqlalchemy.orm import Session

from gpa.models.core import AuditLog


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
