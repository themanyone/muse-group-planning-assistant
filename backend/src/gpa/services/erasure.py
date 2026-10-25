"""Group export + hard erasure (roadmap B-143).

The owner's two sovereign acts over a group:

- **Export** — a real ZIP archive a human can open: the
  group's record as JSON (by domain), its calendar as iCal,
  and its stored files as themselves. Secrets never travel:
  password/token/key hashes and sealed integration configs
  are withheld by column rule (the manifest names what was
  withheld and why); an integration exports the NAMES of its
  config keys, never their values.
- **Erase** — the group, hard-deleted: every row that belongs
  to it across every table, its queued jobs, its stored
  files, its feed tokens and integration secrets with them.
  People (user accounts) are NOT group content and are never
  erased. The audit log keeps exactly one row: the fact of
  the erasure (roadmap wording), with an empty detail.

Completeness is by construction, not by list-keeping. One
deletion plan serves both acts: it seeds every table that
carries a ``group_id`` and follows foreign keys to a
fixpoint, so it names exactly the rows that belong to the
group — including child rows that carry no group_id of their
own (a comment through its thread, a vote through its poll,
a seat's API keys through the seat). The export serialises
exactly the planned rows; erasure deletes exactly them. A
table added tomorrow joins both acts without anyone
remembering to update a list here.
"""

import io
import json
import re
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpa.db import Base
from gpa.models.groups import Group
from gpa.models.core import FileAsset
from gpa.models.sources import Flyer, Source
from gpa.services import audit
from gpa.services import ical as ical_service
from gpa.services import integrations as integration_service
from gpa.services import recurrence as recurrence_service
from gpa.services import settings_pack

# Columns whose values are secrets or one-way hashes. They
# are withheld from exports by NAME, everywhere, and named in
# the manifest so the withholding is visible, not silent.
SECRET_COLUMNS = frozenset(
    {"password_hash", "token_hash", "key_hash", "config_sealed"}
)


def _models_by_table() -> dict[str, type]:
    """Every mapped class, by table name (importing the models
    package first so the registry is complete)."""
    import gpa.models  # noqa: F401  (registry side effect)

    return {
        mapper.class_.__tablename__: mapper.class_
        for mapper in Base.registry.mappers
    }


def _serialise(row) -> dict:
    """One row as JSON-able data. Secret columns are withheld
    (see SECRET_COLUMNS); datetimes become ISO strings; bytes
    become a note of their size — an archive of records is
    not a second copy of anyone's blob columns."""
    out: dict = {}
    for column in row.__table__.columns:
        if column.name in SECRET_COLUMNS:
            continue
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, bytes):
            value = f"<{len(value)} bytes>"
        out[column.name] = value
    return out


# ------------------------------------------------------------------
# The one plan both acts share
# ------------------------------------------------------------------


def _deletion_plan(db: Session, group_id: str) -> dict[str, set[str]]:
    """Every row that belongs to the group, by table.

    Seeds: each table carrying group_id, plus the group row
    itself. Then foreign keys to a fixpoint: a row belongs to
    the group when it points at something that does (a comment
    through its thread, a vote through its poll, a seat's API
    keys through the seat). Tables keyed to PEOPLE (refresh
    tokens, one-time tokens) are never reached — users are not
    group content.
    """
    import gpa.models  # noqa: F401

    tables = {t.name: t for t in Base.metadata.sorted_tables}
    plan: dict[str, set[str]] = {"groups": {group_id}}
    for name, table in tables.items():
        if "group_id" in table.c:
            ids = {
                row[0]
                for row in db.execute(
                    select(table.c.id).where(table.c.group_id == group_id)
                )
            }
            if ids:
                plan[name] = ids
    changed = True
    while changed:
        changed = False
        for name, table in tables.items():
            for column in table.columns:
                for fk in column.foreign_keys:
                    parent = fk.column.table.name
                    if parent not in plan or not plan[parent]:
                        continue
                    found = {
                        row[0]
                        for row in db.execute(
                            select(table.c.id).where(
                                column.in_(sorted(plan[parent]))
                            )
                        )
                    }
                    new = found - plan.get(name, set())
                    if new:
                        plan.setdefault(name, set()).update(new)
                        changed = True
    return plan


def _by_ids(db: Session, model: type, ids: set[str]) -> list[dict]:
    if not ids:
        return []
    rows = db.query(model).filter(model.id.in_(sorted(ids))).all()
    return [_serialise(row) for row in rows]


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------

# Which export file claims which tables. Any planned table
# claimed by NO file lands in remaining.json — the catch-all
# that keeps the archive complete by construction.
_EXPORT_FILES: dict[str, tuple[str, ...]] = {
    "sources.json": ("sources", "source_chunks", "flyers", "file_assets"),
    "events.json": (
        "events", "recurrences", "event_changes", "event_source_links",
        "rsvps", "attendance_marks", "event_invites",
        "event_contradictions", "external_event_links",
        "announcement_drafts",
    ),
    "planning.json": (
        "tasks", "polls", "poll_options", "poll_votes", "decisions",
        "open_questions", "availability_slots", "availability_marks",
        "resources", "resource_bookings", "role_slots",
        "checklist_items", "cost_lines", "announcements",
        "subgroups", "subgroup_memberships", "templates",
    ),
    "discussion.json": ("threads", "comments"),
    "candidates.json": ("candidates", "candidate_approvals", "provenance"),
    "memory.json": ("summaries", "prune_runs", "ai_usage"),
    "notifications.json": (
        "notifications", "digests", "member_notification_settings",
        "notification_preferences",
    ),
    "integrations.json": (
        "integrations", "outbound_webhooks", "outbound_deliveries",
        "webhook_endpoints", "webhook_receipts", "calendar_feeds",
        "inbound_addresses", "external_account_links", "live_sync_states",
    ),
    "audit.json": ("audit_log",),
    "tombstones.json": ("tombstones",),
}


def build_export(
    db: Session, *, settings, storage, group: Group, actor_label: str
) -> tuple[bytes, str]:
    """Assemble the group's archive; returns (zip bytes,
    download filename). Records an audit row (an export is a
    sensitive read); the router's response commits it."""
    models = _models_by_table()
    plan = _deletion_plan(db, group.id)
    claimed: set[str] = {"groups"}
    files: dict[str, bytes] = {}

    def add_json(name: str, payload) -> None:
        files[name] = json.dumps(
            payload, indent=2, ensure_ascii=False, default=str
        ).encode("utf-8")

    add_json("group.json", {
        "group": _serialise(group),
        "settings": settings_pack.settings_view(group),
    })

    # The people of the group: memberships, the members' own
    # public identity (id, email, name — the archive is the
    # owner's record of their own group), seats, and the seats'
    # API keys (hashes withheld by the column rule).
    from gpa.models.identity import ApiKey, User

    memberships = _by_ids(db, models["memberships"], plan.get("memberships", set()))
    claimed.add("memberships")
    users_by_id = {}
    for row in memberships:
        user = db.get(User, row["user_id"])
        if user is not None:
            users_by_id[user.id] = {
                "id": user.id, "email": user.email, "name": user.name,
            }
    seat_keys = [
        row for row in _by_ids(db, ApiKey, plan.get("api_keys", set()))
    ]
    claimed.add("api_keys")
    add_json("members.json", {
        "memberships": memberships,
        "users": users_by_id,
        "seats": _by_ids(db, models["agent_seats"], plan.get("agent_seats", set())),
        "seat_api_keys": seat_keys,
    })
    claimed.add("agent_seats")

    # Integrations are hand-built: the config KEY NAMES travel,
    # the sealed values never do.
    integration_rows = []
    for row in db.query(models["integrations"]).filter(
        models["integrations"].id.in_(sorted(plan.get("integrations", set())))
    ):
        integration_rows.append({
            **_serialise(row),
            "config_keys": integration_service.config_keys(settings, row),
        })

    for filename, tables in _EXPORT_FILES.items():
        payload = {}
        for table in tables:
            if table == "integrations":
                payload[table] = integration_rows
            elif table in models:
                payload[table] = _by_ids(db, models[table], plan.get(table, set()))
            claimed.add(table)
        add_json(filename, payload)

    # Mail events name their notification by a plain column
    # (no FK), so the plan cannot reach them; they ride with
    # the notifications they describe.
    from gpa.models.notifications import MailEvent

    mail_events = []
    notification_ids = plan.get("notifications", set())
    if notification_ids:
        mail_events = [
            _serialise(row)
            for row in db.query(MailEvent).filter(
                MailEvent.notification_id.in_(sorted(notification_ids))
            )
        ]
    if mail_events:
        payload = json.loads(files["notifications.json"])
        payload["mail_events"] = mail_events
        add_json("notifications.json", payload)

    remaining = {
        table: _by_ids(db, models[table], ids)
        for table, ids in sorted(plan.items())
        if table not in claimed and table in models
    }
    if remaining:
        add_json("remaining.json", remaining)

    # The calendar, in the feed's own convention: published +
    # cancelled (drafts ride in events.json, as records).
    event_model = models["events"]
    events = (
        db.query(event_model)
        .filter(
            event_model.id.in_(sorted(plan.get("events", set()))),
            event_model.status.in_(("published", "cancelled")),
        )
        .order_by(event_model.starts_at)
        .all()
    )
    entries = [
        (event, recurrence_service.get_recurrence(db, event.id))
        for event in events
    ]
    files["events.ics"] = ical_service.render_calendar(
        entries, calendar_name=group.name
    ).encode("utf-8")

    # The stored files themselves, once per unique object,
    # named for the asset that describes them.
    file_entries: dict[str, str] = {}
    assets = (
        db.query(FileAsset).filter(FileAsset.group_id == group.id).all()
    )
    seen_keys: set[str] = set()
    for asset in assets:
        if asset.storage_key in seen_keys:
            continue
        seen_keys.add(asset.storage_key)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", asset.filename or "file")
        entry = f"files/{asset.id}-{safe_name}"
        files[entry] = storage.get_bytes(asset.storage_key)
        file_entries[asset.id] = entry

    audit.record(
        db, actor=actor_label, action="group.exported",
        group_id=group.id, entity_type="group", entity_id=group.id,
        detail={"files": len(files) + 1},
    )

    add_json("manifest.json", {
        "product": "Group Planning Assistant — group export (B-143)",
        "group_id": group.id,
        "group_name": group.name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": actor_label,
        "files": sorted(files) + ["manifest.json"],
        "file_entries": file_entries,
        "withheld": (
            "Password hashes, token hashes, API-key hashes and "
            "sealed integration configs are never exported. "
            "Integrations export their config key NAMES only "
            "(integrations.json). Queued background jobs are "
            "operational, not records, and are not exported."
        ),
    })

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    slug = re.sub(r"[^a-z0-9]+", "-", group.name.lower()).strip("-") or "group"
    return buffer.getvalue(), f"{slug}-export.zip"


# ------------------------------------------------------------------
# Erasure
# ------------------------------------------------------------------


def _deletion_order(plan: dict[str, set[str]]) -> list[str]:
    """Children before parents: a table is deleted once no
    other still-standing planned table references it. The
    decisions self-reference is handled by nulling first
    (erase_group), so excluding self-edges is sound."""
    import gpa.models  # noqa: F401

    tables = {t.name: t for t in Base.metadata.sorted_tables}
    remaining = set(plan)
    order: list[str] = []
    while remaining:
        referenced = {
            fk.column.table.name
            for name in remaining
            for column in tables[name].columns
            for fk in column.foreign_keys
            if fk.column.table.name in remaining
            and fk.column.table.name != name
        }
        ready = sorted(remaining - referenced)
        if not ready:  # a genuine cycle: fall back to name order
            ready = sorted(remaining)
        order.extend(ready)
        remaining -= set(ready)
    return order


def _snapshot_keys(payload: str) -> set[str]:
    """Storage keys named inside a tombstone's JSON snapshot."""
    found: set[str] = set()
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return found

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("storage_key", "thumbnail_key") and isinstance(
                    value, str
                ):
                    found.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def _storage_candidates(db: Session, group_id: str) -> set[str]:
    """Every storage object the group might own: its assets,
    its sources' objects, its flyers' thumbnails, and keys
    named inside its tombstone snapshots."""
    keys: set[str] = set()
    for (key,) in db.execute(
        select(FileAsset.storage_key).where(FileAsset.group_id == group_id)
    ):
        if key:
            keys.add(key)
    for (key,) in db.execute(
        select(Source.storage_key).where(Source.group_id == group_id)
    ):
        if key:
            keys.add(key)
    for (key,) in db.execute(
        select(Flyer.thumbnail_key).where(Flyer.group_id == group_id)
    ):
        if key:
            keys.add(key)
    from gpa.models.core import Tombstone

    for (payload,) in db.execute(
        select(Tombstone.snapshot_json).where(
            Tombstone.group_id == group_id
        )
    ):
        keys.update(_snapshot_keys(payload))
    return keys


def _referenced_keys(db: Session) -> set[str]:
    """Every storage key still referenced ANYWHERE — content
    addressing means another group's identical file shares
    the object, and erasure must never take a shared object
    down with one group."""
    keys: set[str] = set()
    for model, column in (
        (FileAsset, FileAsset.storage_key),
        (Source, Source.storage_key),
        (Flyer, Flyer.thumbnail_key),
    ):
        for (key,) in db.execute(select(column)):
            if key:
                keys.add(key)
    from gpa.models.core import Tombstone

    for (payload,) in db.execute(select(Tombstone.snapshot_json)):
        keys.update(_snapshot_keys(payload))
    return keys


def erase_group(
    db: Session,
    *,
    database,
    storage,
    group: Group,
    actor_label: str,
) -> dict:
    """Hard-erase one group. The caller (router) has already
    verified the owner + the double confirmation. Commits
    BEFORE answering (the house durability law), then sweeps
    storage, then compacts the database file so the bytes
    themselves are gone — the acceptance test reads the raw
    file.
    """
    group_id = group.id
    candidates = _storage_candidates(db, group_id)
    plan = _deletion_plan(db, group_id)
    models = _models_by_table()
    counts: dict[str, int] = {}

    # Decisions point at decisions; clear the planned rows'
    # self-references so the bulk delete cannot wedge.
    if plan.get("decisions"):
        (
            db.query(models["decisions"])
            .filter(models["decisions"].id.in_(sorted(plan["decisions"])))
            .update(
                {"supersedes_id": None, "superseded_by_id": None},
                synchronize_session=False,
            )
        )

    notification_ids = sorted(plan.get("notifications", set()))
    for name in _deletion_order(plan):
        if name == "notifications" and notification_ids:
            # Mail events name their notification by a plain
            # column (no FK): they are the group's rows too.
            from gpa.models.notifications import MailEvent

            counts["mail_events"] = (
                db.query(MailEvent)
                .filter(MailEvent.notification_id.in_(notification_ids))
                .delete(synchronize_session=False)
            )
        model = models[name]
        counts[name] = (
            db.query(model)
            .filter(model.id.in_(sorted(plan[name])))
            .delete(synchronize_session=False)
        )

    # Queued jobs name the group only inside their payload
    # JSON — a digest chain or extraction for a group that no
    # longer exists must never run.
    from gpa.models.core import Job

    counts["jobs"] = (
        db.query(Job)
        .filter(Job.payload_json.like(f"%{group_id}%"))
        .delete(synchronize_session=False)
    )

    # The audit keeps only the fact (B-143): every audit row
    # of the group went with the plan; this one row is written
    # after, with an empty detail.
    audit.record(
        db, actor=actor_label, action="group.erased",
        group_id=group_id, entity_type="group", entity_id=group_id,
        detail={},
    )
    db.commit()

    # Storage sweep: only objects nothing references any more.
    referenced = _referenced_keys(db)
    removed = 0
    for key in sorted(candidates - referenced):
        if storage.exists(key):
            storage.delete(key)
            removed += 1

    # End the request session's transaction BEFORE compacting.
    # The storage sweep above read through this session, and
    # an open transaction holds its locks: on PostgreSQL the
    # compact step's VACUUM (FULL) needs exclusive locks on
    # those very tables, on a separate connection — session
    # waits for the request, vacuum waits for the session,
    # and erasure hangs forever (found by the B-152 parity
    # run: pg_stat_activity showed the VACUUM blocked on the
    # session's own tombstone read). Everything this session
    # wrote is already committed (deletes, then the audit
    # row); this commit only lets the read transaction go.
    db.commit()

    # Compact: a deleted row's bytes linger past the DELETE —
    # in SQLite's free pages and WAL, in PostgreSQL's heap —
    # and erasure means the stored bytes themselves go, not
    # just the rows. The step is dialect-shaped (B-152):
    #
    # * SQLite: checkpoint, VACUUM, checkpoint again. The
    #   vacuum's own output travels through the WAL like any
    #   other write, and on newer SQLite builds the raw main
    #   file still shows the PRE-vacuum image until a
    #   checkpoint settles it (the B-146 restore drill caught
    #   exactly this: logically erased, raw bytes stale). The
    #   second checkpoint makes the file on disk BE the
    #   vacuumed image, WAL empty — the acceptance test reads
    #   that raw file.
    # * PostgreSQL: there is no single database file to read
    #   back, so the file-grep acceptance is SQLite's by
    #   nature. The honest equivalent here is VACUUM (FULL):
    #   it rewrites every table file, so heap pages that held
    #   the erased tuples are written fresh without them.
    connection = database.engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        if database.engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.exec_driver_sql("VACUUM")
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        elif database.engine.dialect.name == "postgresql":
            connection.exec_driver_sql("VACUUM (FULL)")
    finally:
        connection.close()

    return {
        "group_id": group_id,
        "erased": True,
        "deleted": {k: v for k, v in sorted(counts.items()) if v},
        "storage_objects_removed": removed,
    }
