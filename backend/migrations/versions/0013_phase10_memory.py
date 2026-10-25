"""Phase 10 memory schema (roadmap B-113 → B-122).

Revision ID: 0013_phase10_memory
Revises: 0012_phase9_nonprofit

The group's past becomes answerable, and the answering is
accountable: ``summaries`` (everything the memory engine says —
ask answers, discussion summaries, briefings, recaps,
newsletter drafts — each with its source range) and
``prune_runs`` (the pruning audit record: input, kept, dropped,
model, cost). ``groups`` gains the memory dials of the settings
pack: pruning pass cap, per-run token budget, and which engine
judges (B-114/B-119).

As everywhere in this chain, every create/add is guarded:
revision 0001 builds from the models' live metadata, so on a
brand-new database these tables and columns already exist when
0013 runs, while on a Phase 9-era database they do not. Both
roads end at the same schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_phase10_memory"
down_revision: Union[str, None] = "0012_phase9_nonprofit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return table in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in _inspector().get_columns(table)}


def _create(name: str, *columns, indexes=()):
    """Create one table if it is not already there (guarded)."""
    if _has_table(name):
        return
    op.create_table(name, *columns)
    for index_name, cols in indexes:
        op.create_index(index_name, name, cols)


def _add(table: str, column: sa.Column):
    """Add one column if it is not already there (guarded)."""
    if not _has_column(table, column.name):
        op.add_column(table, column)


TABLES: tuple[str, ...] = ("summaries", "prune_runs")

ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("groups", "prune_max_passes"),
    ("groups", "prune_token_budget"),
    ("groups", "prune_driver"),
)


def upgrade() -> None:
    _create(
        "summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("kind", sa.String(24)),
        sa.Column("title", sa.String(240)),
        sa.Column("body", sa.Text),
        sa.Column("status", sa.String(16)),
        sa.Column("verified", sa.Boolean),
        sa.Column("question", sa.Text),
        sa.Column("citations_json", sa.Text),
        sa.Column("range_json", sa.Text),
        sa.Column("event_id", sa.String(36)),
        sa.Column("created_by", sa.String(80)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
        indexes=(
            ("ix_summaries_group_id", ["group_id"]),
            ("ix_summaries_kind", ["kind"]),
            ("ix_summaries_status", ["status"]),
            ("ix_summaries_event_id", ["event_id"]),
            ("ix_summaries_created_at", ["created_at"]),
        ),
    )
    _create(
        "prune_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("purpose", sa.String(24)),
        sa.Column("question", sa.Text),
        sa.Column("input_chunks", sa.Integer),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("kept_json", sa.Text),
        sa.Column("dropped_json", sa.Text),
        sa.Column("passes", sa.Integer),
        sa.Column("model_id", sa.String(120)),
        sa.Column("cost_micros", sa.Integer),
        sa.Column("created_by", sa.String(80)),
        sa.Column("created_at", sa.DateTime),
        indexes=(
            ("ix_prune_runs_group_id", ["group_id"]),
            ("ix_prune_runs_purpose", ["purpose"]),
            ("ix_prune_runs_created_at", ["created_at"]),
        ),
    )
    # The memory dials on groups (B-114/B-119). Server defaults
    # match the models so a Phase 9-era row reads identically.
    _add("groups", sa.Column("prune_max_passes", sa.Integer, server_default="2"))
    _add("groups", sa.Column("prune_token_budget", sa.Integer, server_default="120000"))
    _add("groups", sa.Column("prune_driver", sa.String(16), server_default="hosted"))


def downgrade() -> None:
    for table, column in reversed(ADDED_COLUMNS):
        if _has_column(table, column):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
    for table in reversed(TABLES):
        if _has_table(table):
            op.drop_table(table)
