"""Meta routes: health and deployment info (roadmap B-010, §5).

``GET /health`` is deliberately at the root, unauthenticated —
a load balancer or an operator's curl must be able to ask
without a login. It began as process + database; B-155
deepened it (services/health.py): the job queue's shape, the
configured drivers, and a storage probe, answering 503 when
the database is down, because "process up, database dead" is
not healthy.

The deepening is DISCLOSED carefully, reconciling B-155 with
the Phase 13 threat model (whose test pins this surface):
anonymous callers get the original minimal answer — status,
version, database ok/error, and an EMPTY checks object. The
deep checks (engine name, queue counts, driver names, error
details that can carry filesystem paths) go only to a
signed-in caller, resolved through the one true door
(deps.get_principal, tried softly — a bad or absent
credential means anonymous, never an error on this route).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from gpa import __version__
from gpa.db import get_db
from gpa.deps import get_principal
from gpa.schemas.core import HealthResponse, MetaResponse
from gpa.services import health as health_service

router = APIRouter(tags=["meta"])


def _signed_in(request: Request, db: Session) -> bool:
    """True when the request carries a credential the one
    true door accepts. Any refusal — absent, invalid,
    expired — means anonymous; /health never errors on auth."""
    try:
        get_principal(request, db)
    except Exception:  # noqa: BLE001 — anonymous is a valid answer here
        return False
    return True


@router.get("/health", response_model=HealthResponse)
def health(request: Request, db: Session = Depends(get_db)):
    """Liveness for everyone; the deep checks for signed-in
    callers (see the module docstring for why)."""
    result = health_service.deep_health(
        db,
        database=request.app.state.db,
        settings=request.app.state.settings,
        storage=request.app.state.storage,
    )
    body = HealthResponse(
        status=result["status"],
        version=__version__,
        database=result["checks"]["database"]["status"],
        checks=result["checks"] if _signed_in(request, db) else {},
    )
    if result["status"] == "down":
        # The body stays the health shape; the CODE says down.
        return JSONResponse(status_code=503, content=body.model_dump())
    return body


@router.get("/api/v1/meta", response_model=MetaResponse)
def meta(request: Request) -> MetaResponse:
    """Public facts about this deployment for clients and tooling."""
    return MetaResponse(
        name="Group Planning Assistant",
        version=__version__,
        api="v1",
        openapi="/openapi.json",
    )
