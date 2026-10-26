"""Startup recovery tests (roadmap B-172).

One rehearsal per corruption, plus the double-verdict
guard: a healthy database scores ``serve`` twice in a row
and is never "healed" aside.
"""

from datetime import timedelta

import sqlalchemy

from conftest import make_settings
from gpa.db import Database
from gpa.models.core import Job
from gpa.models.durability import UploadSession
from gpa.models.identity import now_utc
from gpa.services import recovery


def _deployment(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(settings.database_url)
    db.create_all()
    return settings, db


def _assess(db, settings):
    session = db.session()
    try:
        return recovery.assess_and_repair(session, settings)
    finally:
        session.close()


def test_healthy_database_scores_serve_twice(tmp_path):
    """THE double-verdict guard."""
    settings, db = _deployment(tmp_path)
    try:
        first = _assess(db, settings)
        second = _assess(db, settings)
        assert first["verdict"] == "serve" and first["actions"] == []
        assert second["verdict"] == "serve" and second["actions"] == []
    finally:
        db.engine.dispose()


def test_stuck_job_is_requeued_once_then_serve(tmp_path):
    settings, db = _deployment(tmp_path)
    try:
        session = db.session()
        stale = now_utc() - timedelta(seconds=recovery.LEASE_SECONDS + 60)
        job = Job(kind="digest_run", status="running", locked_by="dead-worker",
                  attempts=1, max_attempts=5)
        job.updated_at = stale
        session.add(job)
        fresh = Job(kind="vision", status="running", locked_by="live-worker")
        session.add(fresh)
        session.commit()
        job_id, fresh_id = job.id, fresh.id
        session.close()

        report = _assess(db, settings)
        assert report["verdict"] == "repaired"
        assert any(job_id in a for a in report["actions"])

        session = db.session()
        try:
            healed = session.get(Job, job_id)
            assert healed.status == "queued" and healed.attempts == 2
            assert healed.locked_by is None
            untouched = session.get(Job, fresh_id)
            assert untouched.status == "running"  # within its lease — not ours to touch
        finally:
            session.close()
        # Idempotent: the same ground now scores serve.
        assert _assess(db, settings)["verdict"] == "serve"
    finally:
        db.engine.dispose()


def test_spent_job_fails_instead_of_requeueing(tmp_path):
    settings, db = _deployment(tmp_path)
    try:
        session = db.session()
        job = Job(kind="transcribe", status="running", attempts=5, max_attempts=5)
        job.updated_at = now_utc() - timedelta(seconds=recovery.LEASE_SECONDS + 1)
        session.add(job)
        session.commit()
        job_id = job.id
        session.close()

        _assess(db, settings)
        session = db.session()
        try:
            row = session.get(Job, job_id)
            assert row.status == "failed"
            assert "Recovered at startup" in row.last_error
        finally:
            session.close()
    finally:
        db.engine.dispose()


def test_orphan_parts_swept_open_sessions_kept(tmp_path):
    settings, db = _deployment(tmp_path)
    try:
        from gpa.models.groups import Group

        session = db.session()
        session.add(Group(id="g1", name="G", slug="g"))
        session.commit()
        session.add(UploadSession(
            id="sess-open", group_id="g1", user_id="u1", filename="a.txt",
            total_bytes=10, chunk_bytes=5, sha256="0" * 64, status="open",
        ))
        session.add(UploadSession(
            id="sess-done", group_id="g1", user_id="u1", filename="b.txt",
            total_bytes=10, chunk_bytes=5, sha256="0" * 64, status="completed",
        ))
        session.commit()
        session.close()

        root = settings.storage_dir / "uploads"
        for name in ("sess-open", "sess-done", "sess-ghost"):
            (root / name).mkdir(parents=True)
            (root / name / "0.part").write_bytes(b"12345")

        report = _assess(db, settings)
        assert (root / "sess-open" / "0.part").exists()  # resume contract
        assert not (root / "sess-done").exists()
        assert not (root / "sess-ghost").exists()
        assert report["verdict"] == "repaired"
    finally:
        db.engine.dispose()


def test_unknown_migration_revision_refuses_with_the_repair(tmp_path):
    settings, db = _deployment(tmp_path)
    try:
        session = db.session()
        session.execute(
            sqlalchemy.text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
        )
        session.execute(
            sqlalchemy.text(
                "INSERT INTO alembic_version (version_num) VALUES ('9999_from_the_future')"
            )
        )
        session.commit()
        session.close()
        report = _assess(db, settings)
        assert report["verdict"] == "refuse"
        assert "9999_from_the_future" in report["problems"][0]
        assert "Repair:" in report["problems"][0]
    finally:
        db.engine.dispose()
