"""Demo data, final (roadmap B-147).

The seed becomes the story. Loading the demo story gives a
stranger everything the submission script walks through, on a
blank machine, with no hand-steps:

- two groups — the Riverbend Community Trust (a nonprofit) and
  the Northside Hiking Club (a local club) — plus the original
  B-010 demo group, each with a cast of named people;
- a month of published history on the nonprofit's calendar
  (eight events across October–November 2026);
- a pinned bulletin-board announcement on the nonprofit's board;
- the club's 40-message trip argument as a pasted-chat source
  (Flow B's raw material, exactly forty chunks);
- the nonprofit's newsletter as a source with twelve sections
  (Flow C's raw material, one chunk per section).

Everything is IDEMPOTENT by natural key — a source, event, or
announcement is "the same" when its group already holds one
with the same title — so ``demo.sh`` can run twice and the
second run changes nothing. Demo passwords are printed in the
seed files on purpose; they are demo data, never real secrets.
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from gpa.models.groups import Group, Membership
from gpa.models.nonprofit import Announcement
from gpa.models.events import Event
from gpa.models.sources import Source
from gpa.services import announcements as announcement_service
from gpa.services import events as event_service
from gpa.services import paste as paste_service
from gpa.services import seed as seed_service
from gpa.services import sources as source_service

SEEDS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "seeds"

# The club's trip argument: Ana, Ben, and Cleo, forty messages
# exactly — Flow B's count, kept honest by the test suite.
TRIP_CHAT_LINES = [
    "[21/10/2026, 19:00:00] Ana: Right, the cabin trip is happening — this Saturday 10am at the lake cabin",
    "[21/10/2026, 19:01:00] Ben: Finally, we have talked about it for months",
    "[21/10/2026, 19:02:00] Cleo: I have waited all year for this trip",
    "[21/10/2026, 19:03:00] Ana: Saturday works for everyone, I checked",
    "[21/10/2026, 19:04:00] Ana: I will book the cabin tonight before someone else takes it",
    "[21/10/2026, 19:05:00] Ben: The big one or the small one?",
    "[21/10/2026, 19:06:00] Cleo: Big one, we are six with the cousins",
    "[21/10/2026, 19:07:00] Ana: Agreed then — we take the big cabin",
    "[21/10/2026, 19:08:00] Ben: And the dogs?",
    "[21/10/2026, 19:09:00] Cleo: The dogs come too, obviously",
    "[21/10/2026, 19:10:00] Ben: I can sort the kayaks, my brother has two spare",
    "[21/10/2026, 19:11:00] Ana: Perfect, kayaks are yours",
    "[21/10/2026, 19:12:00] Cleo: Big question — who is driving the van?",
    "[21/10/2026, 19:13:00] Cleo: Not me, I drove last time",
    "[21/10/2026, 19:14:00] Ben: We will figure the van out",
    "[21/10/2026, 19:15:00] Ana: I will plan the food shop on Friday",
    "[21/10/2026, 19:16:00] Cleo: Get the good cheese this time",
    "[21/10/2026, 19:17:00] Ben: And marshmallows, non negotiable",
    "[21/10/2026, 19:18:00] Ben: Also — do we need a second boat?",
    "[21/10/2026, 19:19:00] Ana: Maybe, depends who confirms",
    "[21/10/2026, 19:20:00] Cleo: My cousin Mia is a maybe",
    "[21/10/2026, 19:21:00] Ben: Tom is coming, he told me today",
    "[21/10/2026, 19:22:00] Ana: So that is six plus maybe Mia",
    "[21/10/2026, 19:23:00] Ben: The big cabin sleeps eight, fine",
    "[21/10/2026, 19:24:00] Cleo: What about firewood?",
    "[21/10/2026, 19:25:00] Ana: There is a stack at the cabin already",
    "[21/10/2026, 19:26:00] Ana: Good, one less thing",
    "[21/10/2026, 19:27:00] Ben: I will bring the big pan again",
    "[21/10/2026, 19:28:00] Cleo: And I will bring cards and the speaker",
    "[21/10/2026, 19:29:00] Ben: Weather looks clear for Saturday",
    "[21/10/2026, 19:30:00] Ana: If it rains we still go, agreed?",
    "[21/10/2026, 19:31:00] Ben: Agreed, rain or shine",
    "[21/10/2026, 19:32:00] Cleo: Rain or shine!",
    "[21/10/2026, 19:33:00] Ben: What time do we leave town?",
    "[21/10/2026, 19:34:00] Ana: Aim to arrive by 10am, so leave at eight",
    "[21/10/2026, 19:35:00] Ben: Early but worth it",
    "[21/10/2026, 19:36:00] Cleo: I will set three alarms",
    "[21/10/2026, 19:37:00] Ana: Bringing my guitar for the evening",
    "[21/10/2026, 19:38:00] Ana: Perfect. Saturday, the lake cabin, all of us",
    "[21/10/2026, 19:39:00] Ben: Cannot wait",
]
assert len(TRIP_CHAT_LINES) == 40

TRIP_CHAT_TITLE = "The cabin trip argument (demo)"
NEWSLETTER_TITLE = "Riverbend October newsletter (demo)"
BOARD_TITLE = "Welcome to the Riverbend board"

# The newsletter's twelve sections — one chunk each, each one
# naming an event, exactly as Flow C buries them in prose.
NEWSLETTER_SECTIONS = [
    "Harvest Supper — Friday 6 November, 6.30pm at Riverbend Hall. Bring a dish to share.",
    "Projector Night — Saturday 7 November, 7pm at Riverbend Hall. The projector finally works.",
    "Garden Tidy — Sunday 8 November, 10am in the community garden. Gloves provided.",
    "Book Club — Tuesday 10 November, 7pm at Riverbend Hall. This month: a very long novel.",
    "Choir Practice — Wednesday 11 November, 6pm at Riverbend Hall. New singers welcome.",
    "Repair Café — Saturday 14 November, 11am at Riverbend Hall. Bring one broken thing.",
    "Lantern Walk — Friday 20 November, 5.30pm from the hall steps. Lanterns provided.",
    "Baking Morning — Saturday 21 November, 9am at Riverbend Hall. Flour everywhere, as usual.",
    "Story Night — Friday 27 November, 7pm at Riverbend Hall. One story each, no notes.",
    "River Clean-Up — Saturday 28 November, 10am at the north bank. Waders in the shed.",
    "Games Evening — Saturday 28 November, 6pm at Riverbend Hall. The big tournament continues.",
    "Winter Planning — Tuesday 1 December, 7pm at Riverbend Hall. The whole winter, decided.",
]

# A month of published history: (title, start UTC, end UTC, place).
# Times are UTC-naive, the house convention (D-21); the hall is
# in Indianapolis, so 23:00 UTC is an early-evening local start.
HISTORY_EVENTS = [
    ("Food Bank Sorting Morning", datetime(2026, 10, 3, 14, 0), datetime(2026, 10, 3, 17, 0), "Riverbend Hall"),
    ("Harvest Supper", datetime(2026, 11, 6, 23, 30), datetime(2026, 11, 7, 2, 0), "Riverbend Hall"),
    ("Projector Night", datetime(2026, 11, 8, 0, 0), datetime(2026, 11, 8, 2, 30), "Riverbend Hall"),
    ("Garden Tidy", datetime(2026, 11, 8, 15, 0), datetime(2026, 11, 8, 18, 0), "Community Garden"),
    ("Book Club", datetime(2026, 11, 11, 0, 0), datetime(2026, 11, 11, 2, 0), "Riverbend Hall"),
    ("Repair Café", datetime(2026, 11, 14, 16, 0), datetime(2026, 11, 14, 19, 0), "Riverbend Hall"),
    ("Lantern Walk", datetime(2026, 11, 20, 22, 30), datetime(2026, 11, 21, 0, 30), "Riverbend Hall steps"),
    ("River Clean-Up", datetime(2026, 11, 28, 15, 0), datetime(2026, 11, 28, 18, 0), "North bank"),
]


def _group(db: Session, slug: str) -> Group:
    return db.query(Group).filter(Group.slug == slug).one()


def load_demo_story(db: Session, seeds_dir: Path | str | None = None) -> dict:
    """Load the whole demo story; return created-vs-present counts.

    Safe to call repeatedly: the second call creates nothing.
    Each underlying service commits its own work (the house
    commit-before-answer rule), so a half-finished load is still
    a consistent database — re-running simply fills the gaps.
    """
    seeds = Path(seeds_dir) if seeds_dir is not None else SEEDS_DIR
    base = seed_service.load_seed_file(db, seeds / "demo.json")
    story = seed_service.load_seed_file(db, seeds / "demo_story.json")
    counts = {
        "users_created": base["created"]["users"] + story["created"]["users"],
        "groups_created": base["created"]["groups"] + story["created"]["groups"],
        "sources_created": 0,
        "events_created": 0,
        "announcements_created": 0,
    }

    trust = _group(db, "riverbend-community-trust")
    club = _group(db, "northside-hiking-club")

    # The 40-message trip argument, pasted into the club.
    if db.query(Source).filter(Source.group_id == club.id, Source.title == TRIP_CHAT_TITLE).one_or_none() is None:
        source_service.create_source(
            db, actor_label="user:demo-seed", group=club, kind="chat_paste",
            title=TRIP_CHAT_TITLE, granted_scope="demo seed: the club's own chat, pasted by its owner",
            chunks=paste_service.parse_paste("\n".join(TRIP_CHAT_LINES)),
        )
        counts["sources_created"] += 1

    # The twelve-section newsletter, archived for the Trust.
    if db.query(Source).filter(Source.group_id == trust.id, Source.title == NEWSLETTER_TITLE).one_or_none() is None:
        source_service.create_source(
            db, actor_label="user:demo-seed", group=trust, kind="newsletter",
            title=NEWSLETTER_TITLE, granted_scope="demo seed: the Trust's own newsletter",
            chunks=[{"text": section} for section in NEWSLETTER_SECTIONS],
        )
        counts["sources_created"] += 1

    # The bulletin board's pinned welcome.
    if db.query(Announcement).filter(Announcement.group_id == trust.id, Announcement.title == BOARD_TITLE).one_or_none() is None:
        owner_row = (
            db.query(Membership)
            .filter(Membership.group_id == trust.id, Membership.role == "owner")
            .one_or_none()
        )
        owner = owner_row.user_id if owner_row is not None else None
        announcement_service.create_announcement(
            db, actor_label="user:demo-seed", actor_user_id=owner, actor_is_seat=False,
            group=trust, title=BOARD_TITLE, pinned=True,
            body="Everything the Trust plans starts here. Flyers, newsletters, and chats become drafts; a person always says yes before anything is published.",
        )
        counts["announcements_created"] += 1

    # A month of published history on the Trust's calendar.
    for title, starts, ends, place in HISTORY_EVENTS:
        if db.query(Event).filter(Event.group_id == trust.id, Event.title == title).one_or_none() is None:
            event_service.create_event(
                db, actor_label="user:demo-seed", group=trust,
                data={
                    "title": title, "starts_at": starts, "ends_at": ends,
                    "place_name": place, "status": "published",
                    "description": "Part of the demo month of history (B-147).",
                },
            )
            counts["events_created"] += 1

    return counts
