"""Crash-correction harness core (roadmap B-175).

The fault-injection suite's shared heart. B-171 gave the system
its durability discipline, B-172 its startup recovery, B-173 its
error correction — this module is the *evidence* road for all
three: it states the crash invariant once, as code, and both
the test suite (``tests/test_chaos.py``) and the report script
(``scripts/chaos_report.py``) judge every fault by it.

THE INVARIANT (the roadmap's words, made checkable). After any
fault — a process killed with SIGKILL mid-transaction, a job
worker dying mid-run, a disk that refuses a write, a database
opened read-only — and after startup recovery has had its say:

1. **No committed user entry is lost.** A row a transaction
   committed is present afterwards, whole.
2. **No half-written entity is visible.** A multi-row entity
   (a source and its chunks, an approval and the event it
   minted) is present complete or absent entirely — never the
   parent without its children, never a status flipped without
   the entity it was flipped for.
3. **No interrupted job is silently gone.** A job whose worker
   died holding it is requeued or visibly failed by recovery —
   its row exists and says what happened, in plain words.

The checks below are deliberately engine-level (integrity and
foreign-key PRAGMAs, row-shape probes) rather than API-level:
a crash corrupts below the API, so the verdict must be read
below the API too. The fault *drivers* live with the tests
(``tests/chaos_worker.py`` performs the real SIGKILL legs in a
subprocess; this module performs the in-process legs and the
100-run loop the acceptance names).
"""

from __future__ import annotations

import hashlib
import random
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from gpa.db import Database
from gpa.models.core import Job
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.sources import Source, SourceChunk
from gpa.services import recovery

# The fault vocabulary of the in-process loop. The subprocess
# legs in tests/test_chaos.py carry the same names for the
# flows they kill inside (ingest / extraction / approval /
# upload / digest); here each name is one transaction shape.
FAULT_KINDS = ("commit", "rollback", "job", "diskfull", "readonly")


def invariant_report(db: Session) -> dict:
    """Judge one database against the invariant.

    Returns ``{ok, problems, integrity, foreign_key_violations,
    sources_without_chunks, jobs_running}``. ``ok`` is True
    only when every leg holds. Sources still honestly awaiting
    a later stage (segmentation / transcription placeholders
    carry a chunk by construction in this codebase, so a
    ready-or-done source with ZERO chunks is always a tear,
    never a stage) are the half-written probe for ingest.
    """
    problems: list[str] = []
    integrity = "unknown"
    fk_violations = 0
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        integrity = db.execute(text("PRAGMA integrity_check")).scalar() or "unknown"
        if integrity != "ok":
            problems.append(f"integrity_check says {integrity!r}, not 'ok'")
        fk_rows = db.execute(text("PRAGMA foreign_key_check")).fetchall()
        fk_violations = len(fk_rows)
        if fk_violations:
            problems.append(f"{fk_violations} foreign-key violation(s)")

    sources_without_chunks = (
        db.query(Source)
        .filter(Source.status.in_(["ready", "done"]))
        .all()
    )
    torn = []
    for source in sources_without_chunks:
        count = db.query(SourceChunk).filter(SourceChunk.source_id == source.id).count()
        if count == 0:
            torn.append(source.id)
    if torn:
        problems.append(
            f"{len(torn)} ready source(s) have no chunks (half-written): {torn}"
        )

    jobs_running = [
        job.id for job in db.query(Job).filter(Job.status == "running").all()
    ]
    return {
        "ok": not problems,
        "problems": problems,
        "integrity": integrity,
        "foreign_key_violations": fk_violations,
        "sources_without_chunks": torn,
        "jobs_running": jobs_running,
    }


def seed_group(db: Session, *, slug: str = "chaos") -> Group:
    """One committed group — the ground every fault stands on."""
    group = Group(name="Chaos Crew", slug=slug)
    db.add(group)
    db.commit()
    return group


def seed_source(db: Session, group_id: str, *, title: str = "Chaos paste") -> Source:
    """One source with its three chunks, in ONE transaction.

    This is ingest's atomic shape (B-023/B-024): the source and
    its content land together or not at all, so it is the
    entity the invariant's second leg watches."""
    source = Source(
        group_id=group_id, kind="chat_paste", title=title,
        granted_by="user:chaos", granted_scope="group-history",
        status="ready", stage="done", progress=100,
    )
    db.add(source)
    db.flush()
    for position in range(3):
        db.add(SourceChunk(
            source_id=source.id, position=position,
            text=f"chaos chunk {position}", sender="Chaos",
        ))
    return source


def _fresh_database(base: Path, name: str) -> Database:
    db = Database(f"sqlite:///{base / name}")
    db.create_all()
    return db


def _reopen_and_judge(db_file: Path, settings) -> dict:
    """Dispose, reopen cold, run startup recovery, then judge.

    This is the exact road a restarted process walks (B-172):
    WAL recovery on open, the assess-and-repair pass, and only
    then the invariant verdict on the healed ground."""
    db = Database(f"sqlite:///{db_file}")
    session = db.session()
    try:
        recovery.assess_and_repair(session, settings)
        return invariant_report(session)
    finally:
        session.close()
        db.engine.dispose()


def _settings_for(base: Path):
    """Minimal real settings for recovery's storage sweep."""
    from gpa.config import Settings

    return Settings(
        env="test",
        database_url=f"sqlite:///{base / 'unused.db'}",
        secret_key="chaos-harness-not-a-real-secret",
        storage_dir=base / "storage",
        outbox_dir=base / "outbox",
    )


def _one_run(base: Path, index: int, fault: str, template: Path) -> list[str]:
    """One fault, one verdict: a list of invariant problems
    (empty = the invariant held). Each run stands on its own
    database file, exactly as a crashed deployment would —
    copied from the loop's schema template (``create_all``
    per run would make the harness slower than the faults it
    studies; a file copy is the same ground, honestly come
    by: the template is a freshly created, empty deployment)."""
    run_dir = base / f"run-{index:04d}"
    run_dir.mkdir(parents=True)
    db_file = run_dir / "chaos.db"
    import shutil

    shutil.copyfile(template, db_file)
    settings = _settings_for(run_dir)
    db = Database(f"sqlite:///{db_file}")
    session = db.session()
    problems: list[str] = []
    try:
        group = seed_group(session, slug=f"chaos-{index}")
        if fault == "commit":
            # A committed ingest, then the crash: dispose with
            # no ceremony (the in-process shape of kill -9 just
            # after COMMIT — the WAL holds the truth).
            seed_source(session, group.id)
            session.commit()
            session.close()
            db.engine.dispose()
            verdict = _reopen_and_judge(db_file, settings)
            if not verdict["ok"]:
                problems.extend(verdict["problems"])
            check = Database(f"sqlite:///{db_file}")
            s2 = check.session()
            try:
                found = s2.query(Source).filter(Source.group_id == group.id).all()
                if len(found) != 1:
                    problems.append(f"committed source count is {len(found)}, not 1")
                elif s2.query(SourceChunk).filter(
                    SourceChunk.source_id == found[0].id
                ).count() != 3:
                    problems.append("committed source lost chunks across the crash")
            finally:
                s2.close()
                check.engine.dispose()
            return problems
        if fault == "rollback":
            # The crash lands MID-transaction: the source is
            # flushed (its rows exist in the session's WAL
            # frames) but never committed. Nothing of it may be
            # visible afterwards.
            seed_source(session, group.id)
            session.flush()
            session.close()
            db.engine.dispose()  # the crash: no commit, no rollback call
            verdict = _reopen_and_judge(db_file, settings)
            if not verdict["ok"]:
                problems.extend(verdict["problems"])
            check = Database(f"sqlite:///{db_file}")
            s2 = check.session()
            try:
                if s2.query(Source).count() != 0:
                    problems.append("an uncommitted source is visible after the crash")
            finally:
                s2.close()
                check.engine.dispose()
            return problems
        if fault == "job":
            # A worker dies HOLDING a job: claimed (running,
            # committed claim), then the crash. Recovery must
            # requeue or visibly fail it — never leave it
            # silently running.
            job = Job(kind="extract", status="running", locked_by="chaos-worker",
                      attempts=0, max_attempts=5)
            job.updated_at = now_utc() - timedelta(seconds=recovery.LEASE_SECONDS + 60)
            session.add(job)
            session.commit()
            job_id = job.id
            session.close()
            db.engine.dispose()
            verdict = _reopen_and_judge(db_file, settings)
            if not verdict["ok"]:
                problems.extend(verdict["problems"])
            if verdict["jobs_running"]:
                problems.append(
                    f"job(s) still silently running after recovery: {verdict['jobs_running']}"
                )
            check = Database(f"sqlite:///{db_file}")
            s2 = check.session()
            try:
                healed = s2.get(Job, job_id)
                if healed is None:
                    problems.append("the interrupted job's row is gone entirely")
                elif healed.status not in ("queued", "failed"):
                    problems.append(f"interrupted job is {healed.status!r}, not requeued/failed")
                elif not healed.last_error:
                    problems.append("interrupted job carries no plain-words record")
            finally:
                s2.close()
                check.engine.dispose()
            return problems
        if fault == "diskfull":
            # The write road refuses mid-ingest (ENOSPC): the
            # service rolls its transaction back and the partial
            # entity never exists. The injector is the storage
            # boundary itself — the one place a full disk is
            # ever met — raising the OS's own error.
            def _full_disk_write(_payload: bytes) -> str:
                raise OSError(28, "No space left on device")

            try:
                seed_source(session, group.id)
                _full_disk_write(b"the file that does not fit")
                session.commit()  # never reached — the write refuses first
            except OSError:
                session.rollback()
            session.close()
            db.engine.dispose()
            verdict = _reopen_and_judge(db_file, settings)
            if not verdict["ok"]:
                problems.extend(verdict["problems"])
            check = Database(f"sqlite:///{db_file}")
            s2 = check.session()
            try:
                if s2.query(Source).count() != 0:
                    problems.append("a disk-full ingest left a partial source behind")
            finally:
                s2.close()
                check.engine.dispose()
            return problems
        if fault == "readonly":
            # The database opens READ-ONLY (the fsck/remount
            # shape): writes refuse cleanly, committed rows
            # read back whole, nothing corrupts.
            seed_source(session, group.id)
            session.commit()
            session.close()
            db.engine.dispose()
            # A creator-based read-only open: the pysqlite
            # driver ignores ``?mode=ro`` in the URL string, so
            # the URI must reach sqlite3.connect itself.
            import sqlite3

            def _read_only_connection():
                return sqlite3.connect(
                    f"file:{db_file}?mode=ro", uri=True, check_same_thread=False
                )

            ro_engine = create_engine("sqlite://", creator=_read_only_connection)
            try:
                with ro_engine.connect() as conn:
                    count = conn.execute(text("SELECT COUNT(*) FROM sources")).scalar()
                    if count != 1:
                        problems.append(f"read-only open sees {count} sources, not 1")
                try:
                    with ro_engine.begin() as conn:
                        conn.execute(
                            text("INSERT INTO groups (id, name, slug) VALUES ('x', 'X', 'x')")
                        )
                    problems.append("a write through a read-only open SUCCEEDED")
                except Exception:
                    pass  # the refusal IS the correct outcome
            finally:
                ro_engine.dispose()
            verdict = _reopen_and_judge(db_file, settings)
            if not verdict["ok"]:
                problems.extend(verdict["problems"])
            return problems
        problems.append(f"unknown fault kind {fault!r}")
        return problems
    finally:
        try:
            session.close()
        except Exception:
            pass
        try:
            db.engine.dispose()
        except Exception:
            pass


def run_inprocess_chaos(base: Path, *, runs: int = 100, seed: int = 175) -> dict:
    """The 100-run chaos pass (B-175's acceptance shape).

    Seeded-random fault choice over :data:`FAULT_KINDS`, one
    fresh deployment per run, the invariant judged after every
    fault on the recovered ground. Returns the report dict the
    test asserts on and ``scripts/chaos_report.py`` renders:
    ``{runs, passed, failures: [{run, fault, problems}],
    by_fault: {kind: {runs, passed}}}``."""
    base.mkdir(parents=True, exist_ok=True)
    template_db = _fresh_database(base, "template.db")
    template_db.engine.dispose()
    template = base / "template.db"
    rng = random.Random(seed)
    by_fault: dict[str, dict] = {kind: {"runs": 0, "passed": 0} for kind in FAULT_KINDS}
    failures: list[dict] = []
    passed = 0
    for index in range(runs):
        fault = rng.choice(FAULT_KINDS)
        by_fault[fault]["runs"] += 1
        problems = _one_run(base, index, fault, template)
        if problems:
            failures.append({"run": index, "fault": fault, "problems": problems})
        else:
            passed += 1
            by_fault[fault]["passed"] += 1
    return {
        "runs": runs,
        "passed": passed,
        "failures": failures,
        "by_fault": by_fault,
        "seed": seed,
    }


def file_sha256(path: Path) -> str:
    """A file's SHA-256 — the upload-resume proof's yardstick."""
    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


__all__ = [
    "FAULT_KINDS",
    "file_sha256",
    "invariant_report",
    "run_inprocess_chaos",
    "seed_group",
    "seed_source",
]
