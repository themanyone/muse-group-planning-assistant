"""Phase 3 AI schema (roadmap B-037, B-043, B-047).

Revision ID: 0006_phase3_ai
Revises: 0005_job_progress

Three growths, one revision (the phase's schema lands together so
every later Phase 3 slice builds on the same ground):

- ``ai_usage`` — the model-call ledger (B-037/B-047): one row per
  adapter call, with tokens and integer micro-unit cost.
- ``candidates`` grows from the Phase 1 stub into the full
  candidate of roadmap §4 (B-043): draft JSON, candidate-level
  confidence, server-computed flags, the prompt name+version that
  produced the draft, the extraction job link, the merge link,
  the (Phase 4) resulting-entity link, and ``updated_at``.
- ``groups.ai_daily_budget_micros`` — the per-group daily AI
  budget setting (B-047); NULL means no cap.

As everywhere in this chain, every step is inspector-guarded:
revision 0001 builds from the models' live metadata, so on a
brand-new database these objects already exist when 0006 runs,
while on a Phase 2-era database they do not. Both roads end at
the same schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from gpa import models  # noqa: F401  (registers every table)
from gpa.models.ai import AiUsage

revision: str = "0006_phase3_ai"
down_revision: Union[str, None] = "0005_job_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, type, nullable, server_default) for the guarded adds.
_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine, bool, str | None], ...] = (
    ("candidates", "draft_json", sa.Text(), False, "{}"),
    ("candidates", "confidence", sa.Float(), True, None),
    ("candidates", "flags_json", sa.Text(), False, "[]"),
    ("candidates", "prompt_name", sa.String(64), False, ""),
    ("candidates", "prompt_version", sa.Integer(), True, None),
    ("candidates", "job_id", sa.String(36), True, None),
    ("candidates", "merged_into_id", sa.String(36), True, None),
    ("candidates", "resulting_entity_type", sa.String(32), True, None),
    ("candidates", "resulting_entity_id", sa.String(36), True, None),
    ("candidates", "updated_at", sa.DateTime(), True, None),
    ("groups", "ai_daily_budget_micros", sa.Integer(), True, None),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    return column in [c["name"] for c in _inspector().get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    AiUsage.__table__.create(bind, checkfirst=True)
    for table, name, col_type, nullable, default in _COLUMNS:
        if not _has_column(table, name):
            op.add_column(
                table,
                sa.Column(name, col_type, nullable=nullable, server_default=default),
            )
    # updated_at predates itself on old rows: backfill from
    # created_at so no candidate claims it was never touched at a
    # time before it existed.
    if _has_column("candidates", "updated_at"):
        op.execute(
            "UPDATE candidates SET updated_at = created_at WHERE updated_at IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table, name, _type, _nullable, _default in reversed(_COLUMNS):
        if _has_column(table, name):
            op.drop_column(table, name)
    if _has_table("ai_usage"):
        AiUsage.__table__.drop(bind)
