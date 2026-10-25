"""Phase 11 integrations schema (roadmap B-123 → B-128).

Revision ID: 0014_phase11_integrations
Revises: 0013_phase10_memory

The assistant reaches the places the group already talks:
``external_account_links`` (identity by explicit link, never
by name), ``live_sync_states`` (a live source's intake
cursor), ``integrations`` (the framework record with its
sealed config), ``webhook_endpoints`` + ``webhook_receipts``
(the signed inbound door and its replay guard),
``external_event_links`` (an event's copy in an outside
calendar), and ``outbound_webhooks`` + ``outbound_deliveries``
(the signed outbound door, inspectable per delivery).
``source_chunks`` gains ``sender_ref`` — the speaker's id in
the source system, the only thing an account link may key on.

As everywhere in this chain, every create/add is guarded:
revision 0001 builds from the models' live metadata, so on a
brand-new database these tables and the column already exist
when 0014 runs, while on a Phase 10-era database they do
not. Both roads end at the same schema, and down is as
guarded as up.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_phase11_integrations"
down_revision: Union[str, None] = "0013_phase10_memory"
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


TABLES: tuple[str, ...] = (
    "external_account_links",
    "live_sync_states",
    "integrations",
    "webhook_endpoints",
    "webhook_receipts",
    "external_event_links",
    "outbound_webhooks",
    "outbound_deliveries",
)

ADDED_COLUMNS: tuple[tuple[str, str], ...] = (("source_chunks", "sender_ref"),)


def upgrade() -> None:
    _create(
        "external_account_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("connector_kind", sa.String(48)),
        sa.Column("external_user_id", sa.String(240)),
        sa.Column("external_name", sa.String(160)),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_by", sa.String(80)),
        sa.Column("created_at", sa.DateTime),
        sa.UniqueConstraint("group_id", "connector_kind", "external_user_id"),
        indexes=(
            ("ix_external_account_links_group_id", ["group_id"]),
            ("ix_external_account_links_connector_kind", ["connector_kind"]),
            ("ix_external_account_links_user_id", ["user_id"]),
        ),
    )
    _create(
        "live_sync_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id")),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("cursor", sa.String(240)),
        sa.Column("messages_seen", sa.Integer),
        sa.Column("last_synced_at", sa.DateTime),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
        sa.UniqueConstraint("source_id"),
        indexes=(
            ("ix_live_sync_states_source_id", ["source_id"]),
            ("ix_live_sync_states_group_id", ["group_id"]),
        ),
    )
    _create(
        "integrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("kind", sa.String(48)),
        sa.Column("name", sa.String(160)),
        sa.Column("config_sealed", sa.Text),
        sa.Column("status", sa.String(16)),
        sa.Column("last_error", sa.Text),
        sa.Column("last_checked_at", sa.DateTime),
        sa.Column("last_success_at", sa.DateTime),
        sa.Column("created_by", sa.String(80)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
        indexes=(
            ("ix_integrations_group_id", ["group_id"]),
            ("ix_integrations_kind", ["kind"]),
        ),
    )
    _create(
        "webhook_endpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id")),
        sa.Column("token_hash", sa.String(128)),
        sa.Column("token_prefix", sa.String(16)),
        sa.Column("created_by", sa.String(80)),
        sa.Column("revoked_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime),
        indexes=(
            ("ix_webhook_endpoints_group_id", ["group_id"]),
            ("ix_webhook_endpoints_source_id", ["source_id"]),
            ("ix_webhook_endpoints_token_hash", ["token_hash"]),
        ),
    )
    _create(
        "webhook_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("webhook_endpoints.id")),
        sa.Column("signature", sa.String(128)),
        sa.Column("received_at", sa.DateTime),
        sa.UniqueConstraint("endpoint_id", "signature"),
        indexes=(
            ("ix_webhook_receipts_endpoint_id", ["endpoint_id"]),
            ("ix_webhook_receipts_signature", ["signature"]),
        ),
    )
    _create(
        "external_event_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id")),
        sa.Column("connector_kind", sa.String(48)),
        sa.Column("external_id", sa.String(240)),
        sa.Column("payload_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
        sa.UniqueConstraint("event_id", "connector_kind"),
        indexes=(
            ("ix_external_event_links_group_id", ["group_id"]),
            ("ix_external_event_links_event_id", ["event_id"]),
        ),
    )
    _create(
        "outbound_webhooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("url", sa.String(2000)),
        sa.Column("events_json", sa.Text),
        sa.Column("secret_sealed", sa.Text),
        sa.Column("active", sa.Boolean),
        sa.Column("created_by", sa.String(80)),
        sa.Column("created_at", sa.DateTime),
        indexes=(("ix_outbound_webhooks_group_id", ["group_id"]),),
    )
    _create(
        "outbound_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("webhook_id", sa.String(36), sa.ForeignKey("outbound_webhooks.id")),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id")),
        sa.Column("event_kind", sa.String(48)),
        sa.Column("payload_json", sa.Text),
        sa.Column("status", sa.String(16)),
        sa.Column("attempts", sa.Integer),
        sa.Column("last_error", sa.Text),
        sa.Column("response_status", sa.Integer),
        sa.Column("job_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("delivered_at", sa.DateTime),
        indexes=(
            ("ix_outbound_deliveries_webhook_id", ["webhook_id"]),
            ("ix_outbound_deliveries_group_id", ["group_id"]),
            ("ix_outbound_deliveries_event_kind", ["event_kind"]),
            ("ix_outbound_deliveries_status", ["status"]),
        ),
    )
    _add("source_chunks", sa.Column("sender_ref", sa.String(240)))


def downgrade() -> None:
    for table, column in reversed(ADDED_COLUMNS):
        if _has_column(table, column):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
    for table in reversed(TABLES):
        if _has_table(table):
            op.drop_table(table)
