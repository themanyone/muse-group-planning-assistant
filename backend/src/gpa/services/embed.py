"""Embed origins + the public page (roadmap B-135).

B-059 gave a public-listed group a public face. This slice
completes the widget story: a group REGISTERS the web
origins allowed to embed its page, and CORS answers are
made only for those origins — **an unregistered origin gets
nothing**: no `Access-Control-Allow-Origin` header (so a
browser blocks the read), and the embed endpoint itself
refuses a present-but-unregistered Origin outright.

An origin is a scheme + host (+ port) — never a path: a
path would be a page, and pages are not origins. Values are
normalised (lowercase, no trailing slash) so the register
and the browser's `Origin` header meet exactly.
"""

import json
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.services import audit


def origins_of(group: Group) -> list[str]:
    """The group's registered embed origins."""
    try:
        rows = json.loads(group.embed_origins_json or "[]")
    except json.JSONDecodeError:
        return []
    return [str(row) for row in rows if isinstance(row, str)]


def _normalise(origin: str) -> str:
    """One origin, canonical — or a plain-words 422.

    An origin is `scheme://host[:port]` and nothing more:
    a path, query, or fragment names a PAGE, and letting one
    into the register would quietly widen what it vouches
    for."""
    parsed = urlparse(origin.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError(
            "validation_error",
            detail=(
                f"'{origin}' is not an origin. An origin looks like "
                "https://village.example — a scheme and a host, no path."
            ),
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise AppError(
            "validation_error",
            detail=(
                f"'{origin}' carries a path — an origin is a scheme "
                "and a host only (https://village.example)."
            ),
        )
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def set_origins(
    db: Session, *, actor_label: str, group: Group, origins: list[str]
) -> list[str]:
    """Replace the group's embed register whole (an admin
    act): the list the CORS answers and the embed door check
    against. Duplicates collapse; order is kept."""
    normalised: list[str] = []
    for origin in origins:
        value = _normalise(origin)
        if value not in normalised:
            normalised.append(value)
    group.embed_origins_json = json.dumps(normalised)
    audit.record(
        db, actor=actor_label, action="group.embed_origins_set",
        group_id=group.id, entity_type="group", entity_id=group.id,
        detail={"origins": normalised},
    )
    db.commit()
    return normalised


def origin_registered(db: Session, origin: str) -> bool:
    """Is this origin on ANY group's embed register? (The
    CORS middleware's question — a browser origin is answered
    for when the deployment's groups vouch for it.)"""
    candidate = origin.strip().lower().rstrip("/")
    for group in db.query(Group).all():
        if candidate in origins_of(group):
            return True
    return False


def group_allows_origin(group: Group, origin: str) -> bool:
    """Is this origin on THIS group's register? (The embed
    endpoint's question — stricter than the middleware's:
    the widget serves one group's data.)"""
    return origin.strip().lower().rstrip("/") in origins_of(group)
