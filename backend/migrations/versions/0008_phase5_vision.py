"""Phase 5 vision schema (roadmap B-063 → B-071).

Revision ID: 0008_phase5_vision
Revises: 0007_phase4_events

Three columns, one revision — the whole vision area's schema
needs land together:

- ``provenance.region_json`` — a vision field's evidence is a
  PLACE on the picture, not a quote (B-063): the bounding box
  ``[x, y, w, h]`` as fractions of the image, stored beside the
  quote column (text provenance leaves it NULL).
- ``flyers.phash`` — the poster's perceptual hash (B-071), the
  average-hash hex the duplicate detector compares; existing
  rows predate it and carry the empty string (unknown, never a
  false match).
- ``flyers.generated`` — an AI-generated event image says so
  forever (B-067): the flag is a column, not a convention, so no
  projection can drop it.

As everywhere in this chain, every change is guarded: revision
0001 builds from the models' live metadata, so on a brand-new
database these columns already exist when 0008 runs, while on a
Phase 4-era database they do not. Both roads end at the same
schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_phase5_vision"
down_revision: Union[str, None] = "0007_phase4_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    if table not in _inspector().get_table_names():
        return False
    return column in [c["name"] for c in _inspector().get_columns(table)]


def upgrade() -> None:
    if not _has_column("provenance", "region_json"):
        op.add_column(
            "provenance", sa.Column("region_json", sa.Text(), nullable=True)
        )
    if not _has_column("flyers", "phash"):
        op.add_column(
            "flyers",
            sa.Column("phash", sa.String(16), nullable=False, server_default=""),
        )
    if not _has_column("flyers", "generated"):
        op.add_column(
            "flyers",
            sa.Column("generated", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if _has_column("flyers", "generated"):
        op.drop_column("flyers", "generated")
    if _has_column("flyers", "phash"):
        op.drop_column("flyers", "phash")
    if _has_column("provenance", "region_json"):
        op.drop_column("provenance", "region_json")
