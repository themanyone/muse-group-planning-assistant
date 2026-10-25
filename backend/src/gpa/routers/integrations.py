"""Integration routes (roadmap Phase 11, B-123 → B-128).

The doors onto the bridge: account links and live sync
(B-123), the Samtal calendar return path (B-124), the
integration framework (B-127), the inbound webhook endpoints
(B-126), and outbound webhooks with their deliveries
(B-128). Every handler authorises through gpa.deps like
every other router and defers every rule to the services —
routers here shape responses and nothing more.

The webhook RECEIVER is the one unauthenticated door in this
file, by design and by precedent (B-030's email-in): its
credential is the token in its path plus the HMAC signature
on its body, verified inside services/webhooks_in.py before
a single chunk is written.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from gpa.db import get_db
from gpa.deps import GroupContext, Principal, require_group_role, require_scope
from gpa.models.identity import User
from gpa.schemas.integrations import (
    AccountLinkCreateRequest,
    AccountLinkResponse,
    EmbedOriginsRequest,
    EmbedOriginsResponse,
    GoogleSyncResponse,
    HealthBoardEntryResponse,
    ReconcileResponse,
    IntegrationCreateRequest,
    IntegrationResponse,
    IntegrationUpdateRequest,
    LiveSyncResponse,
    OutboundDeliveryResponse,
    OutboundWebhookCreateRequest,
    OutboundWebhookResponse,
    SamtalCalendarSyncResponse,
    SpeakerMember,
    SpeakerResponse,
    WebhookDeliveryResponse,
    WebhookEndpointCreateRequest,
    WebhookEndpointResponse,
)
from gpa.services import livechat as livechat_service
from gpa.services import sources as source_service

router = APIRouter(prefix="/api/v1/groups", tags=["integrations"])

# The token-carrying receiver door (B-126 mounts its route
# here): no group in its path, because the token IS the
# address — the same shape as B-030's email-in router.
webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["integrations"])


def _link_response(db: Session, link) -> AccountLinkResponse:
    user = db.get(User, link.user_id)
    return AccountLinkResponse(
        id=link.id, group_id=link.group_id,
        connector_kind=link.connector_kind,
        external_user_id=link.external_user_id,
        external_name=link.external_name,
        user_id=link.user_id,
        member_name=user.name if user is not None else "",
        created_at=link.created_at.isoformat(),
    )


# --- Account links (B-123) ---------------------------------------------
# Identity administration is an organiser act: a link decides
# whose words belong to whom, and that is never a member's
# self-service claim.


@router.post(
    "/{group_id}/account-links",
    response_model=AccountLinkResponse,
    status_code=201,
)
def create_account_link(
    body: AccountLinkCreateRequest,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Link one outside identity to one member of this group.

    The link keys on the outside id — the response shows the
    member it now resolves to. A name is never a key: nothing
    in this API can link by name."""
    link = livechat_service.create_link(
        db, actor_label=principal.label, group=context.group,
        connector_kind=body.connector_kind,
        external_user_id=body.external_user_id,
        external_name=body.external_name,
        user_id=body.user_id,
    )
    return _link_response(db, link)


@router.get(
    "/{group_id}/account-links", response_model=list[AccountLinkResponse]
)
def list_account_links(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """Every account link in the group (organiser+)."""
    return [
        _link_response(db, link)
        for link in livechat_service.list_links(db, context.group)
    ]


@router.delete("/{group_id}/account-links/{link_id}", status_code=204)
def delete_account_link(
    link_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Remove one link (organiser+). The speaker's chunks
    stay; they simply stop resolving to the member."""
    livechat_service.delete_link(
        db, actor_label=principal.label, group=context.group, link_id=link_id
    )
    return None


# --- Live sync + speakers (B-123) ---------------------------------------


@router.post(
    "/{group_id}/sources/{source_id}/sync", response_model=LiveSyncResponse
)
def sync_live_source(
    source_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Poll a live source's connector for new messages.

    New messages land as chunks, in order, under the
    permission record the source was registered with. A
    connector that cannot answer — no live Samtal configured,
    Messenger's ungranted access — refuses in plain words and
    the refusal is recorded on the source's sync state."""
    source = source_service.get_source(db, context.group, source_id)
    result = livechat_service.sync_live_source(
        db, actor_label=principal.label, group=context.group, source=source
    )
    return LiveSyncResponse(
        source_id=source.id, added=result["added"], cursor=result["cursor"],
        status=source.status, stage=source.stage,
    )


@router.get(
    "/{group_id}/sources/{source_id}/speakers",
    response_model=list[SpeakerResponse],
)
def list_speakers(
    source_id: str,
    context: GroupContext = Depends(require_group_role("member")),
    db: Session = Depends(get_db),
):
    """A live source's speakers, resolved through account
    links (member+). Unlinked speakers answer guest=True —
    even when their name is a member's name. That is the
    identity law, and this response is where it shows."""
    source = source_service.get_source(db, context.group, source_id)
    return [
        SpeakerResponse(
            external_id=row["external_id"],
            name=row["name"],
            member=(
                SpeakerMember(**row["member"])
                if row["member"] is not None
                else None
            ),
            guest=row["guest"],
        )
        for row in livechat_service.speakers_of(db, context.group, source)
    ]


# --- The Samtal calendar return path (B-124) ---------------------------


@router.post(
    "/{group_id}/sources/{source_id}/calendar-sync",
    response_model=SamtalCalendarSyncResponse,
)
def sync_samtal_calendar(
    source_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Run the calendar return path for a Samtal source.

    Out: published events appear on the hall's calendar.
    In: Samtal-side edits arrive as CHANGE CANDIDATES in the
    review queue — never as silent writes. The event itself
    does not move until a person approves the candidate."""
    from gpa.services import samtal_calendar as calendar_service

    source = source_service.get_source(db, context.group, source_id)
    result = calendar_service.sync_calendar(
        db, actor_label=principal.label, group=context.group, source=source
    )
    return SamtalCalendarSyncResponse(
        source_id=source.id,
        pushed=result["pushed"],
        candidates_created=result["candidates_created"],
    )


# --- Inbound webhook endpoints (B-126) -----------------------------------


def _endpoint_response(endpoint, *, token=None, secret=None) -> WebhookEndpointResponse:
    return WebhookEndpointResponse(
        id=endpoint.id, group_id=endpoint.group_id,
        source_id=endpoint.source_id, token_prefix=endpoint.token_prefix,
        created_at=endpoint.created_at.isoformat(),
        revoked_at=endpoint.revoked_at.isoformat() if endpoint.revoked_at else None,
        token=token, signing_secret=secret,
        url_path=f"/api/v1/webhooks/source/{token}" if token else None,
    )


@router.post(
    "/{group_id}/webhook-endpoints",
    response_model=WebhookEndpointResponse,
    status_code=201,
)
def create_webhook_endpoint(
    body: WebhookEndpointCreateRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Open a signed webhook door onto one source (organiser+).

    The response carries the full URL token and the signing
    secret ONCE — the token is stored hashed and the secret
    is derived, never stored, so neither can be read back."""
    from gpa.services import webhooks_in as webhook_service

    endpoint, token, secret = webhook_service.create_endpoint(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        source_id=body.source_id,
    )
    return _endpoint_response(endpoint, token=token, secret=secret)


@router.get(
    "/{group_id}/webhook-endpoints",
    response_model=list[WebhookEndpointResponse],
)
def list_webhook_endpoints(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """A group's webhook endpoints (organiser+) — prefixes
    only; the secrets were shown once, at creation."""
    from gpa.services import webhooks_in as webhook_service

    return [
        _endpoint_response(endpoint)
        for endpoint in webhook_service.list_endpoints(db, context.group)
    ]


@router.delete("/{group_id}/webhook-endpoints/{endpoint_id}", status_code=204)
def revoke_webhook_endpoint(
    endpoint_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Revoke one endpoint (organiser+): its URL dies at
    once, and every later delivery is refused and logged."""
    from gpa.services import webhooks_in as webhook_service

    webhook_service.revoke_endpoint(
        db, actor_label=principal.label, group=context.group,
        endpoint_id=endpoint_id,
    )
    return None


# --- The integration framework (B-127) -----------------------------------
# Credentials live here, so the write doors are admin doors;
# the read doors are organiser doors, and what they read is
# the record — kind, key NAMES, health — never a secret.


def _integration_response(request: Request, db: Session, row) -> IntegrationResponse:
    from gpa.services import integrations as integration_service

    return IntegrationResponse(
        id=row.id, group_id=row.group_id, kind=row.kind, name=row.name,
        config_keys=integration_service.config_keys(
            request.app.state.settings, row
        ),
        status=row.status, last_error=row.last_error,
        last_checked_at=row.last_checked_at.isoformat() if row.last_checked_at else None,
        last_success_at=row.last_success_at.isoformat() if row.last_success_at else None,
        created_at=row.created_at.isoformat(),
    )


@router.post(
    "/{group_id}/integrations",
    response_model=IntegrationResponse,
    status_code=201,
)
def create_integration(
    body: IntegrationCreateRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Register one integration (admin+). Its config is
    sealed before it is stored; the response names the
    config keys held and shows no value, ever."""
    from gpa.services import integrations as integration_service

    row = integration_service.create_integration(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        kind=body.kind, name=body.name, config=body.config,
    )
    if body.kind in ("facebook_events", "google_calendar"):
        # B-137: connecting an outside calendar anchors the
        # group's nightly reconciliation chain.
        from gpa.services import reconcile as reconcile_service

        reconcile_service.ensure_scheduled(db, group=context.group)
        db.commit()
    return _integration_response(request, db, row)


@router.get(
    "/{group_id}/integrations", response_model=list[IntegrationResponse]
)
def list_integrations(
    request: Request,
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """A group's integrations with their health (organiser+)."""
    from gpa.services import integrations as integration_service

    return [
        _integration_response(request, db, row)
        for row in integration_service.list_integrations(db, context.group)
    ]


@router.get(
    "/{group_id}/integrations/health-board",
    response_model=list[HealthBoardEntryResponse],
)
def integration_health_board(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """The health board (B-136, organiser+): every integration
    with its recorded state and ONE next step, in words. This
    route is registered BEFORE /{integration_id} on purpose —
    "health-board" is a board, not an integration id."""
    from gpa.services import health_board

    return [
        HealthBoardEntryResponse(**entry)
        for entry in health_board.board(db, group=context.group)
    ]


@router.post(
    "/{group_id}/integrations/reconcile",
    response_model=ReconcileResponse,
)
def reconcile_now(
    request: Request,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Run reconciliation NOW (admin+, B-137): compare the
    group's outside copies, notify the organisers of any
    drift, return the drift list. Registered before
    /{integration_id} routes for the same reason as the
    health board. Running it also anchors the nightly chain
    if it is not anchored yet."""
    from gpa.services import reconcile as reconcile_service

    drift = reconcile_service.reconcile_group(
        db, request.app.state.settings,
        group=context.group, actor=principal.label,
    )
    reconcile_service.ensure_scheduled(db, group=context.group)
    db.commit()
    return ReconcileResponse(drift=drift)


@router.get(
    "/{group_id}/integrations/{integration_id}",
    response_model=IntegrationResponse,
)
def read_integration(
    integration_id: str,
    request: Request,
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """One integration's record (organiser+)."""
    from gpa.services import integrations as integration_service

    row = integration_service.get_integration(db, context.group, integration_id)
    return _integration_response(request, db, row)


@router.patch(
    "/{group_id}/integrations/{integration_id}",
    response_model=IntegrationResponse,
)
def update_integration(
    integration_id: str,
    body: IntegrationUpdateRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Rename and/or re-configure one integration (admin+).
    A present config replaces the old one whole and resets
    the health verdict to unconfigured."""
    from gpa.services import integrations as integration_service

    row = integration_service.update_integration(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        integration_id=integration_id, name=body.name, config=body.config,
    )
    return _integration_response(request, db, row)


@router.delete("/{group_id}/integrations/{integration_id}", status_code=204)
def delete_integration(
    integration_id: str,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Remove one integration and its sealed config (admin+)."""
    from gpa.services import integrations as integration_service

    integration_service.delete_integration(
        db, actor_label=principal.label, group=context.group,
        integration_id=integration_id,
    )
    return None


@router.post(
    "/{group_id}/integrations/{integration_id}/health",
    response_model=IntegrationResponse,
)
def check_integration_health(
    integration_id: str,
    request: Request,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Run the health check and record its verdict (admin+):
    status, last success, and — when it fails — the error in
    plain words, on the record where the list shows it."""
    from gpa.services import integrations as integration_service

    row = integration_service.check_health(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        integration_id=integration_id,
    )
    return _integration_response(request, db, row)


# --- Google Calendar: connect + sync (B-132) ---------------------------
# Connect is the credential act (admin); sync is the outward
# press (organiser + publish scope), like every human-
# triggered outward run in this phase.


@router.post(
    "/{group_id}/integrations/{integration_id}/google-connect",
    response_model=IntegrationResponse,
)
def connect_google(
    integration_id: str,
    request: Request,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Verify a Google Calendar connection's credentials
    against the client boundary and record the verdict on
    the row (admin+). The sealed token is what a deployment's
    OAuth consent flow issued; this door proves it answers
    before any sync trusts it."""
    from gpa.services import google_calendar as google_service

    row = google_service.connect(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        integration_id=integration_id,
    )
    return _integration_response(request, db, row)


@router.post(
    "/{group_id}/integrations/{integration_id}/google-sync",
    response_model=GoogleSyncResponse,
)
def sync_google(
    integration_id: str,
    request: Request,
    context: GroupContext = Depends(require_group_role("organiser")),
    principal: Principal = Depends(require_scope("publish")),
    db: Session = Depends(get_db),
):
    """Run one Google Calendar sync (organiser+): published
    events upsert into the chosen calendar, cancellations
    propagate as cancellations. ONE-WAY OUT — edits made on
    Google are never read back into events here."""
    from gpa.services import google_calendar as google_service

    counts = google_service.sync_group(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        integration_id=integration_id,
    )
    return GoogleSyncResponse(integration_id=integration_id, **counts)


# --- Embed origins (B-135) ----------------------------------------------
# The register of origins allowed to embed the group's
# public page. Reading it is an organiser read; replacing it
# is an admin act — it decides whose websites may frame the
# group's face.


@router.get(
    "/{group_id}/embed-origins", response_model=EmbedOriginsResponse
)
def list_embed_origins(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """The group's registered embed origins (organiser+)."""
    from gpa.services import embed as embed_service

    return EmbedOriginsResponse(origins=embed_service.origins_of(context.group))


@router.put(
    "/{group_id}/embed-origins", response_model=EmbedOriginsResponse
)
def set_embed_origins(
    body: EmbedOriginsRequest,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Replace the embed register whole (admin+). An entry
    that is not an origin (a path, a bare word) is refused in
    plain words and nothing is stored."""
    from gpa.services import embed as embed_service

    origins = embed_service.set_origins(
        db, actor_label=principal.label, group=context.group,
        origins=body.origins,
    )
    return EmbedOriginsResponse(origins=origins)


# --- Outbound webhooks + deliveries (B-128) ------------------------------
# Registration is an admin act (it points the group's
# announcements at an outside address); the delivery record
# is an organiser read — it is the answer to "did they hear?".


def _outbound_response(webhook, *, secret=None) -> OutboundWebhookResponse:
    import json as _json

    try:
        events = _json.loads(webhook.events_json or "[]")
    except _json.JSONDecodeError:
        events = []
    return OutboundWebhookResponse(
        id=webhook.id, group_id=webhook.group_id, url=webhook.url,
        events=events, active=webhook.active,
        created_at=webhook.created_at.isoformat(),
        signing_secret=secret,
    )


@router.post(
    "/{group_id}/outbound-webhooks",
    response_model=OutboundWebhookResponse,
    status_code=201,
)
def create_outbound_webhook(
    body: OutboundWebhookCreateRequest,
    request: Request,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Register one outbound endpoint (admin+). The response
    carries the signing secret ONCE; deliveries are signed
    with it and only its sealed form is kept."""
    from gpa.services import outbound as outbound_service

    webhook, secret = outbound_service.create_webhook(
        db, request.app.state.settings,
        actor_label=principal.label, group=context.group,
        url=body.url, events=body.events,
    )
    return _outbound_response(webhook, secret=secret)


@router.get(
    "/{group_id}/outbound-webhooks",
    response_model=list[OutboundWebhookResponse],
)
def list_outbound_webhooks(
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """A group's outbound endpoints (organiser+)."""
    from gpa.services import outbound as outbound_service

    return [
        _outbound_response(webhook)
        for webhook in outbound_service.list_webhooks(db, context.group)
    ]


@router.delete("/{group_id}/outbound-webhooks/{webhook_id}", status_code=204)
def delete_outbound_webhook(
    webhook_id: str,
    context: GroupContext = Depends(require_group_role("admin")),
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Remove one endpoint (admin+); its pending deliveries
    go with it — a removed door announces nothing more."""
    from gpa.services import outbound as outbound_service

    outbound_service.delete_webhook(
        db, actor_label=principal.label, group=context.group,
        webhook_id=webhook_id,
    )
    return None


@router.get(
    "/{group_id}/outbound-webhooks/{webhook_id}/deliveries",
    response_model=list[OutboundDeliveryResponse],
)
def list_outbound_deliveries(
    webhook_id: str,
    context: GroupContext = Depends(require_group_role("organiser")),
    db: Session = Depends(get_db),
):
    """One endpoint's deliveries, newest first (organiser+):
    status, attempts, the receiver's HTTP answer, and the
    last error in plain words."""
    from gpa.services import outbound as outbound_service

    return [
        OutboundDeliveryResponse(
            id=d.id, webhook_id=d.webhook_id, group_id=d.group_id,
            event_kind=d.event_kind, status=d.status, attempts=d.attempts,
            last_error=d.last_error, response_status=d.response_status,
            created_at=d.created_at.isoformat(),
            delivered_at=d.delivered_at.isoformat() if d.delivered_at else None,
        )
        for d in outbound_service.list_deliveries(db, context.group, webhook_id)
    ]


@webhook_router.post(
    "/source/{token}", response_model=WebhookDeliveryResponse, status_code=201
)
async def receive_webhook_delivery(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """The receiver: one signed delivery → chunks on its source.

    The credential is the token in the path plus the
    ``x-gpa-signature`` HMAC over ``x-gpa-timestamp`` and the
    raw body. Verification — token, window, signature,
    replay receipt — happens inside services/webhooks_in.py
    before a single chunk is written; refusals are logged
    there too."""
    from gpa.services import webhooks_in as webhook_service

    raw_body = await request.body()
    source, added = webhook_service.receive_delivery(
        db, request.app.state.settings,
        token=token, raw_body=raw_body,
        signature=request.headers.get("x-gpa-signature"),
        timestamp=request.headers.get("x-gpa-timestamp"),
    )
    return WebhookDeliveryResponse(source_id=source.id, chunks_added=added)
