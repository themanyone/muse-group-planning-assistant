"""Phase 13 privacy + storage schema (roadmap B-141 → B-144).

Revision ID: 0016_phase13_privacy
Revises: 0015_phase12_publishing

Phase 13 re-attacks everything built so far, and its schema
is two guarded columns on ``groups``, no new tables:

- ``upload_quota_bytes`` — the per-group cap on total stored
  upload bytes (B-141); the model default (100 MiB) applies to
  new groups, and this migration backfills the same value for
  existing rows so the quota bites uniformly. NULL means a
  group has deliberately chosen no quota.
- ``source_retention_days`` — the retention dial (B-144):
  raw sources older than this auto-expire; NULL (the
  migration's value for every existing group) means sources
  are kept forever. Retention is opt-in.

As everywhere in this chain, every add is guarded: revision
0001 builds from the models' live metadata, so on a brand-new
database these columns already exist when 0016 runs, while
on a Phase 12-era database they do not. Both roads end at the
same schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_phase13_privacy"
down_revision: Union[str, None] = "0015_phase12_publishing"
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


def _add(table: str, column: sa.Column):
    """Add one column if it is not already there (guarded)."""
    if not _has_column(table, column.name):
        op.add_column(table, column)


ADDED_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    ("groups", sa.Column("upload_quota_bytes", sa.Integer())),
    ("groups", sa.Column("source_retention_days", sa.Integer())),
)


def upgrade() -> None:
    for table, column in ADDED_COLUMNS:
        _add(table, column)
    # Backfill the quota default for rows that predate the
    # column (fresh databases already carry the model default
    # on new rows; the UPDATE is a no-op where values exist).
    if _has_column("groups", "upload_quota_bytes"):
        op.execute(
            "UPDATE groups SET upload_quota_bytes = 104857600 "
            "WHERE upload_quota_bytes IS NULL"
        )


def downgrade() -> None:
    for table, column in reversed(ADDED_COLUMNS):
        if _has_column(table, column.name):
            op.drop_column(table, column.name)
