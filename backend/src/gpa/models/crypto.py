"""Cryptographic storage models (roadmap B-167).

One table: the per-group data keys, wrapped under the
deployment's master key. The master key itself is NEVER in
the database (it arrives by environment / OS keyring — the
B-167 law); what rests here is each group's random data key
sealed under it, so a copied database yields neither the
fields (sealed under data keys) nor the data keys (sealed
under a key the database has never seen).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.core import new_id
from gpa.models.identity import now_utc


class GroupKey(Base):
    """One group's wrapped data key (B-167).

    ``wrapped_key`` is base64(nonce ‖ AES-256-GCM(master,
    data_key)) with the group id as authenticated data — a
    wrapped key replayed into another group's row does not
    open. ``version`` is the wrap generation: master rotation
    (B-169) re-wraps and bumps it, never touching the sealed
    fields themselves."""

    __tablename__ = "group_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("groups.id"), unique=True, index=True
    )
    wrapped_key: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RecoveryEscrow(Base):
    """One owner recovery code's escrow row (B-169).

    A recovery code never rests anywhere: what rests is the
    master key sealed under a scrypt key derived from the
    code (per-row salt), plus a keyed check value that lets a
    presented code find its row without trying every seal.
    ``code_hint`` is the code's last two characters — enough
    for an owner to recognise which code they hold, useless
    for reconstruction. A used code is spent (``used_at``);
    minting a new set deletes the old set outright.
    """

    __tablename__ = "recovery_escrow"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code_check: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code_hint: Mapped[str] = mapped_column(String(8), default="")
    salt: Mapped[str] = mapped_column(String(64))
    sealed_master: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
