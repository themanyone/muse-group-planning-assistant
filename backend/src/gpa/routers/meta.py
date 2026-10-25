"""Meta routes: health and deployment info (roadmap B-010, §5).

``GET /health`` is deliberately at the root, unauthenticated, and
shallow-but-honest: it proves the process is up AND the database
answers, because "process up, database dead" is not healthy.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from gpa import __version__
from gpa.db import get_db
from gpa.schemas.core import HealthResponse, MetaResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    """Liveness + database reachability in one small answer."""
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", version=__version__, database="ok")


@router.get("/api/v1/meta", response_model=MetaResponse)
def meta(request: Request) -> MetaResponse:
    """Public facts about this deployment for clients and tooling."""
    return MetaResponse(
        name="Group Planning Assistant",
        version=__version__,
        api="v1",
        openapi="/openapi.json",
    )
