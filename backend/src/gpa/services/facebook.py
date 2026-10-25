"""Facebook Event drafts (roadmap B-058).

A draft is where this module's road ENDS — deliberately.
Creating a real Facebook Event means posting outward as a
person or a page, an act that belongs to a human pressing a
button through the Phase 12 integration. What the backend owes
the organiser until then is a draft that is *complete*: every
field Meta's event creation requires, in the shape it expects,
plus a copy-ready text block a person can paste anywhere.

The draft is built from a PUBLISHED event only: a draft event
has no business being announced anywhere (the bright line), and
a cancelled one is announced as cancelled by the calendar, not
re-announced as live by a social draft.
"""

from datetime import timezone
from zoneinfo import ZoneInfo

from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.sources import Flyer
from gpa.services import ical as ical_service

# The fields Meta's event creation requires / accepts for a
# basic event, as the completeness test pins them: a name, a
# start time, and (for anything but the briefest gathering) an
# end time, a description, and a place.
META_REQUIRED_FIELDS: tuple[str, ...] = ("name", "start_time")


def build_draft(db, event: Event) -> dict:
    """One published event → {payload, copy_text, cover}.

    ``payload`` is the draft in Meta's own field names
    (``name``, ``start_time``/``end_time`` as ISO-8601 with the
    event's true offset, ``description``, ``place``), ready for
    Phase 12 to hand to the real integration unchanged in shape.
    ``cover`` is a SUGGESTION — the event's flyer picture when it
    has one — never a claim that anything was uploaded."""
    if event.status == "draft":
        raise AppError(
            "not_found", detail="That event could not be found."
        )
    if event.status == "cancelled":
        raise AppError(
            "conflict",
            detail="A cancelled event has no Facebook draft — it is announced as cancelled, not as live.",
        )
    tz = ZoneInfo(event.timezone)
    start_local = event.starts_at.replace(tzinfo=timezone.utc).astimezone(tz)
    end_local = event.ends_at.replace(tzinfo=timezone.utc).astimezone(tz)
    description = ical_service._description(event)
    payload: dict = {
        "name": event.title,
        "start_time": start_local.isoformat(),
        "end_time": end_local.isoformat(),
        "description": description,
        "timezone": event.timezone,
    }
    place: dict = {}
    if event.place_name:
        place["name"] = event.place_name
    if event.place_address:
        place["location"] = event.place_address
    if event.lat is not None and event.lng is not None:
        place["latitude"] = event.lat
        place["longitude"] = event.lng
    if place:
        payload["place"] = place

    cover = None
    if event.flyer_id:
        flyer = db.get(Flyer, event.flyer_id)
        if flyer is not None:
            cover = {
                "flyer_id": flyer.id,
                "thumbnail_key": flyer.thumbnail_key,
                "alt_text": flyer.alt_text,
                "note": (
                    "Suggested cover: the event's own flyer picture. "
                    "Nothing has been uploaded anywhere."
                ),
            }

    # The copy-ready block: what a person pastes into any
    # "create event" form, on any platform, and loses nothing.
    when = start_local.strftime("%A %d %B %Y, %H:%M")
    lines = [event.title, f"{when} ({event.timezone})"]
    if event.place_name:
        lines.append(f"Where: {event.place_name}")
    if description:
        lines.append("")
        lines.append(description)
    copy_text = "\n".join(lines)
    return {"payload": payload, "copy_text": copy_text, "cover": cover}
