"""Phase 9 nonprofit-pack schema (roadmap B-105 → B-111).

Revision ID: 0012_phase9_nonprofit
Revises: 0011_phase8_notifications

The members pack lands together: sub-groups + their memberships
(B-105), the announcements board (B-106), resources + their
bookings (B-108), attendance marks (B-109), and candidate
approvals (B-111). Existing tables grow columns: ``events`` and
``tasks`` gain the ``subgroup_id`` scope, ``role_slots`` gains
the shift window (B-107), and ``groups`` gains the settings
pack's typed review + template columns (B-111).

As everywhere in this chain, every create/add is guarded:
revision 0001 builds from the models' live metadata, so on a
brand-new database these tables and columns already exist when
0012 runs, while on a Phase 8-era database they do not. Both
roads end at the same schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_phase9_nonprofit"
down_revision: Union[str, None] = "0011_phase8_notifications"
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


def _create(name: str, *columns, indexes=()):
    """Create one table if it is not already there (guarded)."""
    if _has_table(name):
        return
    op.create_table(name, *columns)
    for index_name, cols in indexes:
        op.create_index(index_name, name, cols)


def _add(table: str, column: sa.Column):
    """Add one column if it is not already there (guarded)."""
    if not _has_column(table, column.name):
        op.add_column(table, column)


def _ensure_index(table: str, name: str, columns: list[str]):
    """Create one index if the table lacks it (guarded) — the
    added scope columns are indexed in the models, so the
    upgrade road must end with the same indexes a fresh
    0001-built database has."""
    if not _has_table(table):
        return
    if name not in {ix["name"] for ix in _inspector().get_indexes(table)}:
        op.create_index(name, table, columns)


def _drop_index(table: str, name: str):
    """Drop one index if it exists (guarded) — SQLite refuses
    to drop a column its index still covers, so the downgrade
    removes the scope indexes before the columns."""
    if not _has_table(table):
        return
    if name in {ix["name"] for ix in _inspector().get_indexes(table)}:
        op.drop_index(name, table_name=table)


# Table names in creation order (parents before children), and
# the reverse for downgrade. Kept as data so up/down cannot
# drift apart.
TABLES: tuple[str, ...] = (
    "subgroups",
    "subgroup_memberships",
    "announcements",
    "resources",
    "resource_bookings",
    "attendance_marks",
    "candidate_approvals",
)

# (table, column-name) pairs this revision adds to old tables,
# in the order downgrade removes them (reversed).
ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("events", "subgroup_id"),
    ("tasks", "subgroup_id"),
    ("role_slots", "starts_at"),
    ("role_slots", "ends_at"),
    ("groups", "review_min_role"),
    ("groups", "review_approvals_required"),
    ("groups", "template_set"),
)


def upgrade() -> None:
    _create(
        "subgroups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("group_id", "name"),
        indexes=(("ix_subgroups_group_id", ["group_id"]),),
    )
    _create(
        "subgroup_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "subgroup_id", sa.String(36),
            sa.ForeignKey("subgroups.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("subgroup_id", "user_id"),
        indexes=(
            ("ix_subgroup_memberships_subgroup_id", ["subgroup_id"]),
            ("ix_subgroup_memberships_group_id", ["group_id"]),
            ("ix_subgroup_memberships_user_id", ["user_id"]),
        ),
    )
    _create(
        "announcements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("subgroup_id", sa.String(36), sa.ForeignKey("subgroups.id"), nullable=True),
        sa.Column("title", sa.String(240), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("author_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_announcements_group_id", ["group_id"]),
            ("ix_announcements_subgroup_id", ["subgroup_id"]),
        ),
    )
    _create(
        "resources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("keeper_user_id", sa.String(36), nullable=True),
        sa.Column("keeper_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("group_id", "name"),
        indexes=(("ix_resources_group_id", ["group_id"]),),
    )
    _create(
        "resource_bookings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "resource_id", sa.String(36),
            sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("booked_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("resource_id", "event_id"),
        indexes=(
            ("ix_resource_bookings_group_id", ["group_id"]),
            ("ix_resource_bookings_resource_id", ["resource_id"]),
            ("ix_resource_bookings_event_id", ["event_id"]),
        ),
    )
    _create(
        "attendance_marks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("marked_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "user_id"),
        indexes=(
            ("ix_attendance_marks_group_id", ["group_id"]),
            ("ix_attendance_marks_event_id", ["event_id"]),
            ("ix_attendance_marks_user_id", ["user_id"]),
        ),
    )
    _create(
        "candidate_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "candidate_id", sa.String(36),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("approver_user_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("approver_label", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("candidate_id", "approver_user_id"),
        indexes=(
            ("ix_candidate_approvals_group_id", ["group_id"]),
            ("ix_candidate_approvals_candidate_id", ["candidate_id"]),
            ("ix_candidate_approvals_approver_user_id", ["approver_user_id"]),
        ),
    )

    # Existing tables grow (guarded adds, same discipline).
    # The scope columns land WITHOUT a database-level foreign
    # key on this road: SQLite cannot add an FK constraint to
    # an existing table in place. Fresh databases get the FK
    # from the models via 0001; upgraded ones get the plain
    # column, and the reference is validated by the services
    # (subgroups.get_subgroup) on both roads — the same
    # discipline as the codebase's other plain-string links.
    _add("events", sa.Column("subgroup_id", sa.String(36), nullable=True))
    _add("tasks", sa.Column("subgroup_id", sa.String(36), nullable=True))
    _add("role_slots", sa.Column("starts_at", sa.DateTime(), nullable=True))
    _add("role_slots", sa.Column("ends_at", sa.DateTime(), nullable=True))
    _add("groups", sa.Column("review_min_role", sa.String(16), nullable=False, server_default="organiser"))
    _add("groups", sa.Column("review_approvals_required", sa.Integer(), nullable=False, server_default="1"))
    _add("groups", sa.Column("template_set", sa.String(32), nullable=False, server_default="starter"))
    _ensure_index("events", "ix_events_subgroup_id", ["subgroup_id"])
    _ensure_index("tasks", "ix_tasks_subgroup_id", ["subgroup_id"])


def downgrade() -> None:
    _drop_index("events", "ix_events_subgroup_id")
    _drop_index("tasks", "ix_tasks_subgroup_id")
    for table, column in reversed(ADDED_COLUMNS):
        if not _has_column(table, column):
            continue
        if column == "subgroup_id":
            # The scope columns carry a foreign key, which
            # SQLite will not drop in place — batch mode
            # recreates the table without the column instead.
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column(column)
        else:
            op.drop_column(table, column)
    for name in reversed(TABLES):
        if _has_table(name):
            op.drop_table(name)
