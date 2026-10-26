"""Chaos worker — the subprocess that dies (roadmap B-175).

This file is NOT a test. ``tests/test_chaos.py`` runs it as a
real subprocess against a real database file, and every
scenario ends the same way: the worker writes a marker file
(the proof it reached the kill point by the road intended,
not by crashing early), flushes, and then kills ITSELF with
SIGKILL — the death no cleanup handler, no ``finally``, no
atexit hook can soften. What the parent test finds in the
database afterwards is exactly what a real ``kill -9`` (or a
power cut at the same instant) would leave.

Usage:
    python chaos_worker.py <db_file> <storage_dir> <marker_file> <scenario>

Scenarios (one per roadmap flow):
    ingest_after_commit     paste ingest commits, then dies
    ingest_mid_transaction  paste ingest dies before COMMIT
    extraction_mid_job      an extract job is claimed, then dies
    approval_mid_transaction approval dies before its COMMIT —
                             candidate flip + minted event are
                             one transaction or neither is
    upload_mid_session      a chunked upload dies with one part
                             landed; the resume contract must hold
    digest_mid_job          a digest_run job is claimed, then dies
"""

import hashlib
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

# The package lives under src/ in the repo; a subprocess does
# not inherit pytest's import posture, so name the road plainly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gpa.db import Database  # noqa: E402
from gpa.models.core import Job  # noqa: E402
from gpa.models.durability import UploadSession  # noqa: E402
from gpa.models.events import Event  # noqa: E402
from gpa.models.groups import Candidate  # noqa: E402
from gpa.services import jobs as job_service  # noqa: E402
from gpa.services.chaos import seed_group, seed_source  # noqa: E402

# The upload scenario's whole file: two parts, the first landed
# before the kill, the second sent by the parent's "resume".
UPLOAD_PART_0 = b"chaos-upload-part-zero|"
UPLOAD_PART_1 = b"chaos-upload-part-one|"


def _die(marker: Path) -> None:
    """Write the kill-point marker, flush everything, SIGKILL."""
    marker.write_text("at-kill-point", encoding="utf-8")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)


def main() -> None:
    db_file, storage_dir, marker, scenario = (
        Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4],
    )
    db = Database(f"sqlite:///{db_file}")
    db.create_all()
    session = db.session()
    group = seed_group(session, slug="chaos-sub")

    if scenario == "ingest_after_commit":
        seed_source(session, group.id)
        session.commit()
        _die(marker)
    elif scenario == "ingest_mid_transaction":
        seed_source(session, group.id)
        session.flush()  # rows written, transaction OPEN — no commit
        _die(marker)
    elif scenario in ("extraction_mid_job", "digest_mid_job"):
        kind = "extract" if scenario.startswith("extraction") else "digest_run"
        job = Job(kind=kind, payload_json="{}", status="queued")
        session.add(job)
        session.commit()
        claimed = job_service.claim_next(session, "chaos-worker")
        assert claimed is not None and claimed.id == job.id
        _die(marker)  # the worker dies HOLDING the claimed job
    elif scenario == "approval_mid_transaction":
        candidate = Candidate(
            group_id=group.id, kind="event", title="Chaos picnic",
            created_by="system:extraction", status="pending",
        )
        session.add(candidate)
        session.commit()
        # The approval's own transaction (services/groups.py's
        # shape): flip the candidate AND mint the event, or
        # neither. The kill lands between flush and commit.
        candidate.status = "approved"
        candidate.approved_by = "user:chaos"
        session.add(Event(
            group_id=group.id, title="Chaos picnic", created_by="user:chaos",
            starts_at=datetime(2026, 11, 7, 19, 0),
            ends_at=datetime(2026, 11, 7, 21, 0),
        ))
        session.flush()
        _die(marker)
    elif scenario == "upload_mid_session":
        whole = UPLOAD_PART_0 + UPLOAD_PART_1
        upload = UploadSession(
            id="chaos-upload", group_id=group.id, user_id="user:chaos",
            filename="chaos.txt", total_bytes=len(whole),
            chunk_bytes=len(UPLOAD_PART_0),
            sha256=hashlib.sha256(whole).hexdigest(),
            parts_json="[0]", status="open",
        )
        session.add(upload)
        session.commit()
        part_dir = storage_dir / "uploads" / "chaos-upload"
        part_dir.mkdir(parents=True)
        part = part_dir / "0.part"
        part.write_bytes(UPLOAD_PART_0)
        with open(part, "ab") as handle:  # fsync: the landed part is durable
            handle.flush()
            os.fsync(handle.fileno())
        _die(marker)
    else:
        raise SystemExit(f"unknown scenario {scenario!r}")


if __name__ == "__main__":
    main()
