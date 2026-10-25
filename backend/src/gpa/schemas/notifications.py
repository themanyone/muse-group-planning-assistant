"""Notification shapes (Phase 8, B-095 → B-103).

The inbox, preferences, failures, invites, and digests as the
API shows them. A NotificationResponse is the ONE row both
channels read (B-101): ``sent`` here means the outbox job
delivered it — the same truth the email carried.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    """One notification: one row, both channels, full state."""

    id: str
    group_id: str | None
    event_id: str | None
    recipient_label: str
    recipient_email: str
    kind: str
    send_email: bool
    in_app: bool
    # The Messenger channel (B-134): whether this row rides
    # it, and its delivery outcome ("", "sent",
    # "driver_unavailable", "failed", "unlinked").
    send_messenger: bool = False
    messenger_status: str = ""
    subject: str
    body_text: str
    body_html: str
    template_body: str
    wording_source: str
    urgent: bool
    send_after: datetime
    status: str
    attempts: int
    last_error: str
    sent_at: datetime | None
    read_at: datetime | None
    created_at: datetime


class InboxResponse(BaseModel):
    """The caller's inbox page (B-101)."""

    items: list[NotificationResponse]
    unread: int


class PreferenceSwitch(BaseModel):
    """One channel × kind switch (B-100)."""

    channel: str = Field(pattern="^(email|in_app|messenger)$")
    kind: str = Field(pattern="^(invite|reminder|change|digest|rsvp)$")
    enabled: bool = True


class QuietHours(BaseModel):
    """The member's quiet window (B-100), in their timezone."""

    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str | None = None


class PreferencesUpdateRequest(BaseModel):
    """Write switches and/or quiet hours. ``quiet_hours``
    omitted leaves them untouched; an explicit empty object
    clears them (the service owns that reading)."""

    switches: list[PreferenceSwitch] = Field(default_factory=list)
    quiet_hours: QuietHours | None = None


class PreferencesResponse(BaseModel):
    """The member's full effective policy in one group."""

    group_id: str
    switches: list[PreferenceSwitch]
    quiet_hours: QuietHours | None


class MailEventResponse(BaseModel):
    """One provider mail event (B-102)."""

    id: str
    provider: str
    email: str
    event_type: str
    notification_id: str | None
    detail: str
    created_at: datetime


class FailuresResponse(BaseModel):
    """The admin deliverability view (B-102)."""

    notifications: list[NotificationResponse]
    mail_events: list[MailEventResponse]


class MailWebhookRequest(BaseModel):
    """A provider's delivery webhook, stubbed shape (B-102):
    the provider signs and authenticates with its own driver
    in Phase 12; the recorded shape is this."""

    email: str = Field(min_length=3, max_length=320)
    event_type: str = Field(pattern="^(bounce|complaint)$")
    notification_id: str | None = None
    detail: str = ""


class InviteCreateRequest(BaseModel):
    """Invite one address to one event (B-098)."""

    email: str = Field(min_length=3, max_length=320)


class InviteResponse(BaseModel):
    """An invite as its creator sees it. ``link`` carries the
    raw token and is present ONLY on the creation response —
    the token is shown once (B-014's discipline); later reads
    show the prefix only."""

    id: str
    group_id: str
    event_id: str
    email: str
    user_id: str | None
    token_prefix: str
    status: str
    expires_at: datetime
    rsvp_id: str | None
    link: str | None = None


class PublicInviteResponse(BaseModel):
    """An invite's public face: the event, and whether an
    account is on offer (it always is, after answering)."""

    event_id: str
    group_id: str
    group_name: str
    title: str
    description: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    place_name: str
    place_address: str
    status: str
    email: str
    account_offered: bool


class InviteRespondRequest(BaseModel):
    """Answer an invite without an account (B-098)."""

    status: str = Field(pattern="^(coming|cant|maybe)$")
    guests: int = Field(default=0, ge=0, le=50)
    note: str = Field(default="", max_length=1000)


class InviteRespondResponse(BaseModel):
    """The answer's record: attributed to the invited address,
    with the account offer stated, never hidden."""

    rsvp_id: str
    principal_label: str
    status: str
    account_offered: bool


class DigestResponse(BaseModel):
    """A stored digest (B-099): both renderings, as sent."""

    id: str
    group_id: str
    week_start: datetime
    week_end: datetime
    subject: str
    body_text: str
    body_html: str
    created_by: str
    created_at: datetime
    sent_at: datetime | None
