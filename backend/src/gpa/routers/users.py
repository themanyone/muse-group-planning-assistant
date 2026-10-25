"""User routes: profile, password, email change, API keys.

Everything here acts on the CALLER's own account (the /users/me
surface of roadmap §5) — a person manages their own profile and
their own keys, nobody else's.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import Principal, get_principal
from gpa.errors import AppError
from gpa.schemas.identity import (
    ApiKeyCreateRequest,
    ApiKeyResponse,
    EmailChangeRequest,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    TokenConsumeRequest,
    UserResponse,
)
from gpa.schemas.privacy import MeDataResponse
from gpa.services import identity as identity_service
from gpa.services import me_data as me_data_service

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _me(db: Session, principal: Principal):
    """The caller's own user row (seats have no /users/me profile)."""
    if principal.is_seat or principal.user_id is None:
        raise AppError("forbidden", detail="Agent seats do not have a user profile.")
    return identity_service.get_user(db, principal.user_id)


def _user_response(user) -> UserResponse:
    """Shape a user for the API — credentials are never in the schema."""
    return UserResponse(
        id=user.id, email=user.email, name=user.name,
        timezone=user.timezone, locale=user.locale,
    )


def _key_response(row, full_key: str | None = None) -> ApiKeyResponse:
    """Shape an API key; ``key`` is filled only at creation time."""
    return ApiKeyResponse(
        id=row.id, label=row.label, prefix=row.prefix,
        scopes=row.scope_set() and sorted(row.scope_set(), key=lambda s: identity_service.ALL_SCOPES.index(s)) or [],
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        revoked=row.revoked_at is not None,
        key=full_key,
    )


@router.get("/me", response_model=UserResponse)
def me(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """Who am I? — the profile of the signed-in person."""
    return _user_response(_me(db, principal))


@router.get("/me/data", response_model=MeDataResponse)
def my_data(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """Everything the system holds about me (B-144): my
    account, my groups, my keys by prefix, what I wrote and
    chose. No hashes or secrets — a person reading their own
    record should never meet one."""
    return me_data_service.build_me_data(db, _me(db, principal))


@router.patch("/me", response_model=UserResponse)
def update_me(body: ProfileUpdateRequest, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """Update my name / timezone / locale."""
    user = _me(db, principal)
    changes = body.model_dump(exclude_none=True)
    identity_service.update_profile(db, user, **changes)
    return _user_response(user)


@router.post("/me/password", status_code=204)
def change_password(body: PasswordChangeRequest, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """Change my password (current password required); every
    existing session is revoked by the service (B-020)."""
    user = _me(db, principal)
    identity_service.change_password(db, user, current=body.current_password, new=body.new_password)
    return None


@router.post("/me/email-change", status_code=202)
def request_email_change(body: EmailChangeRequest, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """Start an email change — confirmation goes to the NEW address."""
    user = _me(db, principal)
    identity_service.request_email_change(db, request.app.state.settings, user, new_email=body.new_email)
    return {"status": "sent"}


@router.post("/me/email-change/confirm", response_model=UserResponse)
def confirm_email_change(body: TokenConsumeRequest, db: Session = Depends(get_db)):
    """Complete an email change with the token sent to the new address."""
    user = identity_service.consume_email_change(db, body.token)
    return _user_response(user)


@router.post("/me/api-keys", response_model=ApiKeyResponse, status_code=201)
def create_api_key(body: ApiKeyCreateRequest, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """Mint one of my API keys. The full key is shown exactly once,
    in this response (B-014)."""
    user = _me(db, principal)
    row, full_key = identity_service.create_api_key(
        db, label=body.label, scopes=body.scopes, user_id=user.id
    )
    return _key_response(row, full_key)


@router.get("/me/api-keys", response_model=list[ApiKeyResponse])
def list_api_keys(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """My keys — prefixes and scopes only, never the keys."""
    user = _me(db, principal)
    return [_key_response(row) for row in identity_service.list_api_keys(db, user_id=user.id)]


@router.delete("/me/api-keys/{key_id}", status_code=204)
def revoke_api_key(key_id: str, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    """Revoke one of my keys, effective immediately."""
    user = _me(db, principal)
    identity_service.revoke_api_key(db, user_id=user.id, key_id=key_id)
    return None
