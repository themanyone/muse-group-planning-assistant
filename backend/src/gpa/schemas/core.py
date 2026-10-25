"""Core schemas: health, job status, audit entries."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """The first thing to check when "it doesn't work" (roadmap §5)."""

    status: str
    version: str
    database: str


class MetaResponse(BaseModel):
    """Public facts about this deployment: name, version, contract."""

    name: str
    version: str
    api: str
    openapi: str


class JobResponse(BaseModel):
    """A job's state as its enqueuer can poll it (B-008)."""

    id: str
    kind: str
    status: str
    # 0–100, advanced by the handler and set to 100 on completion
    # (B-033); the group stream speaks changes to it.
    progress: int = 0
    attempts: int
    max_attempts: int
    last_error: str
    run_at: str


class AuditEntryResponse(BaseModel):
    """One audit row (B-019)."""

    id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    detail: dict
    created_at: str
