"""Phase 7 planning schema (roadmap B-083 → B-093).

Revision ID: 0010_phase7_planning
Revises: 0009_phase6_truth

The whole planning area's tables land together — tasks,
decisions, open questions, polls (+ options + votes),
checklist items, role slots, cost lines, availability slots
(+ marks), and templates. No existing table changes: Phase 7
is a new module standing beside events, reading them, never
reshaping them.

As everywhere in this chain, every create is guarded: revision
0001 builds from the models' live metadata, so on a brand-new
database these tables already exist when 0010 runs, while on
a Phase 6-era database they do not. Both roads end at the
same schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_phase7_planning"
down_revision: Union[str, None] = "0009_phase6_truth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return table in _inspector().get_table_names()


def _create(name: str, *columns, indexes=()):
    """Create one table if it is not already there (guarded)."""
    if _has_table(name):
        return
    op.create_table(name, *columns)
    for index_name, cols in indexes:
        op.create_index(index_name, name, cols)


# Table names in creation order (parents before children), and
# the reverse for downgrade. Kept as data so up/down cannot
# drift apart.
TABLES: tuple[str, ...] = (
    "templates",
    "tasks",
    "decisions",
    "open_questions",
    "polls",
    "poll_options",
    "poll_votes",
    "checklist_items",
    "role_slots",
    "cost_lines",
    "availability_slots",
    "availability_marks",
)


def upgrade() -> None:
    _create(
        "templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("group_id", "name"),
        indexes=(("ix_templates_group_id", ["group_id"]),),
    )
    _create(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("title", sa.String(240), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_id", sa.String(36), nullable=True),
        sa.Column("owner_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("suggested_owner_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("candidate_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_tasks_group_id", ["group_id"]),
            ("ix_tasks_event_id", ["event_id"]),
            ("ix_tasks_status", ["status"]),
        ),
    )
    _create(
        "decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="current"),
        sa.Column("supersedes_id", sa.String(36), sa.ForeignKey("decisions.id"), nullable=True),
        sa.Column("superseded_by_id", sa.String(36), sa.ForeignKey("decisions.id"), nullable=True),
        sa.Column("candidate_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_decisions_group_id", ["group_id"]),
            ("ix_decisions_event_id", ["event_id"]),
            ("ix_decisions_status", ["status"]),
        ),
    )
    _create(
        "open_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("asked_of_id", sa.String(36), nullable=True),
        sa.Column("asked_of_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column(
            "resolution_decision_id", sa.String(36),
            sa.ForeignKey("decisions.id"), nullable=True,
        ),
        sa.Column("resolved_by", sa.String(80), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("candidate_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_open_questions_group_id", ["group_id"]),
            ("ix_open_questions_event_id", ["event_id"]),
            ("ix_open_questions_status", ["status"]),
        ),
    )
    _create(
        "polls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("question", sa.String(240), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False, server_default="choice"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("closes_at", sa.DateTime(), nullable=True),
        sa.Column("applied_event_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        indexes=(
            ("ix_polls_group_id", ["group_id"]),
            ("ix_polls_event_id", ["event_id"]),
            ("ix_polls_status", ["status"]),
        ),
    )
    _create(
        "poll_options",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "poll_id", sa.String(36),
            sa.ForeignKey("polls.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("label", sa.String(240), nullable=False, server_default=""),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(("ix_poll_options_poll_id", ["poll_id"]),),
    )
    _create(
        "poll_votes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "poll_id", sa.String(36),
            sa.ForeignKey("polls.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "option_id", sa.String(36),
            sa.ForeignKey("poll_options.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("voter_label", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("seat_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("poll_id", "voter_label"),
        indexes=(
            ("ix_poll_votes_poll_id", ["poll_id"]),
            ("ix_poll_votes_option_id", ["option_id"]),
        ),
    )
    _create(
        "checklist_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("section", sa.String(16), nullable=False, server_default="bring"),
        sa.Column("label", sa.String(240), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("claimed_by_id", sa.String(36), nullable=True),
        sa.Column("claimed_by_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_checklist_items_group_id", ["group_id"]),
            ("ix_checklist_items_event_id", ["event_id"]),
        ),
    )
    _create(
        "role_slots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("role_name", sa.String(80), nullable=False, server_default=""),
        sa.Column("claimed_by_id", sa.String(36), nullable=True),
        sa.Column("claimed_by_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_role_slots_group_id", ["group_id"]),
            ("ix_role_slots_event_id", ["event_id"]),
        ),
    )
    _create(
        "cost_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("label", sa.String(240), nullable=False, server_default=""),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paid_by_id", sa.String(36), nullable=True),
        sa.Column("paid_by_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_cost_lines_group_id", ["group_id"]),
            ("ix_cost_lines_event_id", ["event_id"]),
        ),
    )
    _create(
        "availability_slots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("label", sa.String(240), nullable=False, server_default=""),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_availability_slots_group_id", ["group_id"]),
            ("ix_availability_slots_event_id", ["event_id"]),
            ("ix_availability_slots_starts_at", ["starts_at"]),
        ),
    )
    _create(
        "availability_marks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "slot_id", sa.String(36),
            sa.ForeignKey("availability_slots.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("voter_label", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("seat_id", sa.String(36), nullable=True),
        sa.Column("can_attend", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("slot_id", "voter_label"),
        indexes=(("ix_availability_marks_slot_id", ["slot_id"]),),
    )


def downgrade() -> None:
    for name in reversed(TABLES):
        if _has_table(name):
            op.drop_table(name)
