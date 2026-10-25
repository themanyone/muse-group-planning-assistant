"""Job routes (roadmap B-008): poll a job's status.

`GET /api/v1/jobs/{id}` — the enqueuer's window into the queue.
Reading needs an authenticated principal with the read scope.

Phase 13 (B-140) scoping: a job whose payload names a group —
directly, or through a group-owned row it works on (a source,
a notification, an outbound webhook) — belongs to that group,
and only its members (or its seat) may read the job's state.
To everyone else the job is a 404, exactly as if it did not
exist: a job's kind, progress, and last error are the group's
business. Jobs whose payload names no group are the chassis'
own internal kinds; they carry no group data and stay readable
by any signed-in caller, as B-008 built them.
"""

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import Principal, require_scope
from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.integrations import OutboundWebhook
from gpa.models.notifications import Notification
from gpa.models.sources import Source
from gpa.schemas.core import JobResponse
from gpa.services import jobs as job_service
from gpa.services.groups import active_membership

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _job_group_id(db: Session, job) -> str | None:
    """The group a job belongs to, when its payload names one.

    Jobs address their work in the payload (the chassis' rule):
    ``group_id`` for group chains (digest, reconcile), or the id
    of a group-owned row the job works on — a source (extract,
    transcribe, vision), a notification (the outbox), an
    outbound webhook (delivery). Anything else names no group.
    """
    try:
        payload = json.loads(job.payload_json or "{}")
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("group_id"):
        return str(payload["group_id"])
    if payload.get("source_id"):
        source = db.get(Source, str(payload["source_id"]))
        if source is not None:
            return source.group_id
    if payload.get("notification_id"):
        note = db.get(Notification, str(payload["notification_id"]))
        if note is not None:
            return note.group_id
    if payload.get("webhook_id"):
        hook = db.get(OutboundWebhook, str(payload["webhook_id"]))
        if hook is not None:
            return hook.group_id
    return None


def _assert_may_read(db: Session, job, principal: Principal) -> None:
    """Refuse (404) a read of a group-owned job by an outsider."""
    group_id = _job_group_id(db, job)
    if group_id is None:
        return  # Process-internal job: no group data to protect.
    if db.get(Group, group_id) is None:
        return  # The group is gone (erasure); its jobs went with it.
    if principal.is_seat:
        allowed = principal.seat_group_id == group_id
    else:
        allowed = active_membership(db, principal.user_id, group_id) is not None
    if not allowed:
        raise AppError("not_found", detail="That job could not be found.")


@router.get("/{job_id}", response_model=JobResponse)
def read_job(
    job_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("read")),
):
    """One job's state: queued, running, done, or dead-lettered."""
    job = job_service.get_job(db, job_id)
    _assert_may_read(db, job, principal)
    return JobResponse(
        id=job.id, kind=job.kind, status=job.status, progress=job.progress,
        attempts=job.attempts, max_attempts=job.max_attempts,
        last_error=job.last_error, run_at=job.run_at.isoformat(),
    )
