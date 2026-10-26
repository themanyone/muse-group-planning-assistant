"""Phase 17 key-management schema (roadmap B-169).

Revision ID: 0018_phase17_keymgmt
Revises: 0017_phase17_crypto

One new table: ``recovery_escrow`` — the owner recovery
codes' escrow rows (the master key sealed under scrypt keys
derived from one-time codes; the codes themselves rest
nowhere). Guarded like the rest of the chain: 0001 builds
from live metadata, so on a fresh database the table exists
before 0018 runs; on a Phase 17-early database it does not.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_phase17_keymgmt"
down_revision: Union[str, None] = "0017_phase17_crypto"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("recovery_escrow"):
        op.create_table(
            "recovery_escrow",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("code_check", sa.String(64), nullable=False),
            sa.Column("code_hint", sa.String(8), nullable=False, server_default=""),
            sa.Column("salt", sa.String(64), nullable=False),
            sa.Column("sealed_master", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("code_check", name="uq_recovery_escrow_code_check"),
        )
        op.create_index("ix_recovery_escrow_code_check", "recovery_escrow", ["code_check"])


def downgrade() -> None:
    if _has_table("recovery_escrow"):
        op.drop_index("ix_recovery_escrow_code_check", table_name="recovery_escrow")
        op.drop_table("recovery_escrow")
