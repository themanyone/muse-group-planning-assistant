"""Phase 17 field-encryption schema (roadmap B-167).

Revision ID: 0017_phase17_crypto
Revises: 0016_phase13_privacy

One new table: ``group_keys`` — each group's random data key,
wrapped (AES-256-GCM) under the deployment's master key. The
master key itself is never in the database (environment /
keyring only, B-167's law); the sealed fields it ultimately
protects (source chunk text + senders, provenance quotes,
event-link quotes) live in existing columns, their stored
form versioned by the ``enc:v1:`` prefix, so this migration
needs no column changes and no backfill: pre-B-167 rows read
as the legacy plaintext they are until rewritten.

Guarded like the rest of the chain: 0001 builds from the
models' live metadata, so on a brand-new database the table
already exists when 0017 runs; on a Phase 16-era database it
does not. Both roads end at the same schema.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_phase17_crypto"
down_revision: Union[str, None] = "0016_phase13_privacy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("group_keys"):
        op.create_table(
            "group_keys",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
            sa.Column("wrapped_key", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("rotated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("group_id", name="uq_group_keys_group_id"),
        )
        op.create_index("ix_group_keys_group_id", "group_keys", ["group_id"])


def downgrade() -> None:
    if _has_table("group_keys"):
        op.drop_index("ix_group_keys_group_id", table_name="group_keys")
        op.drop_table("group_keys")
