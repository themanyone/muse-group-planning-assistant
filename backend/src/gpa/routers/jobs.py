"""Job routes (roadmap B-008): poll a job's status.

`GET /api/v1/jobs/{id}` — the enqueuer's window into the queue.
Reading needs an authenticated principal with the read scope.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import Principal, require_scope
from gpa.schemas.core import JobResponse
from gpa.services import jobs as job_service

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse)
def read_job(
    job_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("read")),
):
    """One job's state: queued, running, done, or dead-lettered."""
    job = job_service.get_job(db, job_id)
    return JobResponse(
        id=job.id, kind=job.kind, status=job.status, progress=job.progress,
        attempts=job.attempts, max_attempts=job.max_attempts,
        last_error=job.last_error, run_at=job.run_at.isoformat(),
    )
