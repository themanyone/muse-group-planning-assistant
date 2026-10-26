"""Authentication + permission dependencies (roadmap B-017).

This file is THE door. Every router takes its caller from here —
``get_principal`` for "who is calling", ``require_scope`` for "is
this credential allowed to do this class of thing", and
``require_group_role`` for "may this caller act in this group at
this level". No router hand-rolls a permission check (B-017's grep
test guards exactly that).

A *principal* is whoever is calling: a person (via access token or
their own API key) or an agent seat (via its API key). Equal
principals, one door (D-35).
"""

from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.errors import AppError
from gpa.models.groups import Group, role_at_least
from gpa.models.identity import ApiKey, User, now_utc
from gpa.security import hash_secret, verify_access_token
from gpa.services.groups import active_membership, get_group


@dataclass
class Principal:
    """The caller, resolved once per request.

    ``scopes`` is None for an access-token session (a signed-in
    person carries all scopes); for API keys it is the key's scope
    set, enforced by require_scope. ``role`` is set for seats (their
    seat role in their one group) and unused for people, whose roles
    are per-group memberships.
    """

    kind: str  # "user" | "seat"
    user_id: str | None = None
    seat_id: str | None = None
    seat_group_id: str | None = None
    seat_role: str | None = None
    scopes: set[str] | None = None
    key_id: str | None = None
    # The refresh family this access token was minted in (B-174),
    # when known — a label for the session list's "current" mark.
    session_family_id: str = ""
    user: User | None = field(default=None, repr=False)

    @property
    def label(self) -> str:
        """Audit label: a seat's work is always the seat's (B-018)."""
        return f"seat:{self.seat_id}" if self.kind == "seat" else f"user:{self.user_id}"

    @property
    def is_seat(self) -> bool:
        return self.kind == "seat"


def _principal_from_api_key(db: Session, raw_key: str) -> Principal:
    """Resolve an X-Api-Key header to its user or seat, or 401."""
    key = (
        db.query(ApiKey)
        .filter(ApiKey.key_hash == hash_secret(raw_key), ApiKey.revoked_at.is_(None))
        .one_or_none()
    )
    if key is None:
        raise AppError("not_authenticated", detail="That API key is not valid.")
    key.last_used_at = now_utc()
    db.commit()
    if key.seat_id is not None:
        from gpa.models.groups import AgentSeat

        seat = db.get(AgentSeat, key.seat_id)
        if seat is None:
            raise AppError("not_authenticated", detail="That API key is not valid.")
        return Principal(
            kind="seat",
            seat_id=seat.id,
            seat_group_id=seat.group_id,
            seat_role=seat.role,
            scopes=key.scope_set(),
            key_id=key.id,
        )
    user = db.get(User, key.user_id)
    if user is None:
        raise AppError("not_authenticated", detail="That API key is not valid.")
    return Principal(
        kind="user", user_id=user.id, scopes=key.scope_set(), key_id=key.id, user=user
    )


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Resolve the caller from X-Api-Key or the Bearer access token.

    Fails with 401 (never 403): not signed in and not allowed are
    different answers, and this door only answers the first.
    """
    api_key = request.headers.get("x-api-key")
    if api_key:
        return _principal_from_api_key(db, api_key)
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        settings = request.app.state.settings
        claims = verify_access_token(settings.secret_key, authorization[7:].strip())
        user = db.get(User, claims["sub"])
        if user is None:
            raise AppError("not_authenticated")
        return Principal(
            kind="user", user_id=user.id, scopes=None, user=user,
            session_family_id=str(claims.get("fam") or ""),
        )
    raise AppError("not_authenticated")


def require_scope(scope: str):
    """Dependency factory: the caller's credential must carry a scope.

    Session callers (scopes=None) carry every scope. Key callers are
    checked against the key — a read key creating things is B-014's
    canonical 403.
    """

    def _check(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.scopes is not None and scope not in principal.scopes:
            raise AppError(
                "insufficient_scope",
                detail=f"This operation needs the '{scope}' scope.",
            )
        return principal

    return _check


@dataclass
class GroupContext:
    """A group plus the caller's role in it (None when not a member)."""

    group: Group
    role: str | None
    principal: Principal


def get_group_context(
    group_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> GroupContext:
    """Resolve group + the caller's role, hiding private groups.

    The founding lesson (B-015): for a private group, a non-member
    gets 404 — the API does not confirm the group exists. For
    invite/public-listed groups a non-member gets role=None and the
    route decides what (if anything) they may see.
    """
    group = get_group(db, group_id)
    if principal.is_seat:
        role = principal.seat_role if principal.seat_group_id == group.id else None
        if role is None and group.visibility == "private":
            raise AppError("not_found", detail="That group could not be found.")
        return GroupContext(group=group, role=role, principal=principal)
    membership = active_membership(db, principal.user_id, group.id)
    role = membership.role if membership else None
    if role is None and group.visibility == "private":
        # An invited (not yet active) person may still REACH the
        # group — to accept or decline their invitation — with no
        # role. Everyone else gets the 404: existence stays hidden.
        from gpa.models.groups import Membership

        invited = (
            db.query(Membership)
            .filter(
                Membership.user_id == principal.user_id,
                Membership.group_id == group.id,
                Membership.status == "invited",
            )
            .one_or_none()
        )
        if invited is None:
            raise AppError("not_found", detail="That group could not be found.")
    return GroupContext(group=group, role=role, principal=principal)


def require_group_role(minimum: str):
    """Dependency factory: caller must hold at least ``minimum`` role
    in the group named by the path. The one permission check every
    group router uses (B-017)."""

    def _check(context: GroupContext = Depends(get_group_context)) -> GroupContext:
        if context.role is None or not role_at_least(context.role, minimum):
            raise AppError("forbidden", detail=f"That needs a {minimum} or above in this group.")
        return context

    return _check
