"""Auth routes (roadmap B-011 → B-013, B-020, B-021).

Sign-in in its three forms — password, magic link, refresh — plus
registration and password reset. Every credential endpoint sits
behind the auth rate limiter (B-021). All the rules live in
services/identity.py; these handlers parse, call, and answer — and
because services commit before returning, every answer here is
already durable (B-003).
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.ratelimit import auth_rate_limit
from gpa.schemas.identity import (
    LoginRequest,
    LoginResponse,
    MagicLinkRequest,
    MfaChallengeRequest,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    TokenConsumeRequest,
)
from gpa.services import identity as identity_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _session(access: str, refresh: str, user_id: str) -> SessionResponse:
    """Shape a session answer (access + rotating refresh + owner)."""
    return SessionResponse(access_token=access, refresh_token=refresh, user_id=user_id)


def _device(request: Request) -> dict:
    """The caller's device words (B-174): client address + the
    user-agent header, for the session list and anomaly notice."""
    return {
        "ip_address": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""),
    }


@router.post("/register", response_model=SessionResponse, status_code=201, dependencies=[Depends(auth_rate_limit)])
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Create an account and sign the new user straight in."""
    user = identity_service.register_user(
        db, email=body.email, password=body.password, name=body.name,
        timezone=body.timezone, locale=body.locale,
    )
    access, refresh = identity_service.issue_session(
        db, request.app.state.settings, user, **_device(request)
    )
    return _session(access, refresh, user.id)


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(auth_rate_limit)])
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Sign in with email + password. An account with MFA on
    (B-174) earns a challenge here, not a session — the session
    arrives at /login/mfa when the authenticator code answers."""
    user = identity_service.authenticate(db, email=body.email, password=body.password)
    if user.mfa_enabled:
        challenge = identity_service.begin_mfa_challenge(db, user)
        return LoginResponse(user_id=user.id, mfa_required=True, mfa_challenge=challenge)
    access, refresh = identity_service.issue_session(
        db, request.app.state.settings, user, **_device(request)
    )
    return LoginResponse(access_token=access, refresh_token=refresh, user_id=user.id)


@router.post("/login/mfa", response_model=SessionResponse, dependencies=[Depends(auth_rate_limit)])
def login_mfa(body: MfaChallengeRequest, request: Request, db: Session = Depends(get_db)):
    """Answer a sign-in MFA challenge; receive the session."""
    access, refresh, user = identity_service.complete_mfa_challenge(
        db, request.app.state.settings, body.challenge_token, body.code,
        **_device(request),
    )
    return _session(access, refresh, user.id)


@router.post("/magic-link", status_code=202, dependencies=[Depends(auth_rate_limit)])
def magic_link(body: MagicLinkRequest, request: Request, db: Session = Depends(get_db)):
    """Request a sign-in link. Always 202, always the same answer —
    whether the account exists must not leak."""
    identity_service.request_magic_link(db, request.app.state.settings, email=body.email)
    return {"status": "sent"}


@router.post("/magic-link/consume", response_model=SessionResponse, dependencies=[Depends(auth_rate_limit)])
def magic_link_consume(body: TokenConsumeRequest, request: Request, db: Session = Depends(get_db)):
    """Use a magic-link token once; receive a session."""
    access, refresh, user = identity_service.consume_magic_link(
        db, request.app.state.settings, body.token
    )
    return _session(access, refresh, user.id)


# NOTE: magic-link sign-in stays a full sign-in even with MFA on
# (B-174 boundary, recorded in the memory map): the link proves
# control of the account's mailbox, which is itself a factor.
# The MFA challenge guards the password road, where guessing
# is the threat the lockout + second factor answer.


@router.post("/refresh", response_model=SessionResponse)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """Rotate a refresh token into a fresh session (B-013)."""
    access, new_refresh, user = identity_service.rotate_refresh(
        db, request.app.state.settings, body.refresh_token
    )
    return _session(access, new_refresh, user.id)


@router.post("/logout", status_code=204)
def logout(body: RefreshRequest, db: Session = Depends(get_db)):
    """Sign out: the refresh token's whole family is revoked."""
    identity_service.logout(db, body.refresh_token)
    return None


@router.post("/password-reset", status_code=202, dependencies=[Depends(auth_rate_limit)])
def password_reset(body: MagicLinkRequest, request: Request, db: Session = Depends(get_db)):
    """Request a password reset (no account enumeration, as magic links)."""
    identity_service.request_password_reset(db, request.app.state.settings, email=body.email)
    return {"status": "sent"}


@router.post("/password-reset/confirm", status_code=204, dependencies=[Depends(auth_rate_limit)])
def password_reset_confirm(body: TokenConsumeRequest, db: Session = Depends(get_db)):
    """Set the new password from a reset token; all sessions die."""
    identity_service.consume_password_reset(db, body.token, body.new_password or "")
    return None
