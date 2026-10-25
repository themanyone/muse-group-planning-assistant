"""Requeue stuck jobs (roadmap B-155; RUNBOOK.md entry 5).

The job chassis claims only ``queued`` rows. That is the
right law — two workers can never run the same job — with
one sharp edge: a worker that dies mid-job leaves its row
``running`` forever, and the work (a digest, an extraction,
a delivery) silently never happens. /health reports these
as ``jobs.stuck_running``; this script is the operator's
fix.

    uv run python scripts/requeue_stuck_jobs.py              # list
    uv run python scripts/requeue_stuck_jobs.py --apply      # fix

Listing is the default and changes nothing. ``--apply``
returns every running job untouched for longer than
``--older-than MINUTES`` (default 15, the same threshold
/health reports) to ``queued``, clears its lock, and makes
it due immediately — the next worker pass picks it up.
A job that is merely SLOW (long vision work) shows in the
list with its age; the operator decides. Read the list
before applying it: requeueing a job whose worker is
actually alive and working runs it twice.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select  # noqa: E402

from gpa.config import get_settings  # noqa: E402
from gpa.db import Database  # noqa: E402
from gpa.models.core import Job  # noqa: E402


def stuck_jobs(session, *, older_than: timedelta, now: datetime) -> list[Job]:
    """Running jobs untouched since before the cutoff."""
    cutoff = now - older_than
    return list(
        session.execute(
            select(Job)
            .where(Job.status == "running", Job.updated_at < cutoff)
            .order_by(Job.updated_at)
        ).scalars()
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List (or, with --apply, requeue) stuck running jobs."
    )
    parser.add_argument(
        "--older-than",
        type=int,
        default=15,
        help="Minutes a running job may sit untouched before it counts as stuck",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually requeue. Without this, the script only lists.",
    )
    args = parser.parse_args()

    settings = get_settings()
    database = Database(settings.database_url)
    session = database.session()
    now = datetime.now(timezone.utc)
    try:
        stuck = stuck_jobs(
            session, older_than=timedelta(minutes=args.older_than), now=now
        )
        if not stuck:
            print("No stuck jobs.")
            return 0
        for job in stuck:
            updated = job.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = int((now - updated).total_seconds() // 60)
            print(
                f"  {job.id}  kind={job.kind}  locked_by={job.locked_by}  "
                f"attempts={job.attempts}  untouched {age} min"
            )
        if not args.apply:
            print(f"\n{len(stuck)} stuck job(s) listed; nothing changed. "
                  "Re-run with --apply to requeue them.")
            return 0
        for job in stuck:
            job.status = "queued"
            job.locked_by = None
            job.run_at = now
        session.commit()
        print(f"\nRequeued {len(stuck)} job(s); the next worker pass runs them.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
