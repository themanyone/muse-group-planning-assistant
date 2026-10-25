"""Group schemas: groups, memberships, seats, candidates (Phase 1)."""

from pydantic import BaseModel, Field


class GroupCreateRequest(BaseModel):
    """Create a group; the creator becomes its owner (B-015)."""

    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    visibility: str = Field(default="private")
    home_timezone: str = Field(default="UTC", max_length=64)
    default_place: str = Field(default="", max_length=240)


class ReminderRule(BaseModel):
    """One reminder rule (B-096): N days before, or the
    morning of. Days is required (1..365) for days_before and
    ignored for morning_of."""

    kind: str = Field(pattern="^(days_before|morning_of)$")
    days: int | None = Field(default=None, ge=1, le=365)


class GroupUpdateRequest(BaseModel):
    """Edit a group's settings (admin+; B-015)."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    visibility: str | None = None
    home_timezone: str | None = Field(default=None, max_length=64)
    default_place: str | None = Field(default=None, max_length=240)
    # The group's daily AI budget in cost micros (B-047). The
    # update route drops nulls (its standing convention), so the
    # cap can be set or changed here but not cleared back to
    # "no cap" — a very large number is the practical equivalent,
    # stated plainly rather than hidden.
    ai_daily_budget_micros: int | None = Field(default=None, ge=0)
    # Phase 8 comms settings (B-096/B-099/B-103): the reminder
    # rules for the group's events, the weekly digest's moment
    # (weekday 0 = Monday … 6 = Sunday, group-local HH:MM) and
    # whether it runs at all, and the AI reminder-wording
    # opt-in. Saving digest settings re-anchors the digest
    # job chain (services/digests.ensure_scheduled).
    reminder_rules: list[ReminderRule] | None = None
    digest_weekday: int | None = Field(default=None, ge=0, le=6)
    digest_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    digest_enabled: bool | None = None
    ai_reminder_wording: bool | None = None


class GroupResponse(BaseModel):
    """A group as the API shows it."""

    id: str
    name: str
    slug: str
    description: str
    visibility: str
    home_timezone: str
    default_place: str
    my_role: str | None = None
    # Phase 8 comms settings, echoed so a settings screen can
    # render them without a second read (B-096/B-099/B-103).
    reminder_rules: list[ReminderRule] = Field(default_factory=list)
    digest_weekday: int | None = None
    digest_time: str = ""
    digest_enabled: bool = True
    ai_reminder_wording: bool = False


class InviteRequest(BaseModel):
    """Invite a person by email with a starting role (B-016)."""

    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="member")


class RoleChangeRequest(BaseModel):
    """Change a member's role (B-016)."""

    role: str


class MembershipResponse(BaseModel):
    """One membership row: who, with which role, in which state."""

    id: str
    user_id: str
    email: str
    name: str
    role: str
    status: str


class SeatCreateRequest(BaseModel):
    """Create an agent seat (B-018): a name, a role, and the scopes
    its key will carry. Approval is never among a seat's powers."""

    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="member")
    scopes: list[str] = Field(default_factory=lambda: ["read", "draft"])


class SeatResponse(BaseModel):
    """A seat as listed. ``key`` appears only at creation."""

    id: str
    group_id: str
    name: str
    role: str
    key: str | None = None
    key_prefix: str | None = None


class CandidateCreateRequest(BaseModel):
    """Draft a candidate for the review queue (stub, B-018)."""

    kind: str = Field(default="event", max_length=32)
    title: str = Field(min_length=1, max_length=240)


class CandidateResponse(BaseModel):
    """A candidate as the queue shows it.

    Grown in Phase 3 (B-043) with the review queue's triage data —
    draft, confidence, server-computed flags (B-044), prompt
    provenance (B-038) — all additive: the B-018 stub fields are
    unchanged. ``resulting_entity_*`` stay null until Phase 4
    mints published entities; approval records the decision.
    """

    id: str
    group_id: str
    kind: str
    title: str
    status: str
    created_by: str
    approved_by: str | None = None
    draft: dict = Field(default_factory=dict)
    confidence: float | None = None
    flags: list[str] = Field(default_factory=list)
    source_id: str | None = None
    prompt_name: str = ""
    prompt_version: int | None = None
    job_id: str | None = None
    merged_into_id: str | None = None
    resulting_entity_type: str | None = None
    resulting_entity_id: str | None = None
    # Phase 9 (B-111): the multi-approver review progress — how
    # many distinct approvers have recorded approval, and how
    # many the group's settings require before it is decided.
    approvals_recorded: int = 0
    approvals_required: int = 1
