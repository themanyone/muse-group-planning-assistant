"""Job progress column (roadmap B-033).

Revision ID: 0005_job_progress
Revises: 0004_live_registration

One guarded column: ``jobs.progress`` (0–100), the number the
group stream reports as job.progress. Jobs predate the column in
0001's metadata-built schema, so — as everywhere in this chain —
the add is inspector-guarded and the downgrade drops only what
exists.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_job_progress"
down_revision: Union[str, None] = "0004_live_registration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column("jobs", "progress"):
        op.add_column(
            "jobs",
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if _has_column("jobs", "progress"):
        op.drop_column("jobs", "progress")
