"""Phase 8 notifications schema (roadmap B-095 → B-103).

Revision ID: 0011_phase8_notifications
Revises: 0010_phase7_planning

The comms area's tables land together — notifications (the
outbox), notification_preferences + member_notification_settings
(B-100's switches and quiet hours), event_invites (B-098),
digests (B-099), and mail_events (B-102). Two existing tables
grow columns: ``groups`` gains the Phase 8 settings (reminder
rules, the digest schedule, the AI-wording opt-in), and
``announcement_drafts`` gains its sending record (B-097).

As everywhere in this chain, every create/add is guarded:
revision 0001 builds from the models' live metadata, so on a
brand-new database these tables and columns already exist when
0011 runs, while on a Phase 7-era database they do not. Both
roads end at the same schema, and down is as guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_phase8_notifications"
down_revision: Union[str, None] = "0010_phase7_planning"
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


# Table names in creation order (parents before children), and
# the reverse for downgrade. Kept as data so up/down cannot
# drift apart.
TABLES: tuple[str, ...] = (
    "notifications",
    "notification_preferences",
    "member_notification_settings",
    "event_invites",
    "digests",
    "mail_events",
)

# (table, column-name) pairs this revision adds to old tables,
# in the order downgrade removes them (reversed).
ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("groups", "reminder_rules_json"),
    ("groups", "digest_weekday"),
    ("groups", "digest_time"),
    ("groups", "digest_enabled"),
    ("groups", "ai_reminder_wording"),
    ("announcement_drafts", "sent_at"),
    ("announcement_drafts", "sent_by"),
)


def upgrade() -> None:
    _create(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=True),
        sa.Column("event_id", sa.String(36), nullable=True),
        sa.Column("recipient_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("recipient_label", sa.String(320), nullable=False, server_default=""),
        sa.Column("recipient_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("kind", sa.String(32), nullable=False, server_default=""),
        sa.Column("send_email", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("in_app", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("subject", sa.String(240), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("template_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("wording_source", sa.String(24), nullable=False, server_default="template"),
        sa.Column("urgent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("send_after", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("reminder_rule", sa.String(32), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_notifications_group_id", ["group_id"]),
            ("ix_notifications_event_id", ["event_id"]),
            ("ix_notifications_recipient_user_id", ["recipient_user_id"]),
            ("ix_notifications_kind", ["kind"]),
            ("ix_notifications_status", ["status"]),
        ),
    )
    _create(
        "notification_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default=""),
        sa.Column("kind_category", sa.String(32), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "group_id", "channel", "kind_category"),
        indexes=(
            ("ix_notification_preferences_user_id", ["user_id"]),
            ("ix_notification_preferences_group_id", ["group_id"]),
        ),
    )
    _create(
        "member_notification_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("quiet_start", sa.String(5), nullable=True),
        sa.Column("quiet_end", sa.String(5), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "group_id"),
        indexes=(
            ("ix_member_notification_settings_user_id", ["user_id"]),
            ("ix_member_notification_settings_group_id", ["group_id"]),
        ),
    )
    _create(
        "event_invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column(
            "event_id", sa.String(36),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False, server_default=""),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("token_hash", sa.String(128), nullable=False, server_default=""),
        sa.Column("token_prefix", sa.String(16), nullable=False, server_default=""),
        sa.Column("invited_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("rsvp_id", sa.String(36), nullable=True),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(
            ("ix_event_invites_group_id", ["group_id"]),
            ("ix_event_invites_event_id", ["event_id"]),
            ("ix_event_invites_token_hash", ["token_hash"]),
        ),
    )
    _create(
        "digests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("week_start", sa.DateTime(), nullable=False),
        sa.Column("week_end", sa.DateTime(), nullable=False),
        sa.Column("subject", sa.String(240), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        indexes=(("ix_digests_group_id", ["group_id"]),),
    )
    _create(
        "mail_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(48), nullable=False, server_default=""),
        sa.Column("email", sa.String(320), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(24), nullable=False, server_default=""),
        sa.Column("notification_id", sa.String(36), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        indexes=(("ix_mail_events_email", ["email"]),),
    )

    # Existing tables grow (guarded adds, same discipline).
    _add("groups", sa.Column("reminder_rules_json", sa.Text(), nullable=False, server_default=""))
    _add("groups", sa.Column("digest_weekday", sa.Integer(), nullable=True))
    _add("groups", sa.Column("digest_time", sa.String(5), nullable=False, server_default=""))
    _add("groups", sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    _add("groups", sa.Column("ai_reminder_wording", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add("announcement_drafts", sa.Column("sent_at", sa.DateTime(), nullable=True))
    _add("announcement_drafts", sa.Column("sent_by", sa.String(80), nullable=True))


def downgrade() -> None:
    for table, column in reversed(ADDED_COLUMNS):
        if _has_column(table, column):
            op.drop_column(table, column)
    for name in reversed(TABLES):
        if _has_table(name):
            op.drop_table(name)
