"""Phase 2 source schema (roadmap B-023 → B-033).

Revision ID: 0002_phase2_sources
Revises: 0001_initial

The Phase 2 tables: sources, source_chunks, provenance, flyers,
inbound_addresses — plus the tombstone columns on candidates
(``source_id`` / ``source_deleted_at``, B-023) and the
stage/progress columns on sources (B-033).

Why every step is guarded: revision 0001 builds its schema from
the models' LIVE metadata, so on a brand-new database 0001 has
already created these tables and columns by the time this
revision runs, while on a database that was genuinely created in
the Phase 0/1 era they do not exist yet. Both roads must end at
the same schema, so each object is created only when missing and
dropped only when present. The end state is exact either way;
the guards only negotiate which road was taken.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from gpa import models  # noqa: F401  (registers every table)
from gpa.models.sources import Flyer, InboundAddress, Provenance, Source, SourceChunk

revision: str = "0002_phase2_sources"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (Source.__table__, SourceChunk.__table__, Provenance.__table__,
           Flyer.__table__, InboundAddress.__table__)


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    return column in [c["name"] for c in _inspector().get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)
    # Candidate tombstone columns (B-023).
    if not _has_column("candidates", "source_id"):
        op.add_column("candidates", sa.Column("source_id", sa.String(36), nullable=True))
    if not _has_column("candidates", "source_deleted_at"):
        op.add_column(
            "candidates", sa.Column("source_deleted_at", sa.DateTime(), nullable=True)
        )
    # Source stage/progress (B-033). Existing Phase 2 rows predate
    # the columns; the defaults match how those ingests finished:
    # synchronously, at done/100.
    if not _has_column("sources", "stage"):
        op.add_column(
            "sources",
            sa.Column("stage", sa.String(48), nullable=False, server_default="done"),
        )
    if not _has_column("sources", "progress"):
        op.add_column(
            "sources",
            sa.Column("progress", sa.Integer(), nullable=False, server_default="100"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column("sources", "progress"):
        op.drop_column("sources", "progress")
    if _has_column("sources", "stage"):
        op.drop_column("sources", "stage")
    if _has_column("candidates", "source_deleted_at"):
        op.drop_column("candidates", "source_deleted_at")
    if _has_column("candidates", "source_id"):
        op.drop_column("candidates", "source_id")
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
