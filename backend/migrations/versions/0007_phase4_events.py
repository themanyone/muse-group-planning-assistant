"""Phase 4 events schema (roadmap B-049 → B-061).

Revision ID: 0007_phase4_events
Revises: 0006_phase3_ai

Eight new tables, one revision — the whole events area lands
together so every Phase 4 slice builds on the same ground:

- ``events`` / ``event_source_links`` / ``event_changes`` —
  the moment, its supporting sources, its change history (B-049,
  B-050).
- ``recurrences`` — series rules + exceptions (B-052).
- ``calendar_feeds`` — hashed secret feed tokens (B-053).
- ``threads`` / ``comments`` — event discussion (B-060).
- ``rsvps`` — answers + waitlist state (B-061).

As everywhere in this chain, every create is guarded
(``checkfirst``): revision 0001 builds from the models' live
metadata, so on a brand-new database these tables already exist
when 0007 runs, while on a Phase 3-era database they do not.
Both roads end at the same schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from gpa import models  # noqa: F401  (registers every table)
from gpa.models.events import (
    CalendarFeed,
    Comment,
    Event,
    EventChange,
    EventSourceLink,
    Recurrence,
    Rsvp,
    Thread,
)

revision: str = "0007_phase4_events"
down_revision: Union[str, None] = "0006_phase3_ai"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Creation order honours the foreign keys (parents before children).
_TABLES = (
    Event,
    EventSourceLink,
    EventChange,
    Recurrence,
    CalendarFeed,
    Thread,
    Comment,
    Rsvp,
)


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    for model in _TABLES:
        model.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for model in reversed(_TABLES):
        if _has_table(model.__tablename__):
            model.__table__.drop(bind)
