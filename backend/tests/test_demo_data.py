"""Demo data, final (roadmap B-147).

The seed is the story: two named groups with a cast, a month
of published history, a pinned board post, the 40-message trip
argument, and the twelve-section newsletter — all reproducible
from ``demo.sh`` / ``scripts/seed.py --demo`` on a blank
database, and all idempotent: a second load creates nothing.
"""

from sqlalchemy import text

from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.nonprofit import Announcement
from gpa.models.sources import Source
from gpa.services import sources as source_service
from gpa.services.demo import NEWSLETTER_TITLE, TRIP_CHAT_TITLE, load_demo_story


def test_demo_story_loads_the_whole_cast_and_month(app, db_session):
    counts = load_demo_story(db_session)
    assert counts["groups_created"] == 3  # B-010 demo group + Trust + Club
    assert counts["sources_created"] == 2
    assert counts["events_created"] == 8
    assert counts["announcements_created"] == 1

    groups = {g.slug for g in db_session.query(Group).all()}
    assert {"demo-community-group", "riverbend-community-trust", "northside-hiking-club"} <= groups
    users = db_session.execute(text("SELECT COUNT(*) FROM users")).scalar()
    memberships = db_session.execute(text("SELECT COUNT(*) FROM memberships")).scalar()
    assert users == 7
    assert memberships >= 9  # 3 owners + 6 enrolled cast members

    club = db_session.query(Group).filter(Group.slug == "northside-hiking-club").one()
    trip = db_session.query(Source).filter(Source.group_id == club.id, Source.title == TRIP_CHAT_TITLE).one()
    assert len(source_service.list_chunks(db_session, trip)) == 40

    trust = db_session.query(Group).filter(Group.slug == "riverbend-community-trust").one()
    letter = db_session.query(Source).filter(Source.group_id == trust.id, Source.title == NEWSLETTER_TITLE).one()
    assert len(source_service.list_chunks(db_session, letter)) == 12

    board = db_session.query(Announcement).filter(Announcement.group_id == trust.id).all()
    assert len(board) == 1 and board[0].pinned is True

    events = db_session.query(Event).filter(Event.group_id == trust.id, Event.status == "published").all()
    assert len(events) == 8
    months = {(e.starts_at.year, e.starts_at.month) for e in events}
    assert months == {(2026, 10), (2026, 11)}  # a month of history, spilling either side


def test_demo_story_is_idempotent(app, db_session):
    load_demo_story(db_session)
    second = load_demo_story(db_session)
    assert second == {
        "users_created": 0, "groups_created": 0, "sources_created": 0,
        "events_created": 0, "announcements_created": 0,
    }
    assert db_session.execute(text("SELECT COUNT(*) FROM sources")).scalar() == 2 + 8  # 2 story + 8 manual event sources
    assert db_session.execute(text("SELECT COUNT(*) FROM events")).scalar() == 8
