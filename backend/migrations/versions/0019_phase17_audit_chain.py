"""Phase 17 audit-chain schema (roadmap B-170).

Revision ID: 0019_phase17_audit_chain
Revises: 0018_phase17_keymgmt

The audit log becomes a hash chain: every row gains
``prev_hash`` / ``row_hash``, and a one-row head table
(``audit_chain_head``) holds the chain's current tip so
writers extend the chain inside their own transaction —
under WAL's serialized writers the chain cannot fork.
Rows written before this migration carry NULL hashes; the
chain verifies from its first hashed row (the verifier
reports where the hashed span starts).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_phase17_audit_chain"
down_revision: Union[str, None] = "0018_phase17_keymgmt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _columns("audit_log")
    with op.batch_alter_table("audit_log") as batch:
        if "prev_hash" not in cols:
            batch.add_column(sa.Column("prev_hash", sa.String(64), nullable=True))
        if "row_hash" not in cols:
            batch.add_column(sa.Column("row_hash", sa.String(64), nullable=True))
    if not _has_table("audit_chain_head"):
        op.create_table(
            "audit_chain_head",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("last_hash", sa.String(64), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if _has_table("audit_chain_head"):
        op.drop_table("audit_chain_head")
    cols = _columns("audit_log")
    with op.batch_alter_table("audit_log") as batch:
        if "row_hash" in cols:
            batch.drop_column("row_hash")
        if "prev_hash" in cols:
            batch.drop_column("prev_hash")
