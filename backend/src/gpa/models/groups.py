"""Group tables (roadmap B-015 → B-018).

A group is the home of one community's planning. Who belongs, with
which role, is a Membership row; an AI participant belongs through
an AgentSeat row of its own — a seat is a first-class member with a
role, never a fake user (roadmap D-35).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc

# The role ladder, least to most power. Permission checks compare by
# position in this tuple, never by scattered string comparisons.
ROLE_ORDER: tuple[str, ...] = ("guest", "member", "organiser", "admin", "owner")

# Group visibility (B-015). Private groups must not leak their
# existence: a non-member asking gets a 404, not a 403.
VISIBILITIES: tuple[str, ...] = ("private", "invite", "public-listed")


def role_at_least(role: str, minimum: str) -> bool:
    """True when ``role`` is at or above ``minimum`` on the ladder."""
    return ROLE_ORDER.index(role) >= ROLE_ORDER.index(minimum)


class Group(Base):
    """One community's planning home."""

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    # URL-friendly unique name; collisions get a numeric suffix
    # (services/groups.py owns that rule).
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    home_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    default_place: Mapped[str] = mapped_column(String(240), default="")
    # The per-group daily AI budget (Phase 3, B-047), in integer
    # micro-units of currency to match the AiUsage ledger. NULL
    # means "no cap set" — the soft-stop has nothing to stop at.
    ai_daily_budget_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Phase 8 comms settings (B-096/B-099/B-103). Reminder rules
    # as JSON: a list of {"kind": "days_before", "days": n} and
    # {"kind": "morning_of"} — empty means the roadmap defaults
    # (7 days, 1 day, morning-of). The digest schedule: weekday
    # (0 = Monday … 6 = Sunday, group-local) + "HH:MM"; NULL
    # weekday means the default (Monday 08:00) when the digest
    # is enabled. ``ai_reminder_wording`` is the B-103 opt-in:
    # the model may rephrase reminder words around the facts,
    # never change them.
    reminder_rules_json: Mapped[str] = mapped_column(Text, default="")
    digest_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    digest_time: Mapped[str] = mapped_column(String(5), default="")
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_reminder_wording: Mapped[bool] = mapped_column(Boolean, default=False)
    # Phase 9 settings pack (B-111). The review-queue rules live
    # as typed columns because approval reads them on every act:
    # the lowest role that may approve a candidate, and how many
    # DISTINCT approvers a candidate needs before it is decided
    # (1 = the first approval decides, the founding default).
    review_min_role: Mapped[str] = mapped_column(String(16), default="organiser")
    review_approvals_required: Mapped[int] = mapped_column(Integer, default=1)
    # Which template set the group's shelf draws from (B-111) —
    # a closed vocabulary owned by services/settings_pack.py.
    template_set: Mapped[str] = mapped_column(String(32), default="starter")
    # Phase 10 memory settings (B-114/B-119), validated by the
    # settings pack like the dials above: how many pruning
    # passes the engine may spend on one question (hard cap 3,
    # enforced in the pack AND the engine), the per-run token
    # budget it must stop inside, and which engine judges —
    # "hosted" (the process's configured driver) or "local"
    # (the group's own OpenAI-compatible endpoint, B-119).
    prune_max_passes: Mapped[int] = mapped_column(Integer, default=2)
    prune_token_budget: Mapped[int] = mapped_column(Integer, default=120_000)
    prune_driver: Mapped[str] = mapped_column(String(16), default="hosted")
    # Phase 12 embed origins (B-135): the web origins allowed
    # to embed this group's public page/widget, as a JSON list
    # of origin strings ("https://village.example"). Empty =
    # no embeds anywhere: CORS answers for the public surfaces
    # name only origins on this list, and the embed endpoint
    # refuses any other origin outright.
    embed_origins_json: Mapped[str] = mapped_column(Text, default="[]")
    # Phase 13 privacy + storage dials (B-141/B-144), validated
    # by the settings pack like the dials above. The upload
    # quota caps the group's TOTAL stored upload bytes
    # (FileAsset sizes); NULL means no group quota. Retention:
    # raw sources older than this many days auto-expire (the
    # approved events they produced stand); NULL means sources
    # are kept forever — retention is opt-in, never a surprise.
    upload_quota_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=104_857_600
    )
    source_retention_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Membership(Base):
    """A user's place in a group: their role and invite state."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "group_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    # invited -> active (accepted) | declined. Only active counts.
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AgentSeat(Base):
    """An AI participant's seat in one group (B-018).

    A seat has a display name, a role on the same ladder, and (via
    its ApiKey) scopes. Everything a seat does is audited as the
    seat — never as the human who created it.
    """

    __tablename__ = "agent_seats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="member")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    # The seat's own profile (Phase 19, B-202): a seat is a
    # participant with a public face, like a person — a
    # description of what it does for the group and an avatar
    # URL it (or an admin) maintains. Both start empty; an
    # undescribed seat is visible as such, never dressed up.
    description: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")


class Candidate(Base):
    """A draft awaiting the review queue (B-018 stub, grown by
    Phase 3 into the full candidate of roadmap §4).

    A candidate is the ONLY thing AI pipelines may create: fields
    drafted from a source, each with its confidence and (through
    ``Provenance`` rows) the exact quote that supports it. A person
    approves, edits, rejects, or merges it; approval is the only
    path from draft to published entity (Phase 4 mints the entity
    and fills ``resulting_entity_*`` — until then the link stays
    empty, honestly).
    """

    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="event")
    title: Mapped[str] = mapped_column(String(240), default="")
    # Who drafted it, as an audit-friendly label: "user:<id>" or
    # "seat:<id>" — a seat's work is never attributed to its owner.
    # Machine-drafted candidates say "system:extraction"; the
    # person who requested the run is on the job + audit rows.
    created_by: Mapped[str] = mapped_column(String(80))
    # pending | edited -> approved | rejected | merged; a re-run's
    # fresh drafts mark the old undecided ones superseded (B-045).
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The draft itself, as JSON (B-040): the kind's fields at top
    # level (title / starts_at / place …) plus a "fields" map —
    # per field: value, confidence, quote, supported — so the
    # queue can show exactly what the model claimed and what the
    # source actually backs. Plain text column; services own the
    # shape (ai/extraction.py writes it, services/quality.py reads
    # it for flags).
    draft_json: Mapped[str] = mapped_column(Text, default="{}")
    # Candidate-level confidence: the mean of its supported fields'
    # confidences (per-field numbers live in the draft; this is the
    # queue's sort/filter number, B-043). NULL for hand-made stubs.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Server-computed quality flags (B-044), a JSON list of closed
    # vocabulary strings the frontend renders verbatim.
    flags_json: Mapped[str] = mapped_column(Text, default="[]")
    # Which prompt (name + version) produced this draft (B-038) —
    # a candidate can always answer "which instructions made you?"
    prompt_name: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The extraction job that produced this draft (B-045); plain
    # string, like the audit's entity references.
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # When this candidate was merged into another (B-043): the
    # survivor's id. The merged row keeps its draft + provenance —
    # merging decides, it does not erase.
    merged_into_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # The published entity approval minted (Phase 4, B-049). Until
    # Phase 4 exists these stay NULL on purpose: approval records
    # the DECISION here; minting is the events module's job, and a
    # filled link before then would be a lie in the database.
    resulting_entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resulting_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, onupdate=now_utc
    )
    # The source this draft was drawn from (Phase 2, B-023). A
    # PLAIN STRING, not a foreign key, on purpose: deleting a
    # source must never cascade-delete an approved candidate — the
    # id stays as a tombstone and ``source_deleted_at`` says when
    # the material behind it went away. The candidate keeps its
    # text; it loses its quotes (see models/sources.py Provenance).
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
