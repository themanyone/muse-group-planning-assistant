"""Crash-correction harness (roadmap B-175).

Fault injection as a first-class suite. The subprocess legs
run ``tests/chaos_worker.py`` as a real process and let it
SIGKILL itself at the kill point of one roadmap flow each —
ingest, extraction, approval, upload, digest — then reopen
the database cold, walk B-172's startup-recovery road, and
judge the ground by the invariant in ``gpa.services.chaos``:

* no committed user entry is lost,
* no half-written entity is visible,
* every interrupted job is resumed or visibly failed —
  never silently gone.

The in-process legs cover the faults a subprocess cannot aim
as precisely (disk-full at the storage boundary, a read-only
database), and the seeded 100-run loop is the acceptance's
named pass: one hundred faults, one hundred invariant
verdicts, zero failures.
"""

import hashlib
import os
import signal
import subprocess
import sys
from pathlib import Path

from conftest import make_settings
from gpa.db import Database
from gpa.models.core import Job
from gpa.models.durability import UploadSession
from gpa.models.events import Event
from gpa.models.groups import Candidate
from gpa.models.sources import Source, SourceChunk
from gpa.services import chaos, jobs, recovery

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).resolve().parent / "chaos_worker.py"


def _kill_worker(tmp_path: Path, scenario: str) -> tuple[Path, Path]:
    """Run one chaos scenario to its SIGKILL; return (db, storage)."""
    db_file = tmp_path / "chaos.db"
    storage = tmp_path / "storage"
    marker = tmp_path / "marker.txt"
    result = subprocess.run(
        [sys.executable, str(WORKER), str(db_file), str(storage),
         str(marker), scenario],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    # Death by SIGKILL, at the marker — not an early error.
    assert marker.exists(), f"worker never reached its kill point:\n{result.stderr}"
    assert result.returncode == -signal.SIGKILL, result.returncode
    return db_file, storage


def _recover_and_judge(tmp_path: Path, db_file: Path):
    """The restarted process's road: recovery, then the verdict."""
    settings = make_settings(tmp_path)
    # make_settings points storage at tmp_path/storage — the
    # same ground the worker wrote its upload parts to.
    db = Database(f"sqlite:///{db_file}")
    session = db.session()
    try:
        report = recovery.assess_and_repair(session, settings)
        verdict = chaos.invariant_report(session)
        return db, session, report, verdict
    except Exception:
        session.close()
        db.engine.dispose()
        raise


def test_kill_after_ingest_commit_loses_nothing(tmp_path):
    db_file, _storage = _kill_worker(tmp_path, "ingest_after_commit")
    db, session, _report, verdict = _recover_and_judge(tmp_path, db_file)
    try:
        assert verdict["ok"], verdict["problems"]
        sources = session.query(Source).all()
        assert len(sources) == 1
        chunks = session.query(SourceChunk).filter(
            SourceChunk.source_id == sources[0].id
        ).all()
        assert [c.position for c in sorted(chunks, key=lambda c: c.position)] == [0, 1, 2]
    finally:
        session.close()
        db.engine.dispose()


def test_kill_mid_ingest_transaction_shows_nothing_half_written(tmp_path):
    db_file, _storage = _kill_worker(tmp_path, "ingest_mid_transaction")
    db, session, _report, verdict = _recover_and_judge(tmp_path, db_file)
    try:
        assert verdict["ok"], verdict["problems"]
        assert session.query(Source).count() == 0
        assert session.query(SourceChunk).count() == 0
    finally:
        session.close()
        db.engine.dispose()


def _killed_job_is_recovered_and_completes(tmp_path, scenario, kind):
    db_file, _storage = _kill_worker(tmp_path, scenario)
    settings = make_settings(tmp_path)
    db, session, report, verdict = _recover_and_judge(tmp_path, db_file)
    try:
        assert verdict["ok"], verdict["problems"]
        # The claim's worker is dead and its lease is the
        # recovery clock's business: force the lease expired
        # the way time would, then walk recovery again — the
        # B-172 road a later restart takes.
        job = session.query(Job).filter(Job.kind == kind).one()
        assert job.status == "running"  # the kill left it claimed
        from datetime import timedelta

        from gpa.models.identity import now_utc

        job.updated_at = now_utc() - timedelta(seconds=recovery.LEASE_SECONDS + 60)
        session.commit()
        second = recovery.assess_and_repair(session, settings)
        assert second["verdict"] == "repaired"
        healed = session.get(Job, job.id)
        assert healed.status == "queued" and healed.last_error  # resumed, on record
        # And the resumed job actually RUNS to done — resumed,
        # not merely relabelled. (The probe handler is restored
        # afterwards: the registry is process-global and later
        # tests own the real handlers.)
        seen = []
        previous = jobs.HANDLERS.get(kind)
        jobs.register_handler(kind, lambda db_, payload: seen.append(payload))
        try:
            assert jobs.process_one(session, settings, "chaos-judge") is True
        finally:
            if previous is None:
                jobs.HANDLERS.pop(kind, None)
            else:
                jobs.register_handler(kind, previous)
        assert session.get(Job, job.id).status == "done"
        assert seen == [{}]
    finally:
        session.close()
        db.engine.dispose()


def test_kill_mid_extraction_job_is_recovered_and_completes(tmp_path):
    _killed_job_is_recovered_and_completes(tmp_path, "extraction_mid_job", "extract")


def test_kill_mid_digest_job_is_recovered_and_completes(tmp_path):
    _killed_job_is_recovered_and_completes(tmp_path, "digest_mid_job", "digest_run")


def test_kill_mid_approval_leaves_candidate_pending_and_no_event(tmp_path):
    db_file, _storage = _kill_worker(tmp_path, "approval_mid_transaction")
    db, session, _report, verdict = _recover_and_judge(tmp_path, db_file)
    try:
        assert verdict["ok"], verdict["problems"]
        candidate = session.query(Candidate).one()
        assert candidate.status == "pending" and candidate.approved_by is None
        assert session.query(Event).count() == 0  # no half-minted event
    finally:
        session.close()
        db.engine.dispose()


def test_kill_mid_upload_keeps_the_resume_contract(tmp_path):
    db_file, storage = _kill_worker(tmp_path, "upload_mid_session")
    db, session, _report, verdict = _recover_and_judge(tmp_path, db_file)
    try:
        assert verdict["ok"], verdict["problems"]
        upload = session.get(UploadSession, "chaos-upload")
        assert upload is not None and upload.status == "open"  # resumable, not swept
        part0 = storage / "uploads" / "chaos-upload" / "0.part"
        assert part0.read_bytes() == b"chaos-upload-part-zero|"
        # Resume: the missing part arrives, the assembled whole
        # matches the session's promised hash — resumed, never
        # restarted, and the hash proves the file (B-171's law).
        # (The part bytes are chaos_worker.py's UPLOAD_PART_1,
        # duplicated here as a literal so this file never
        # imports the worker it subprocesses.)
        whole = part0.read_bytes() + b"chaos-upload-part-one|"
        assert hashlib.sha256(whole).hexdigest() == upload.sha256
    finally:
        session.close()
        db.engine.dispose()


def test_hundred_run_chaos_pass(tmp_path):
    """The acceptance, by name: 100 seeded faults across the
    five fault kinds, the invariant judged after every one."""
    report = chaos.run_inprocess_chaos(tmp_path / "chaos100", runs=100, seed=175)
    assert report["runs"] == 100
    assert report["failures"] == [], report["failures"][:3]
    assert report["passed"] == 100
    # Every fault kind was genuinely exercised — a pass that
    # never drew a kind proves nothing about that kind.
    for kind, counts in report["by_fault"].items():
        assert counts["runs"] > 0, f"fault kind {kind} never drawn"
        assert counts["passed"] == counts["runs"]
