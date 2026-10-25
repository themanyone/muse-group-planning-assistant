"""Initial schema — every Phase 0/1 table (roadmap B-003/B-011…).

Revision ID: 0001_initial
Revises: none

This first migration builds the schema from the models' own
metadata: at the very first revision the models ARE the reviewed
schema, and create_all/drop_all keeps up/down perfectly symmetric
(the B-003 acceptance: migration up/down clean on an empty file).
Later revisions are hand-written op-by-op migrations, as usual.
"""
from typing import Sequence, Union

from alembic import op

from gpa.db import Base
from gpa import models  # noqa: F401  (registers every table)

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
