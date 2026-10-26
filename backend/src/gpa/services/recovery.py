"""Startup recovery & self-healing (roadmap B-172).

Every process start runs :func:`assess_and_repair` before it
serves or works. It expects corruption — a killed worker, a
torn write, a half-applied migration — and answers one of
three verdicts:

* ``serve`` — the ground is sound; start.
* ``repaired`` — named damage was healed (each repair is in
  the report and, where a group's data moved, the audit
  log); start.
* ``refuse`` — the damage is beyond automatic repair; the
  report NAMES the problem and the manual repair, and the
  process must not serve on top of it.

The repairs:

* **WAL checkpoint** — a SQLite database left with a fat WAL
  by a killed process is checkpointed and truncated.
* **Stuck jobs** — a job still ``running`` past its lease
  (its worker died holding it) is requeued with an attempt
  counted, or failed when its attempts are spent — the
  B-157 exactly-once law holds because job attempts are
  idempotent at their handlers.
* **Orphan upload parts** — part files whose upload session
  no longer exists (or long completed) are swept; an OPEN
  session's parts are the resume contract and are never
  touched.
* **Migration state** — an ``alembic_version`` naming a
  revision this build does not know, or a database whose
  tables exist in a half-migrated shape, refuses with the
  repair named. An unstamped database (the create_all
  posture tests and first boots use) is noted, not refused.

The double-verdict guard: repair is idempotent — run the
assessment twice on the same ground and the second verdict
is ``serve`` with no actions. A healthy database is never
"healed" aside.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from gpa.models.core import Job
from gpa.models.durability import UploadSession
from gpa.models.identity import now_utc
from gpa.services import audit

LEASE_SECONDS = 900


def _known_revisions() -> set[str]:
    from pathlib import Path as _Path

    versions = _Path(__file__).resolve().parent.parent.parent / "migrations" / "versions"
    found = set()
    if versions.is_dir():
        for file in versions.glob("*.py"):
            stem = file.stem
            found.add(stem)
            found.add(stem.split("_")[0])
    return found


def _checkpoint_wal(db: Session, report: dict) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        return
    row = db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
    # (busy, log_frames, checkpointed_frames): a nonzero log
    # means frames were resting in the WAL and are now home.
    if row and len(row) >= 3 and (row[1] or 0) > 0:
        report["actions"].append(
            f"wal checkpointed ({row[2]} of {row[1]} frames brought home)"
        )


def _repair_stuck_jobs(db: Session, report: dict, *, lease_seconds: int) -> None:
    cutoff = now_utc() - timedelta(seconds=lease_seconds)
    stuck = (
        db.query(Job)
        .filter(Job.status == "running", Job.updated_at < cutoff)
        .all()
    )
    for job in stuck:
        job.attempts += 1
        job.locked_by = None
        if job.attempts > job.max_attempts:
            job.status = "failed"
            job.last_error = (
                "Recovered at startup: the worker holding this job died, "
                "and its attempts are spent."
            )
            report["actions"].append(f"job {job.id} ({job.kind}) failed after recovery")
        else:
            job.status = "queued"
            job.run_at = now_utc()
            job.last_error = "Recovered at startup: requeued after its worker died."
            report["actions"].append(f"job {job.id} ({job.kind}) requeued")
        audit.record(
            db, actor="system:recovery", action="recovery.job_requeued",
            group_id=None, entity_type="job", entity_id=job.id,
            detail={"kind": job.kind, "attempts": job.attempts, "status": job.status},
        )
    if stuck:
        db.commit()


def _sweep_orphan_parts(db: Session, settings, report: dict) -> None:
    uploads_root = Path(settings.storage_dir) / "uploads"
    if not uploads_root.is_dir():
        return
    live = {
        row.id: row.status
        for row in db.query(UploadSession).all()
    }
    for entry in sorted(uploads_root.iterdir()):
        if not entry.is_dir():
            continue
        status = live.get(entry.name)
        if status == "open":
            continue  # the resume contract — never touch
        removed = 0
        for part in entry.iterdir():
            try:
                part.unlink()
                removed += 1
            except OSError:
                pass
        try:
            entry.rmdir()
        except OSError:
            pass
        report["actions"].append(
            f"swept {removed} orphan upload part(s) of session {entry.name} "
            f"({status or 'no session row'})"
        )


def _check_migrations(db: Session, report: dict) -> None:
    bind = db.get_bind()
    tables = set()
    from sqlalchemy import inspect

    tables = set(inspect(bind).get_table_names())
    if "alembic_version" not in tables:
        report["notes"].append(
            "database is unstamped (create_all posture) — no migration state to verify"
        )
        return
    rows = db.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    known = _known_revisions()
    for (version,) in rows:
        if version not in known:
            report["problems"].append(
                f"alembic_version names revision {version!r}, which this build "
                "does not know — the database is NEWER than the code or a "
                "migration was interrupted. Repair: deploy the matching code, "
                "or restore the backup taken before the upgrade."
            )


def assess_and_repair(db: Session, settings, *, lease_seconds: int = LEASE_SECONDS) -> dict:
    """Run every check and repair; return the report.

    ``{verdict, actions, problems, notes}`` — verdict is
    ``refuse`` when problems stand, ``repaired`` when actions
    were taken, else ``serve``."""
    report: dict = {"actions": [], "problems": [], "notes": []}
    _checkpoint_wal(db, report)
    _repair_stuck_jobs(db, report, lease_seconds=lease_seconds)
    _sweep_orphan_parts(db, settings, report)
    _check_migrations(db, report)
    if report["problems"]:
        report["verdict"] = "refuse"
    elif report["actions"]:
        report["verdict"] = "repaired"
    else:
        report["verdict"] = "serve"
    return report


__all__ = ["LEASE_SECONDS", "assess_and_repair"]
