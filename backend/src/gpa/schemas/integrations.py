"""Integration schemas (roadmap Phase 11, B-123 → B-128).

The wire shapes of the bridge: account links, live syncs,
speakers, the integration framework, the webhook doors, and
outbound deliveries. Two disciplines run through all of
them, inherited from the phases before: secrets appear in a
response exactly once (at creation, beside a field that says
so) or never; and health/status fields carry plain words a
person can act on, because these schemas are what an admin
reads when something outside this system breaks.
"""

from datetime import datetime

from pydantic import BaseModel, Field


# --- Account links (B-123) -------------------------------------------


class AccountLinkCreateRequest(BaseModel):
    """Link one outside identity to one member (organiser act).

    ``external_user_id`` is the speaker's id in the outside
    system — the key. ``external_name`` is the name that
    system showed for them: evidence for the human making
    the link, never a matching key.
    """

    connector_kind: str = Field(min_length=1, max_length=48)
    external_user_id: str = Field(min_length=1, max_length=240)
    external_name: str = Field(default="", max_length=160)
    user_id: str = Field(min_length=1, max_length=36)


class AccountLinkResponse(BaseModel):
    id: str
    group_id: str
    connector_kind: str
    external_user_id: str
    external_name: str
    user_id: str
    member_name: str = ""
    created_at: str


class SpeakerMember(BaseModel):
    user_id: str
    name: str


class SpeakerResponse(BaseModel):
    """One speaker in a live source, resolved (B-123).

    ``guest`` is True exactly when no account link resolves
    this speaker — including when their display name matches
    a member's name letter for letter. That is the identity
    law, rendered."""

    external_id: str | None = None
    name: str | None = None
    member: SpeakerMember | None = None
    guest: bool = True


class LiveSyncResponse(BaseModel):
    """The result of one poll of a live source (B-123)."""

    source_id: str
    added: int
    cursor: str
    status: str
    stage: str


class SamtalCalendarSyncResponse(BaseModel):
    """One run of the Samtal calendar return path (B-124):
    how many events were pushed out, and how many Samtal-side
    edits arrived as change candidates for the queue."""

    source_id: str
    pushed: int
    candidates_created: int


# --- The integration framework (B-127) -------------------------------


class IntegrationCreateRequest(BaseModel):
    """Register one integration (admin act).

    ``config`` is the connector's settings as a JSON object —
    tokens and keys inside it are sealed before they touch
    the database and are never read back out through the API
    (responses carry only the NAMES of the config keys).
    """

    kind: str = Field(min_length=1, max_length=48)
    name: str = Field(default="", max_length=160)
    config: dict = Field(default_factory=dict)


class IntegrationUpdateRequest(BaseModel):
    """Replace an integration's name and/or config (admin act).

    A present ``config`` replaces the sealed config whole —
    partial secret updates are how stale half-rotated keys
    happen, so the framework does not offer them."""

    name: str | None = Field(default=None, max_length=160)
    config: dict | None = None


class IntegrationResponse(BaseModel):
    id: str
    group_id: str
    kind: str
    name: str
    # The NAMES of the config keys held — never their values.
    config_keys: list[str] = Field(default_factory=list)
    status: str
    last_error: str = ""
    last_checked_at: str | None = None
    last_success_at: str | None = None
    created_at: str


# --- Google Calendar sync (B-132) -------------------------------------


class GoogleSyncResponse(BaseModel):
    """One Google sync run's counts: copies created, copies
    brought back into agreement, cancellations propagated,
    and copies already current (left alone)."""

    integration_id: str
    pushed: int
    updated: int
    cancelled: int
    unchanged: int


class GooglePullResponse(BaseModel):
    """One Google inbound pull's counts (B-159): how many
    Google-side edits were drafted as change candidates, out
    of how many linked copies were compared."""

    integration_id: str
    candidates_created: int
    checked: int


# --- Reconciliation (B-137) --------------------------------------------


class DriftEntryResponse(BaseModel):
    """One drifted outside copy: which event, which door, and
    how it drifted — "differs" (edited outside) or "missing"
    (deleted outside). Reconciliation reports; it never
    repairs."""

    event_id: str
    title: str
    connector: str
    kind: str
    external_id: str


class ReconcileResponse(BaseModel):
    drift: list[DriftEntryResponse] = Field(default_factory=list)


# --- Integration health board (B-136) ---------------------------------


class HealthBoardEntryResponse(BaseModel):
    """One integration on the health board: its recorded
    verdict and the single next step, in words."""

    integration_id: str
    kind: str
    name: str = ""
    status: str
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str = ""
    advice: str


# --- Embed origins (B-135) --------------------------------------------


class EmbedOriginsRequest(BaseModel):
    """Replace a group's embed register whole (admin act):
    the web origins allowed to embed its public page. An
    origin is a scheme + host — never a path."""

    origins: list[str] = Field(default_factory=list, max_length=50)


class EmbedOriginsResponse(BaseModel):
    origins: list[str] = Field(default_factory=list)


# --- Inbound webhook endpoints (B-126) -------------------------------


class WebhookEndpointCreateRequest(BaseModel):
    """Open a webhook door onto one source (organiser act)."""

    source_id: str = Field(min_length=1, max_length=36)


class WebhookEndpointResponse(BaseModel):
    id: str
    group_id: str
    source_id: str
    token_prefix: str
    created_at: str
    revoked_at: str | None = None
    # Creation-only fields: the full token (for the URL) and
    # the signing secret appear in THIS response and never
    # again — only a hash of the token is stored, and the
    # secret is derived, not stored at all.
    token: str | None = None
    signing_secret: str | None = None
    url_path: str | None = None


class WebhookDeliveryResponse(BaseModel):
    """One accepted inbound delivery (B-126): the source it
    fed and how many chunks it archived."""

    source_id: str
    chunks_added: int


# --- Outbound webhooks + deliveries (B-128) --------------------------


class OutboundWebhookCreateRequest(BaseModel):
    """Register one outbound endpoint (admin act)."""

    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(default_factory=list)


class OutboundWebhookResponse(BaseModel):
    id: str
    group_id: str
    url: str
    events: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: str
    # Creation-only: the signing secret deliveries are signed
    # with. Shown once; only its sealed form is kept.
    signing_secret: str | None = None


class OutboundDeliveryResponse(BaseModel):
    id: str
    webhook_id: str
    group_id: str
    event_kind: str
    status: str
    attempts: int
    last_error: str = ""
    response_status: int | None = None
    created_at: str
    delivered_at: str | None = None
