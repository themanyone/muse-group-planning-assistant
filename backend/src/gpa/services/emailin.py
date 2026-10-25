"""Email-in ingest (roadmap B-030).

What this file is for: each group can open an inbound email
channel — a per-group address whose secret token IS the
permission. Mail delivered to that address becomes an ``email``
source: the group's members granted this channel on purpose (an
admin created it, and the creation is audited), so a newsletter
a member forwards in needs no further ceremony.

The receiver route is webhook-shaped NOW (a JSON body with
from/subject/text/html, the shape every mail provider's inbound
webhook shares) and the real provider is wired in Phase 12: the
provider adapter will verify its own signature, pull the token
out of the To address, and call ``receive_email`` here. The part
that must never be an afterthought — the permission record and
the forged-token refusal — is built and tested today.

Forged or unknown tokens are refused (401 ``token_invalid``) and
LOGGED: an audit row with no group (the token named none) plus
the app log. A refused mail creates no source, ever.
"""

import logging

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.sources import InboundAddress
from gpa.security import hash_secret, new_token
from gpa.services import audit
from gpa.services import files as file_service
from gpa.services import newsletter as newsletter_service
from gpa.services import sources as source_service

log = logging.getLogger("gpa.emailin")

# The local part carries the token; the domain is the deployment's
# own inbound domain once Phase 12 wires a provider. The token —
# never the group name — is what routes the mail, so addresses do
# not leak which group they feed.
ADDRESS_TEMPLATE = "in+{token}@inbox.local"


def create_inbound_address(
    db: Session, *, actor_label: str, group: Group
) -> tuple[InboundAddress, str, str]:
    """Open a group's email-in channel; return (row, token, address).

    The full token is returned ONCE, here — only its hash and a
    short display prefix are stored (the API-key discipline,
    B-014). Losing the token means revoking and re-issuing, never
    recovering.
    """
    token = new_token(32)
    row = InboundAddress(
        group_id=group.id,
        token_hash=hash_secret(token),
        token_prefix=token[:8],
        created_by=actor_label,
    )
    db.add(row)
    audit.record(
        db, actor=actor_label, action="email_in.address_created",
        group_id=group.id, entity_type="inbound_address", entity_id=row.id,
        detail={"token_prefix": row.token_prefix},
    )
    db.commit()
    return row, token, ADDRESS_TEMPLATE.format(token=token)


def _resolve(db: Session, token: str) -> InboundAddress | None:
    """The address row a presented token belongs to, or None."""
    return (
        db.query(InboundAddress)
        .filter(InboundAddress.token_hash == hash_secret(token))
        .one_or_none()
    )


def receive_email(
    db: Session,
    *,
    token: str,
    sender: str,
    subject: str = "",
    text: str = "",
    html: str = "",
):
    """Turn one delivered email into a source, or refuse it.

    The source's permission record reads ``system:email-in`` —
    the channel is the granter — and a second audit row names the
    member who opened the channel and the outside sender, so the
    chain from permission to material is complete. The sender is
    also kept on every chunk: it is provenance, not decoration.
    """
    address = _resolve(db, token)
    if address is None:
        # Refused AND logged: a forged token is a fact the group
        # (and its operator) deserves a record of.
        audit.record(
            db, actor="system:email-in", action="email_in.rejected",
            group_id=None, entity_type="inbound_address", entity_id="",
            detail={"reason": "unknown token", "from": sender},
        )
        db.commit()
        log.warning("email-in refused: unknown token, from=%s", sender)
        raise AppError("token_invalid", detail="That email-in address is not valid.")
    group = db.get(Group, address.group_id)
    if text.strip():
        chunks = file_service.extract_chunks("text", text.encode("utf-8"))
    elif html.strip():
        chunks = newsletter_service.parse_newsletter_html(html)
    else:
        raise AppError(
            "validation_error", detail="That email carried no readable body."
        )
    for chunk in chunks:
        chunk.setdefault("sender", sender)
    source = source_service.create_source(
        db, actor_label="system:email-in", group=group, kind="email",
        title=subject or f"Email from {sender}",
        granted_scope=f"email-in token {address.token_prefix}",
        chunks=chunks,
    )
    audit.record(
        db, actor="system:email-in", action="email_in.received",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"from": sender, "channel_created_by": address.created_by},
    )
    db.commit()
    return source
