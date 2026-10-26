"""Phase 17 advanced account security schema (roadmap B-174).

Revision ID: 0021_phase17_account_security
Revises: 0020_phase17_durability

Why this revision exists: B-174 gives every account TOTP
multi-factor authentication, a per-device session list,
progressive lockout after failed sign-ins, and login-anomaly
notices. That needs:

- on ``users``: the SEALED TOTP secret (never plaintext), the
  MFA on/off flag, the last accepted TOTP time-step (codes are
  single-use per step), the consecutive-failure counter, and
  the lock expiry;
- on ``refresh_tokens``: the user-agent / IP the session was
  born on, so the session list can name each device;
- a new ``user_devices`` table: every (IP, user-agent) pair a
  person has signed in from, the anomaly notice's memory.

Guarded like the rest of the chain: 0001 builds from live
metadata, so on a fresh database every column and the table
already exist; on a Phase 17-era database they do not. Both
roads end at the same schema, and down is as guarded as up.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_phase17_account_security"
down_revision: Union[str, None] = "0020_phase17_durability"
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
    ("users", sa.Column("totp_secret", sa.Text(), nullable=True)),
    ("users", sa.Column("mfa_enabled", sa.Integer(), nullable=False, server_default="0")),
    ("users", sa.Column("mfa_last_step", sa.Integer(), nullable=False, server_default="-1")),
    ("users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0")),
    ("users", sa.Column("locked_until", sa.DateTime(), nullable=True)),
    ("refresh_tokens", sa.Column("user_agent", sa.String(240), nullable=False, server_default="")),
    ("refresh_tokens", sa.Column("ip_address", sa.String(64), nullable=False, server_default="")),
)


def upgrade() -> None:
    for table, column in ADDED_COLUMNS:
        _add(table, column)
    if not _has_table("user_devices"):
        op.create_table(
            "user_devices",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("ip_address", sa.String(64), nullable=False, server_default=""),
            sa.Column("user_agent", sa.String(240), nullable=False, server_default=""),
            sa.Column("first_seen_at", sa.DateTime(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"])


def downgrade() -> None:
    if _has_table("user_devices"):
        op.drop_index("ix_user_devices_user_id", table_name="user_devices")
        op.drop_table("user_devices")
    for table, column in reversed(ADDED_COLUMNS):
        if _has_column(table, column.name):
            op.drop_column(table, column.name)
