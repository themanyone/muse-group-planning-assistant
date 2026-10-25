"""Flyer-surface schemas (roadmap Phase 5: B-065, B-070, B-072).

The wall and the print view are the two places a group LOOKS at
its posters rather than its calendar, so their shapes are built
for looking: every poster present (read or not), its description
state stated, and — for print — every fact as plain text.
"""

from pydantic import BaseModel, Field


class AltTextUpdateRequest(BaseModel):
    """A person's own description of a flyer (B-065): the human
    road to alt_text_status "complete". The words are stored as
    given (whitespace tidied); an empty or placeholder text is
    refused by the service — it would not be a description."""

    alt_text: str = Field(min_length=1, max_length=500)


class WallEventSummary(BaseModel):
    """The event a flyer became, in the few words the wall needs."""

    id: str
    title: str
    starts_at: str  # UTC ISO (Z)
    status: str


class FlyerWallEntry(BaseModel):
    """One poster on the wall: the picture record, its reading
    state, and its event when approval has made one.

    ``review_status`` is the honest one-word state: the event's
    own status when an event exists, else ``pending_review`` — a
    poster nobody has approved yet is ON the wall, labelled,
    never invisible (B-070). A poster whose reading failed says
    ``read_failed`` (B-069's partial truth, on the wall too)."""

    flyer_id: str
    source_id: str
    source_kind: str
    parent_flyer_id: str | None = None
    thumbnail_key: str = ""
    width: int = 0
    height: int = 0
    alt_text: str
    alt_text_status: str
    transcript: str = ""
    generated: bool = False
    review_status: str
    event: WallEventSummary | None = None


class FlyerWallResponse(BaseModel):
    """One period's wall (B-070)."""

    period: str  # "YYYY-MM" in the group's timezone
    entries: list[FlyerWallEntry] = Field(default_factory=list)


class PrintEventRow(BaseModel):
    """One event as the noticeboard needs it: every fact present
    as text (B-072). ``when`` is words ("Saturday 24 October
    2026, 7:00 PM – 9:00 PM"), not an instant — paper has no
    timezone conversions."""

    event_id: str
    title: str
    when: str
    place: str = ""
    host: str = ""
    cost: str = ""
    bring: str = ""
    description: str = ""
    status: str


class PrintMonthResponse(BaseModel):
    """A month's print listing (B-072): the structured rows plus
    the whole listing as one plain-text document the frontend
    can hand to a printer as-is."""

    period: str  # "YYYY-MM" in the group's timezone
    timezone: str
    events: list[PrintEventRow] = Field(default_factory=list)
    text: str = ""
