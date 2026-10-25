"""Phase 6 truth-keeping schema (roadmap B-076 → B-080).

Revision ID: 0009_phase6_truth
Revises: 0008_phase5_vision

One column, three tables — the whole truth-keeping area's
schema lands together:

- ``events.deleted_at`` — the tombstone stamp (B-080): a
  deleted event keeps its row for 30 days, hidden everywhere,
  restorable whole; NULL on every live event.
- ``event_contradictions`` — a disagreement an event carries
  until a person resolves it (B-079).
- ``announcement_drafts`` — drafted re-announcements (B-078);
  drafting only, sending is Phase 10.
- ``tombstones`` — the 30-day record of a deletion (B-080),
  carrying a deleted source's snapshot so undelete can
  rebuild it.

As everywhere in this chain, every change is guarded: revision
0001 builds from the models' live metadata, so on a brand-new
database these objects already exist when 0009 runs, while on
a Phase 5-era database they do not. Both roads end at the same
schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_phase6_truth"
down_revision: Union[str, None] = "0008_phase5_vision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    if table not in _inspector().get_table_names():
        return False
    return column in [c["name"] for c in _inspector().get_columns(table)]


def _has_table(table: str) -> bool:
    return table in _inspector().get_table_names()


def upgrade() -> None:
    if not _has_column("events", "deleted_at"):
        op.add_column(
            "events", sa.Column("deleted_at", sa.DateTime(), nullable=True)
        )
    if not _has_table("event_contradictions"):
        op.create_table(
            "event_contradictions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "event_id", sa.String(36),
                sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "group_id", sa.String(36),
                sa.ForeignKey("groups.id"), nullable=False,
            ),
            sa.Column("field", sa.String(64), nullable=False),
            sa.Column("values_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(16), nullable=False, server_default="open"),
            sa.Column("resolved_by", sa.String(80), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Index("ix_event_contradictions_event_id", "event_id"),
            sa.Index("ix_event_contradictions_group_id", "group_id"),
            sa.Index("ix_event_contradictions_status", "status"),
        )
    if not _has_table("announcement_drafts"):
        op.create_table(
            "announcement_drafts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "event_id", sa.String(36),
                sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "group_id", sa.String(36),
                sa.ForeignKey("groups.id"), nullable=False,
            ),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("changes_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_by", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Index("ix_announcement_drafts_event_id", "event_id"),
            sa.Index("ix_announcement_drafts_group_id", "group_id"),
        )
    if not _has_table("tombstones"):
        op.create_table(
            "tombstones",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "group_id", sa.String(36),
                sa.ForeignKey("groups.id"), nullable=False,
            ),
            sa.Column("entity_type", sa.String(16), nullable=False),
            sa.Column("entity_id", sa.String(36), nullable=False),
            sa.Column("title", sa.String(240), nullable=False, server_default=""),
            sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("deleted_by", sa.String(80), nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=False),
            sa.Column("purge_after", sa.DateTime(), nullable=False),
            sa.Index("ix_tombstones_group_id", "group_id"),
            sa.Index("ix_tombstones_entity_id", "entity_id"),
        )


def downgrade() -> None:
    if _has_table("tombstones"):
        op.drop_table("tombstones")
    if _has_table("announcement_drafts"):
        op.drop_table("announcement_drafts")
    if _has_table("event_contradictions"):
        op.drop_table("event_contradictions")
    if _has_column("events", "deleted_at"):
        op.drop_column("events", "deleted_at")
