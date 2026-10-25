"""The integration health board (roadmap B-136).

Every integration's state on one screen, in the order a
volunteer coordinator actually needs it: **what state is it
in, and what is the single next step?** The board never
multiplies advice — one integration, one sentence of next
step, drawn from its recorded verdict (B-127's status +
last_error, refreshed by health checks — the board reads
records; it does not ping anything itself, so opening it is
free and cannot hammer an outside system).

The advice names its step concretely: a refused token says
"reconnect the account and re-enter a fresh access token",
not "check your settings". The outside system's own error
words stay on the entry beside the advice — advice never
replaces the evidence.
"""

from sqlalchemy.orm import Session

from gpa.models.groups import Group
from gpa.models.integrations import Integration

# What "connect it" means, per kind — the config keys the
# connect step asks for, in words an admin can act on.
_CONNECT_ADVICE = {
    "facebook_events": (
        "Connect a Facebook Page: add page_id and access_token to this "
        "integration's config, then run a health check."
    ),
    "google_calendar": (
        "Connect a Google calendar: add calendar_id and access_token to "
        "this integration's config, then run a health check."
    ),
    "samtal": (
        "No Samtal is wired in this deployment — when one is, its "
        "address goes in this integration's config."
    ),
    "messenger": (
        "Messenger access has not been granted to this project; the "
        "paste/export road is the way until it is."
    ),
}

# Words that mark a refusal of the credential itself, as
# opposed to any other outside failure.
_TOKEN_WORDS = ("token", "401", "unauthor", "credential", "permission denied")


def advice_for(row: Integration) -> str:
    """The single next step for one integration, in words."""
    if row.last_checked_at is None:
        return "Not checked yet — run a health check to learn this connection's state."
    if row.status == "ok":
        return "Working — nothing to do."
    if row.status == "unconfigured":
        return _CONNECT_ADVICE.get(
            row.kind,
            "Add this integration's config keys, then run a health check.",
        )
    if row.status == "error":
        error = (row.last_error or "").lower()
        if any(word in error for word in _TOKEN_WORDS):
            return (
                "The credential was refused — reconnect the account and "
                "re-enter a fresh access token, then run a health check."
            )
        return (
            "The outside system answered with an error — its own words "
            "are recorded on this entry. Confirm the config, then run "
            "the check again."
        )
    return "Run a health check to learn this connection's state."


def board(db: Session, *, group: Group) -> list[dict]:
    """Every integration of the group, oldest first, each with
    its verdict and its single next step."""
    rows = (
        db.query(Integration)
        .filter(Integration.group_id == group.id)
        .order_by(Integration.created_at, Integration.id)
        .all()
    )
    return [
        {
            "integration_id": row.id,
            "kind": row.kind,
            "name": row.name,
            "status": row.status,
            "last_checked_at": row.last_checked_at,
            "last_success_at": row.last_success_at,
            "last_error": row.last_error,
            "advice": advice_for(row),
        }
        for row in rows
    ]
