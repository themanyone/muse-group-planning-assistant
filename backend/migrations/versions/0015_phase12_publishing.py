"""Phase 12 publishing schema (roadmap B-131 → B-137).

Revision ID: 0015_phase12_publishing
Revises: 0014_phase11_integrations

Phase 12 takes the group's plans to the world outside, and
its schema is four guarded columns, no new tables:

- ``external_event_links.external_url`` — the outside copy's
  own address (a Facebook event's URL, B-131), kept beside
  the outside id the Phase 11 link row already carries;
- ``notifications.send_messenger`` + ``notifications.
  messenger_status`` — the Messenger channel (B-134): whether
  this row rides it, and what the channel did at delivery,
  recorded in the row's own words;
- ``groups.embed_origins_json`` — the web origins allowed to
  embed the group's public page (B-135); CORS answers name
  only these origins, because an unregistered origin gets
  nothing.

As everywhere in this chain, every add is guarded: revision
0001 builds from the models' live metadata, so on a brand-new
database these columns already exist when 0015 runs, while on
a Phase 11-era database they do not. Both roads end at the
same schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_phase12_publishing"
down_revision: Union[str, None] = "0014_phase11_integrations"
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
    ("external_event_links", sa.Column("external_url", sa.String(1000))),
    ("notifications", sa.Column("send_messenger", sa.Boolean)),
    ("notifications", sa.Column("messenger_status", sa.String(32))),
    ("groups", sa.Column("embed_origins_json", sa.Text)),
)


def upgrade() -> None:
    for table, column in ADDED_COLUMNS:
        _add(table, column)


def downgrade() -> None:
    for table, column in reversed(ADDED_COLUMNS):
        if _has_column(table, column.name):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column.name)
