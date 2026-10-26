"""Phase 19 agent-seat equality schema (roadmap B-202).

Revision ID: 0022_phase19_seat_equality
Revises: 0021_phase17_account_security

Why this revision exists: B-202 gives every agent seat the
profile a person has — a description of what the seat does
for its group and an avatar URL — so a seat stands in the
group as a named participant, not a bare key with a role.
That needs two columns on ``agent_seats``:

- ``description`` (text, default "") — the seat's own words
  about its job, editable by the seat itself or an admin;
- ``avatar_url`` (string, default "") — where its picture
  lives; the backend stores the address, never the image.

Guarded like the rest of the chain: 0001 builds from live
metadata, so on a fresh database both columns already
exist; on a Phase 18-era database they do not. Both roads
end at the same schema, and down is as guarded as up.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_phase19_seat_equality"
down_revision: Union[str, None] = "0021_phase17_account_security"
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


ADDED_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    ("agent_seats", sa.Column("description", sa.Text(), nullable=False, server_default="")),
    ("agent_seats", sa.Column("avatar_url", sa.String(500), nullable=False, server_default="")),
)


def upgrade() -> None:
    """Add the seat profile columns wherever they are missing."""
    for table, column in ADDED_COLUMNS:
        if not _has_column(table, column.name):
            op.add_column(table, column)


def downgrade() -> None:
    """Drop the seat profile columns wherever they exist."""
    for table, column in reversed(ADDED_COLUMNS):
        if _has_column(table, column.name):
            op.drop_column(table, column.name)
