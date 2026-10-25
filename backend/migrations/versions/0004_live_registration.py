"""Live-chat registration columns (roadmap B-032).

Revision ID: 0004_live_registration
Revises: 0003_link_url

Two guarded columns on sources: ``connector_kind`` and
``external_id`` — the binding a chat_live source is registered
under. Guarded for the standing reason (0001 builds from live
metadata, so a fresh database already has them).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_live_registration"
down_revision: Union[str, None] = "0003_link_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (("connector_kind", sa.String(48)), ("external_id", sa.String(240)))


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        if not _has_column("sources", name):
            op.add_column(
                "sources", sa.Column(name, type_, nullable=False, server_default="")
            )


def downgrade() -> None:
    for name, _type in reversed(_COLUMNS):
        if _has_column("sources", name):
            op.drop_column("sources", name)
