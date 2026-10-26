"""Server-side draft autosave (roadmap B-171).

Candidates and events are edited in drafts; a browser crash
must never eat the words. An editor's client autosaves the
draft payload here as it changes:

* every save names the ``base_version`` it was written
  against;
* a save whose base is current replaces the payload and
  bumps the version;
* a save whose base is STALE (someone — or the same editor
  in another tab — saved since) never overwrites: it lands
  as a conflict copy beside the live draft, and the caller
  is answered 409 ``draft_conflict`` with both versions, so
  the human merges words instead of losing them.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from gpa.models.durability import DraftAutosave

TARGET_KINDS = ("candidate", "event")


def _live_row(
    db: Session, group_id: str, user_id: str, target_kind: str, target_id: str
) -> DraftAutosave | None:
    return (
        db.query(DraftAutosave)
        .filter(
            DraftAutosave.group_id == group_id,
            DraftAutosave.user_id == user_id,
            DraftAutosave.target_kind == target_kind,
            DraftAutosave.target_id == target_id,
            DraftAutosave.is_conflict == 0,
        )
        .one_or_none()
    )


def conflicts_for(
    db: Session, group_id: str, user_id: str, target_kind: str, target_id: str
) -> list[DraftAutosave]:
    return (
        db.query(DraftAutosave)
        .filter(
            DraftAutosave.group_id == group_id,
            DraftAutosave.user_id == user_id,
            DraftAutosave.target_kind == target_kind,
            DraftAutosave.target_id == target_id,
            DraftAutosave.is_conflict == 1,
        )
        .order_by(DraftAutosave.updated_at.desc())
        .all()
    )


def read(
    db: Session, *, group_id: str, user_id: str, target_kind: str, target_id: str
) -> dict | None:
    row = _live_row(db, group_id, user_id, target_kind, target_id)
    if row is None:
        return None
    return {
        "payload": json.loads(row.payload_json or "{}"),
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
        "conflicts": [
            {
                "payload": json.loads(c.payload_json or "{}"),
                "version": c.version,
                "updated_at": c.updated_at.isoformat(),
            }
            for c in conflicts_for(db, group_id, user_id, target_kind, target_id)
        ],
    }


def save(
    db: Session,
    *,
    group_id: str,
    user_id: str,
    target_kind: str,
    target_id: str,
    payload: dict,
    base_version: int,
) -> tuple[DraftAutosave, bool]:
    """Save one autosave. Returns (row, conflicted).

    On conflict the returned row is the CONFLICT COPY (the
    words are kept); the live row is untouched."""
    live = _live_row(db, group_id, user_id, target_kind, target_id)
    if live is not None and base_version == live.version:
        live.payload_json = json.dumps(payload)
        live.version += 1
        db.commit()
        db.refresh(live)
        return live, False
    if live is None and base_version == 0:
        row = DraftAutosave(
            group_id=group_id, user_id=user_id, target_kind=target_kind,
            target_id=target_id, payload_json=json.dumps(payload), version=1,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row, False
    # The stale save: keep the words as a conflict copy.
    copy = DraftAutosave(
        group_id=group_id, user_id=user_id, target_kind=target_kind,
        target_id=target_id, payload_json=json.dumps(payload),
        version=(live.version if live else 0) + 1, is_conflict=1,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return copy, True


__all__ = ["TARGET_KINDS", "conflicts_for", "read", "save"]
