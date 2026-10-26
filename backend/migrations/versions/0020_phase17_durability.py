"""Phase 17 durability schema (roadmap B-171).

Revision ID: 0020_phase17_durability
Revises: 0019_phase17_audit_chain

Two new tables: ``draft_autosaves`` (server-side draft
autosave with conflict copies) and ``upload_sessions``
(resumable chunked uploads). Guarded like the rest of the
chain: 0001 builds from live metadata, so on a fresh
database both tables already exist.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_phase17_durability"
down_revision: Union[str, None] = "0019_phase17_audit_chain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("draft_autosaves"):
        op.create_table(
            "draft_autosaves",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("target_kind", sa.String(24), nullable=False),
            sa.Column("target_id", sa.String(64), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_conflict", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_draft_autosaves_group_id", "draft_autosaves", ["group_id"])
        op.create_index("ix_draft_autosaves_target_id", "draft_autosaves", ["target_id"])
    if not _has_table("upload_sessions"):
        op.create_table(
            "upload_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("filename", sa.String(240), nullable=False, server_default="upload"),
            sa.Column("total_bytes", sa.Integer(), nullable=False),
            sa.Column("chunk_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("parts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(16), nullable=False, server_default="open"),
            sa.Column("source_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_upload_sessions_group_id", "upload_sessions", ["group_id"])


def downgrade() -> None:
    if _has_table("upload_sessions"):
        op.drop_index("ix_upload_sessions_group_id", table_name="upload_sessions")
        op.drop_table("upload_sessions")
    if _has_table("draft_autosaves"):
        op.drop_index("ix_draft_autosaves_target_id", table_name="draft_autosaves")
        op.drop_index("ix_draft_autosaves_group_id", table_name="draft_autosaves")
        op.drop_table("draft_autosaves")
