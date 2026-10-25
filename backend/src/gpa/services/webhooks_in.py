"""Generic inbound webhook sources (roadmap B-126).

Any tool that can POST JSON — a form, a Zap, a script — can
feed a source here, through a door with three locks:

- **The token in the URL** names the endpoint. It is shown
  once at creation and stored only as a hash (the B-014 /
  B-030 discipline): a leaked database yields no usable URL.
- **The HMAC signature** proves the sender holds the
  signing secret. The secret is shown once at creation and
  is NEVER stored — it is *derived* from the server secret
  and the endpoint's id, so the server can always recompute
  it for verification while the database holds nothing a
  signature could be forged with. (Rotating the server
  secret rotates every webhook secret with it; the endpoint
  list is where an admin learns that, in the runbook sense —
  the trade is deliberate and recorded here.)
- **The replay window + receipt**: the signature covers a
  timestamp; a delivery older than five minutes is refused,
  and every accepted signature is receipted — the same
  signed payload arriving twice is refused the second time,
  window or no window.

Deliveries append their messages to the endpoint's bound
source as chunks, in order — the payload is archived as
material, exactly like every other ingest. Forged or
replayed deliveries are refused AND logged (the B-030
pattern): an audit row with no group for unknown tokens,
and nothing is ever appended by a refused delivery.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.integrations import WebhookEndpoint, WebhookReceipt
from gpa.models.sources import Source
from gpa.security import hash_secret, new_token
from gpa.services import audit
from gpa.services import sources as source_service

log = logging.getLogger("gpa.webhooks_in")

# How old a signed delivery may be and still be believed.
REPLAY_WINDOW_SECONDS = 300


def derive_signing_secret(settings: Settings, endpoint_id: str) -> str:
    """The endpoint's signing secret — derived, never stored.

    HMAC(server secret, purpose + endpoint id): unique per
    endpoint, unguessable without the server secret, and
    recomputable at verification time from data the server
    already holds. The value returned at creation is the
    only time it leaves the server."""
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        f"gpa-webhook-in:{endpoint_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signature_for(secret: str, timestamp: str, raw_body: bytes) -> str:
    """The signature a sender computes: HMAC-SHA256 over
    ``"<timestamp>.<raw body>"``, hex — the timestamp is
    inside the signed text so it cannot be refreshed by a
    replayer without the secret."""
    return hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()


def create_endpoint(
    db: Session, settings: Settings, *, actor_label: str, group: Group, source_id: str
) -> tuple[WebhookEndpoint, str, str]:
    """Open a webhook door onto one source of this group.

    Returns (endpoint, token, signing_secret) — the token
    and the secret exist in the clear only in this return
    value and the creation response built from it."""
    source = source_service.get_source(db, group, source_id)
    token = new_token(32)
    endpoint = WebhookEndpoint(
        group_id=group.id,
        source_id=source.id,
        token_hash=hash_secret(token),
        token_prefix=token[:8],
        created_by=actor_label,
    )
    db.add(endpoint)
    db.flush()  # the id exists before the secret derives from it
    secret = derive_signing_secret(settings, endpoint.id)
    audit.record(
        db, actor=actor_label, action="webhook_endpoint.created",
        group_id=group.id, entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        detail={"source_id": source.id, "token_prefix": endpoint.token_prefix},
    )
    db.commit()
    return endpoint, token, secret


def list_endpoints(db: Session, group: Group) -> list[WebhookEndpoint]:
    """A group's webhook endpoints, oldest first."""
    return (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.group_id == group.id)
        .order_by(WebhookEndpoint.created_at, WebhookEndpoint.id)
        .all()
    )


def revoke_endpoint(
    db: Session, *, actor_label: str, group: Group, endpoint_id: str
) -> None:
    """Revoke one endpoint: its URL dies at once and totally
    (the B-053 feed rule). Revocation is a timestamp, checked
    on every delivery."""
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.group_id != group.id:
        raise AppError("not_found", detail="That webhook endpoint could not be found.")
    if endpoint.revoked_at is None:
        from gpa.models.identity import now_utc

        endpoint.revoked_at = now_utc()
        audit.record(
            db, actor=actor_label, action="webhook_endpoint.revoked",
            group_id=group.id, entity_type="webhook_endpoint",
            entity_id=endpoint.id, detail={},
        )
        db.commit()


def _refuse_replay(db: Session, *, group: Group, endpoint: WebhookEndpoint):
    """Refuse a replay: log it, then raise the 409.

    Exactly-once has two doors and both end here — the
    sequential replay the receipt SELECT finds, and the
    concurrent twin whose commit loses the receipt row's
    UNIQUE(endpoint, signature) race (see receive_delivery).
    The audit row commits BEFORE the raise, like every
    refusal (the B-030 lesson)."""
    audit.record(
        db, actor="system:webhook-in", action="webhook_in.rejected",
        group_id=group.id, entity_type="webhook_endpoint",
        entity_id=endpoint.id, detail={"reason": "replay"},
    )
    db.commit()
    raise AppError(
        "conflict",
        detail="That delivery was already received — a replay changes nothing.",
    )


def _resolve(db: Session, token: str) -> WebhookEndpoint | None:
    return (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.token_hash == hash_secret(token))
        .one_or_none()
    )


def _refuse(db: Session, *, group_id: str | None, reason: str, detail: str):
    """Log a refused delivery, then raise its refusal.

    The audit row is committed BEFORE the raise — a refusal
    that rolled back with its request would leave no trace,
    and an attacker's probes are exactly the trace an
    operator needs (the B-030 lesson)."""
    audit.record(
        db, actor="system:webhook-in", action="webhook_in.rejected",
        group_id=group_id, entity_type="webhook_endpoint", entity_id="",
        detail={"reason": reason},
    )
    db.commit()
    log.warning("webhook-in refused: %s", reason)
    raise AppError("token_invalid", detail=detail)


def receive_delivery(
    db: Session,
    settings: Settings,
    *,
    token: str,
    raw_body: bytes,
    signature: str | None,
    timestamp: str | None,
) -> tuple[Source, int]:
    """Verify one delivery and archive its messages as chunks.

    Returns (source, chunks_added). Every refusal path goes
    through :func:`_refuse` — logged, and nothing appended.
    """
    endpoint = _resolve(db, token)
    if endpoint is None or endpoint.revoked_at is not None:
        _refuse(
            db, group_id=None, reason="unknown or revoked token",
            detail="That webhook address is not valid.",
        )
    group = db.get(Group, endpoint.group_id)
    source = db.get(Source, endpoint.source_id)

    # The window: the signed timestamp must be near now.
    try:
        stamp = int(timestamp or "")
    except ValueError:
        stamp = 0
    if abs(int(time.time()) - stamp) > REPLAY_WINDOW_SECONDS:
        _refuse(
            db, group_id=group.id, reason="outside the replay window",
            detail="That delivery is too old to accept — sign a fresh one.",
        )
    # The signature: over the timestamp and the exact bytes.
    secret = derive_signing_secret(settings, endpoint.id)
    expected = signature_for(secret, str(timestamp), raw_body)
    if not signature or not hmac.compare_digest(expected, signature):
        _refuse(
            db, group_id=group.id, reason="bad signature",
            detail="That delivery's signature does not check out.",
        )
    # The receipt: this exact signed payload, once only.
    already = (
        db.query(WebhookReceipt)
        .filter(
            WebhookReceipt.endpoint_id == endpoint.id,
            WebhookReceipt.signature == signature,
        )
        .one_or_none()
    )
    if already is not None:
        _refuse_replay(db, group=group, endpoint=endpoint)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AppError(
            "validation_error", detail="That delivery's body is not JSON."
        ) from None
    chunks = _chunks_from_payload(payload)
    added = source_service.append_chunks(db, source=source, chunks=chunks)
    db.add(WebhookReceipt(endpoint_id=endpoint.id, signature=signature))
    audit.record(
        db, actor="system:webhook-in", action="webhook_in.received",
        group_id=group.id, entity_type="source", entity_id=source.id,
        detail={"endpoint_id": endpoint.id, "chunks": added},
    )
    try:
        db.commit()
    except IntegrityError:
        # The concurrent twin of the replay check above: two
        # identical deliveries in flight at once can BOTH pass
        # the receipt SELECT before either commits — but only
        # one can win the receipt row's UNIQUE(endpoint,
        # signature) constraint. The loser's commit fails here,
        # and exactly-once means that must read as the same
        # 409 a sequential replay gets, never a 500: roll the
        # dead transaction back (its chunks go with it, so
        # nothing is archived twice) and refuse it as the
        # replay it is. (Before this catch existed, the
        # constraint fired but surfaced as an unhandled 500 —
        # the guarantee held, its answer was wrong.)
        db.rollback()
        _refuse_replay(db, group=group, endpoint=endpoint)
    return source, added


def _chunks_from_payload(payload) -> list[dict]:
    """A delivery's messages as chunk dicts.

    Two shapes are accepted: ``{"messages": [{sender, text,
    sent_at?, sender_ref?}, …]}`` (a chat-shaped feed) and
    ``{"text": "…"}`` (a single note). Anything else is a
    validation error in plain words — a sender who cannot
    say what they sent gets told so, and nothing is archived
    from a body we could not read."""
    if not isinstance(payload, dict):
        raise AppError("validation_error", detail="A delivery must be a JSON object.")
    messages = payload.get("messages")
    if messages is None and isinstance(payload.get("text"), str):
        messages = [{"text": payload["text"], "sender": payload.get("sender")}]
    if not isinstance(messages, list) or not messages:
        raise AppError(
            "validation_error",
            detail="A delivery must carry a non-empty 'messages' list (or a 'text').",
        )
    chunks = []
    for message in messages:
        if not isinstance(message, dict) or not str(message.get("text", "")).strip():
            raise AppError(
                "validation_error",
                detail="Every delivered message needs text.",
            )
        sent_at = None
        raw_stamp = message.get("sent_at")
        if raw_stamp:
            try:
                parsed = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00"))
                sent_at = (
                    parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    if parsed.tzinfo is not None
                    else parsed
                )
            except ValueError:
                sent_at = None
        chunks.append(
            {
                "text": str(message["text"]),
                "sender": message.get("sender"),
                "sender_ref": message.get("sender_ref"),
                "sent_at": sent_at,
                "timestamp_text": str(raw_stamp) if raw_stamp else None,
            }
        )
    return chunks
