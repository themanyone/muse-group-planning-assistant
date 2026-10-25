"""Live-source intake + identity links (roadmap B-123, B-125).

B-032 built the permission record: a ``chat_live`` source a
person registered against a connector and a conversation.
This module is the receiver that finally exercises it:

- **Intake.** :func:`sync_live_source` polls the connector
  for the messages past the source's stored cursor and
  appends them as chunks, in order — the same chunk shape a
  paste produces, so downstream (extraction, memory) cannot
  tell a hall from a paste, and the acceptance test holds
  the two to identical output on the same words.
- **Identity.** A hall speaker becomes a group member ONLY
  through an :class:`ExternalAccountLink` a person created,
  keyed by the speaker's stable id in the outside system
  (carried on each chunk as ``sender_ref``). The speaker's
  display name is never matched against member names — not
  as a shortcut, not as a fallback. An unlinked speaker is
  a *named guest*: their words are kept, under their name,
  and they are merged with nobody. The speakers view below
  is where that law is observable, and the tests pin it:
  a guest whose name is letter-for-letter a member's name
  still resolves to no member.

Connector dispatch is by the source's ``connector_kind``:
``samtal`` speaks through services/samtal.py's client
boundary; ``messenger`` answers with B-125's honest refusal
(the platform never granted this project live access — the
fallback is paste/export ingest, and the door says so).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.identity import User
from gpa.models.integrations import ExternalAccountLink, LiveSyncState
from gpa.models.sources import Source, SourceChunk
from gpa.services import audit
from gpa.services import sources as source_service


# ------------------------------------------------------------------
# Account links: the only road from a speaker to a member
# ------------------------------------------------------------------


def create_link(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    connector_kind: str,
    external_user_id: str,
    external_name: str,
    user_id: str,
) -> ExternalAccountLink:
    """Link one outside identity to one member of this group.

    The member must be an ACTIVE member — a link pointing at
    a stranger would be a silent membership grant, and this
    service makes no memberships. One outside identity links
    to exactly one member per group; re-linking is a conflict
    a person resolves by deleting the old link first.
    """
    from gpa.services.groups import active_membership

    if active_membership(db, user_id, group.id) is None:
        raise AppError(
            "validation_error",
            detail="Account links can only point at an active member of this group.",
        )
    existing = (
        db.query(ExternalAccountLink)
        .filter(
            ExternalAccountLink.group_id == group.id,
            ExternalAccountLink.connector_kind == connector_kind,
            ExternalAccountLink.external_user_id == external_user_id,
        )
        .one_or_none()
    )
    if existing is not None:
        raise AppError(
            "conflict",
            detail="That outside identity is already linked to a member of this group.",
        )
    link = ExternalAccountLink(
        group_id=group.id,
        connector_kind=connector_kind,
        external_user_id=external_user_id,
        external_name=external_name,
        user_id=user_id,
        created_by=actor_label,
    )
    db.add(link)
    audit.record(
        db, actor=actor_label, action="account_link.created",
        group_id=group.id, entity_type="external_account_link",
        entity_id=link.id,
        detail={
            "connector_kind": connector_kind,
            "external_user_id": external_user_id,
            "user_id": user_id,
        },
    )
    db.commit()
    return link


def list_links(db: Session, group: Group) -> list[ExternalAccountLink]:
    """Every account link in the group, oldest first."""
    return (
        db.query(ExternalAccountLink)
        .filter(ExternalAccountLink.group_id == group.id)
        .order_by(ExternalAccountLink.created_at, ExternalAccountLink.id)
        .all()
    )


def delete_link(
    db: Session, *, actor_label: str, group: Group, link_id: str
) -> None:
    """Remove one link. The speaker's chunks stay; from the
    next speakers view they are a guest again — unlinking
    rewrites no history, it stops resolving it."""
    link = db.get(ExternalAccountLink, link_id)
    if link is None or link.group_id != group.id:
        raise AppError("not_found", detail="That account link could not be found.")
    db.delete(link)
    audit.record(
        db, actor=actor_label, action="account_link.deleted",
        group_id=group.id, entity_type="external_account_link",
        entity_id=link_id,
        detail={"connector_kind": link.connector_kind,
                "external_user_id": link.external_user_id},
    )
    db.commit()


def resolve_link(
    db: Session, group: Group, connector_kind: str, external_user_id: str | None
) -> ExternalAccountLink | None:
    """The link for one outside identity, or None.

    Resolution is BY ID ONLY. There is deliberately no
    name-shaped parameter on this function — a caller cannot
    ask "who is the member called Ana?" because that question
    is the name-guessing this phase forbids.
    """
    if not external_user_id:
        return None
    return (
        db.query(ExternalAccountLink)
        .filter(
            ExternalAccountLink.group_id == group.id,
            ExternalAccountLink.connector_kind == connector_kind,
            ExternalAccountLink.external_user_id == external_user_id,
        )
        .one_or_none()
    )


def speakers_of(db: Session, group: Group, source: Source) -> list[dict]:
    """One source's speakers, resolved — the identity law
    made visible: each distinct (speaker id, name) with the
    member it links to, or ``member: None`` and
    ``guest: True`` when no link exists. Two speakers who
    share a display name appear as two rows whenever their
    ids differ."""
    seen: dict[tuple[str, str], dict] = {}
    for chunk in source_service.list_chunks(db, source):
        key = (chunk.sender_ref or "", chunk.sender or "")
        if key not in seen:
            link = resolve_link(
                db, group, source.connector_kind, chunk.sender_ref
            )
            member = None
            if link is not None:
                user = db.get(User, link.user_id)
                member = {
                    "user_id": link.user_id,
                    "name": user.name if user is not None else "",
                }
            seen[key] = {
                "external_id": chunk.sender_ref,
                "name": chunk.sender,
                "member": member,
                "guest": member is None,
            }
    return list(seen.values())


# ------------------------------------------------------------------
# Intake: poll the connector, append what is new
# ------------------------------------------------------------------


def _parse_instant(raw: str | None) -> datetime | None:
    """An ISO-8601 instant from the connector, as naive UTC —
    the codebase's one datetime convention (D-21)."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def sync_state(db: Session, group: Group, source: Source) -> LiveSyncState:
    """The source's sync state row, created on first use."""
    state = (
        db.query(LiveSyncState)
        .filter(LiveSyncState.source_id == source.id)
        .one_or_none()
    )
    if state is None:
        state = LiveSyncState(source_id=source.id, group_id=group.id)
        db.add(state)
        db.flush()
    return state


def sync_live_source(
    db: Session, *, actor_label: str, group: Group, source: Source
) -> dict:
    """Poll one live source's connector and append new chunks.

    Returns ``{"added": n, "cursor": str}``. A connector that
    cannot answer (no live Samtal configured; Messenger's
    ungranted access) refuses in plain words and records the
    refusal on the sync state — a failed sync is visible on
    the source, never silent. Nothing is appended by a failed
    sync: the cursor only ever advances past messages that
    actually landed as chunks.
    """
    if source.kind != "chat_live":
        raise AppError(
            "validation_error",
            detail="Only a live-chat source can be synced.",
        )
    state = sync_state(db, group, source)
    try:
        messages, cursor = _fetch_new(source, state.cursor)
    except AppError as err:
        state.last_error = err.detail
        db.commit()
        raise
    chunks = [
        {
            "text": str(message.get("text", "")),
            "sender": message.get("sender_name"),
            "sender_ref": message.get("sender_id"),
            "sent_at": _parse_instant(message.get("sent_at")),
            "timestamp_text": message.get("sent_at"),
        }
        for message in messages
    ]
    added = source_service.append_chunks(db, source=source, chunks=chunks)
    state.cursor = cursor
    state.messages_seen += added
    from gpa.models.identity import now_utc

    state.last_synced_at = now_utc()
    state.last_error = ""
    if added:
        # The source is alive now: material has flowed through
        # the permission record B-032 made.
        source.status = "live"
        source.stage = "live"
    audit.record(
        db, actor=actor_label, action="live.synced",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"added": added, "connector_kind": source.connector_kind},
    )
    db.commit()
    return {"added": added, "cursor": state.cursor}


def _fetch_new(source: Source, cursor: str) -> tuple[list[dict], str]:
    """One connector's new messages, by kind — the dispatch
    B-125's honesty lives in: each kind answers for its own
    access, in its own words."""
    if source.connector_kind == "samtal":
        from gpa.services import samtal as samtal_service

        try:
            return samtal_service.get_client().fetch_messages(
                source.external_id, cursor
            )
        except samtal_service.SamtalUnavailable as err:
            raise AppError("validation_error", detail=str(err)) from err
    if source.connector_kind == "messenger":
        from gpa.services import messenger as messenger_service

        return messenger_service.fetch_messages(source, cursor)
    raise AppError(
        "validation_error",
        detail=(
            f"No connector named '{source.connector_kind}' can be "
            "synced — this source's registration names a connector "
            "this build does not carry."
        ),
    )
