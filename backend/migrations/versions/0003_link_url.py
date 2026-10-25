"""Link-source URL column (roadmap B-029).

Revision ID: 0003_link_url
Revises: 0002_phase2_sources

One guarded column: ``sources.url`` records where a link
source's archived chunks were fetched from. Guarded for the same
reason as 0002 — revision 0001 builds from live metadata, so a
brand-new database already has the column when this runs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_link_url"
down_revision: Union[str, None] = "0002_phase2_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column("sources", "url"):
        op.add_column(
            "sources",
            sa.Column("url", sa.String(2000), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if _has_column("sources", "url"):
        op.drop_column("sources", "url")
