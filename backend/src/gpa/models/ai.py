"""AI usage tables (roadmap Phase 3, B-037/B-047).

The ledger of what the models cost. Every adapter call the
pipelines make writes one ``AiUsage`` row — model, tier, tokens,
cost — because spend is *recorded, not estimated* (Doc 07 §9):
the per-group daily budget (B-047) is enforced against this table,
and the release's cost evidence is read off it.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from gpa.db import Base
from gpa.models.identity import new_id, now_utc


class AiUsage(Base):
    """One model call's accounting (B-037).

    ``cost_micros`` is integer micro-units of currency — money in
    this codebase never travels as a float. ``source_id`` is a
    plain string, not a foreign key: the ledger must survive the
    source it describes (the same law as the audit log, B-019).
    """

    __tablename__ = "ai_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("groups.id"), nullable=True, index=True
    )
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # What the call was for: "extract:event", "extract:task", … —
    # later phases add "flyer", "summary", "prune" in the same shape.
    operation: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[str] = mapped_column(String(120), default="")
    tier: Mapped[str] = mapped_column(String(16), default="spark")
    prompt_name: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
