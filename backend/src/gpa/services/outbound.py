"""Outbound webhooks (roadmap B-128).

The group's acts, announced outward: when an event is
published, changed, or cancelled, or a candidate lands in
the review queue, every registered endpoint subscribed to
that kind hears about it — signed, retried, and inspectable.

The shape follows the house's outbox discipline (B-095):

- **Emission is transactional.** :func:`emit` writes the
  delivery row AND its ``webhook_deliver`` job inside the
  caller's transaction (services/jobs.queue), so an act and
  its announcement exist together or not at all. The doors
  that emit are the doors where the acts happen:
  services/events.py (publish / change / cancel),
  services/groups.py (approval mints; the candidate door),
  and services/extraction.py (machine drafts landing).
- **Delivery is a job.** The handler POSTs the payload with
  the same signature scheme as the inbound door
  (HMAC-SHA256 over ``"<timestamp>.<body>"``), so a receiver
  verifies both directions with one piece of code. A
  non-2xx answer or a transport failure records the attempt
  on the delivery row, COMMITS that record (the B-102
  lesson: the chassis rolls back a failed handler's work,
  and the failure record must survive it), and raises — the
  chassis retries with backoff and dead-letters at the
  attempt cap, where the delivery is marked ``dead``.
- **Inspectable.** Every delivery row keeps its status,
  attempt count, last error in plain words, and the
  receiver's HTTP status — "did the form tool hear about
  the move?" is a question the API answers, not a log dive.

The HTTP transport is a registry (like the mail transport
and the Samtal client): production builds a real httpx
client; tests install a MockTransport and no byte ever
leaves the process. The signing secret is shown once at
registration and kept sealed (services/sealing.py).
"""

import hashlib
import hmac
import json
import time
from datetime import timezone

import httpx
from sqlalchemy.orm import Session

from gpa.config import Settings, get_settings
from gpa.errors import AppError
from gpa.models.core import Job
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.integrations import (
    OUTBOUND_EVENT_KINDS,
    OutboundDelivery,
    OutboundWebhook,
)
from gpa.security import new_token
from gpa.services import audit
from gpa.services import jobs as job_service
from gpa.services import sealing

JOB_KIND = "webhook_deliver"
DELIVERY_TIMEOUT_SECONDS = 10.0

# --- Registries (the transport + the settings the job handler
# needs — job handlers receive only db + payload, so the
# process wires its world in here, as everywhere else).

_transport: httpx.BaseTransport | None = None
_settings: Settings | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Install the delivery transport (None = real HTTP)."""
    global _transport
    _transport = transport


def set_settings(settings: Settings | None) -> None:
    """Install the settings the deliver handler unseals with."""
    global _settings
    _settings = settings


def _active_settings() -> Settings:
    return _settings if _settings is not None else get_settings()


# --- Registration -----------------------------------------------------


def create_webhook(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    url: str,
    events: list[str],
) -> tuple[OutboundWebhook, str]:
    """Register one endpoint. Returns (webhook, secret) —
    the secret in the clear only here and in the creation
    response; the row keeps its sealed form."""
    unknown = [kind for kind in events if kind not in OUTBOUND_EVENT_KINDS]
    if unknown:
        raise AppError(
            "validation_error",
            detail=f"Unknown event kinds: {', '.join(unknown)}. "
            f"Choose from: {', '.join(OUTBOUND_EVENT_KINDS)}.",
        )
    if not events:
        raise AppError(
            "validation_error",
            detail="Name at least one event kind for this webhook to hear about.",
        )
    if not url.startswith(("http://", "https://")):
        raise AppError(
            "validation_error",
            detail="A webhook URL must start with http:// or https://.",
        )
    secret = new_token(32)
    webhook = OutboundWebhook(
        group_id=group.id,
        url=url,
        events_json=json.dumps(sorted(set(events))),
        secret_sealed=sealing.seal(settings, secret),
        created_by=actor_label,
    )
    db.add(webhook)
    audit.record(
        db, actor=actor_label, action="outbound_webhook.created",
        group_id=group.id, entity_type="outbound_webhook",
        entity_id=webhook.id,
        detail={"url": url, "events": sorted(set(events))},
    )
    db.commit()
    return webhook, secret


def list_webhooks(db: Session, group: Group) -> list[OutboundWebhook]:
    """A group's outbound endpoints, oldest first."""
    return (
        db.query(OutboundWebhook)
        .filter(OutboundWebhook.group_id == group.id)
        .order_by(OutboundWebhook.created_at, OutboundWebhook.id)
        .all()
    )


def get_webhook(db: Session, group: Group, webhook_id: str) -> OutboundWebhook:
    row = db.get(OutboundWebhook, webhook_id)
    if row is None or row.group_id != group.id:
        raise AppError("not_found", detail="That webhook could not be found.")
    return row


def delete_webhook(
    db: Session, *, actor_label: str, group: Group, webhook_id: str
) -> None:
    """Remove one endpoint. Its undelivered deliveries go
    with it (cascade) — a removed door announces nothing more."""
    row = get_webhook(db, group, webhook_id)
    db.delete(row)
    audit.record(
        db, actor=actor_label, action="outbound_webhook.deleted",
        group_id=group.id, entity_type="outbound_webhook",
        entity_id=webhook_id, detail={},
    )
    db.commit()


def list_deliveries(
    db: Session, group: Group, webhook_id: str
) -> list[OutboundDelivery]:
    """One endpoint's deliveries, newest first — the
    inspectable record."""
    get_webhook(db, group, webhook_id)
    return (
        db.query(OutboundDelivery)
        .filter(OutboundDelivery.webhook_id == webhook_id)
        .order_by(OutboundDelivery.created_at.desc(), OutboundDelivery.id.desc())
        .all()
    )


# --- Emission -----------------------------------------------------------


def emit(db: Session, *, group: Group, kind: str, data: dict) -> list[OutboundDelivery]:
    """Announce one act to every subscribed endpoint.

    Writes delivery rows + their jobs in the CALLER's
    transaction and commits nothing — the act and its
    announcements are one atomic fact. Payloads are small
    and self-describing: the kind, the group, the moment,
    and the act's own facts."""
    deliveries = []
    webhooks = (
        db.query(OutboundWebhook)
        .filter(OutboundWebhook.group_id == group.id, OutboundWebhook.active.is_(True))
        .all()
    )
    for webhook in webhooks:
        try:
            subscribed = set(json.loads(webhook.events_json or "[]"))
        except json.JSONDecodeError:
            subscribed = set()
        if kind not in subscribed:
            continue
        payload = {
            "event": kind,
            "group_id": group.id,
            "occurred_at": now_utc().replace(tzinfo=timezone.utc).isoformat(),
            "data": data,
        }
        delivery = OutboundDelivery(
            webhook_id=webhook.id,
            group_id=group.id,
            event_kind=kind,
            payload_json=json.dumps(payload),
        )
        db.add(delivery)
        db.flush()  # the id exists before the job names it
        job = job_service.queue(
            db, kind=JOB_KIND, payload={"delivery_id": delivery.id}
        )
        delivery.job_id = job.id
        deliveries.append(delivery)
    db.flush()
    return deliveries


def event_data(event, **extra) -> dict:
    """The payload facts for an event act, shared by every
    emitting door so the wire shape is one shape."""
    data = {
        "event_id": event.id,
        "title": event.title,
        "status": event.status,
        "starts_at": event.starts_at.replace(tzinfo=timezone.utc).isoformat(),
        "ends_at": event.ends_at.replace(tzinfo=timezone.utc).isoformat(),
        "place": event.place_name,
    }
    data.update(extra)
    return data


# --- Delivery -------------------------------------------------------------


def _signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    """The outbound signature — the inbound scheme, mirrored:
    HMAC-SHA256 over ``"<timestamp>.<raw body>"``, hex."""
    return hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()


def handle_deliver(db: Session, payload: dict) -> None:
    """The webhook_deliver job handler.

    One attempt per run. Success marks the delivery sent and
    returns (the chassis commits). Failure records the
    attempt + plain-words error, commits THAT record, and
    raises so the chassis retries or dead-letters — at the
    cap the delivery is marked dead in the same record.
    """
    delivery = db.get(OutboundDelivery, payload.get("delivery_id", ""))
    if delivery is None:
        return  # The endpoint (and its deliveries) are gone.
    webhook = db.get(OutboundWebhook, delivery.webhook_id)
    if webhook is None:
        return
    settings = _active_settings()
    secret = sealing.open_sealed(settings, webhook.secret_sealed)
    raw_body = delivery.payload_json.encode("utf-8")
    timestamp = str(int(time.time()))
    delivery.attempts += 1
    try:
        with httpx.Client(
            transport=_transport, timeout=DELIVERY_TIMEOUT_SECONDS
        ) as client:
            response = client.post(
                webhook.url,
                content=raw_body,
                headers={
                    "content-type": "application/json",
                    "x-gpa-event": delivery.event_kind,
                    "x-gpa-delivery": delivery.id,
                    "x-gpa-timestamp": timestamp,
                    "x-gpa-signature": _signature(secret, timestamp, raw_body),
                },
            )
        delivery.response_status = response.status_code
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"the receiver answered HTTP {response.status_code}"
            )
    except Exception as err:
        delivery.last_error = (
            err.detail if isinstance(err, AppError) else
            f"Delivery failed: {err}"[:500]
        )
        job = db.get(Job, delivery.job_id) if delivery.job_id else None
        final = (
            job is not None and job.attempts + 1 >= job.max_attempts
        )
        delivery.status = "dead" if final else "failed"
        db.commit()  # The failure record survives the raise (B-102).
        raise RuntimeError(delivery.last_error) from err
    delivery.status = "sent"
    delivery.delivered_at = now_utc()
    delivery.last_error = ""
    # The chassis commits on success.


def register_handlers() -> None:
    """Offer the deliver handler to the job chassis (B-008)."""
    job_service.register_handler(JOB_KIND, handle_deliver)


register_handlers()
