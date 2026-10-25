"""Seed chassis acceptance (B-009/B-010): the double-seed test —
running the loader twice changes nothing the second time."""

from pathlib import Path

from sqlalchemy import text

from gpa.services.seed import load_seed_file

DEMO = Path(__file__).resolve().parent.parent / "seeds" / "demo.json"


def test_double_seed_changes_nothing(app, db_session):
    first = load_seed_file(db_session, DEMO)
    assert first["created"] == {"users": 1, "groups": 1}
    second = load_seed_file(db_session, DEMO)
    assert second["created"] == {"users": 0, "groups": 0}
    assert second["already_present"] == {"users": 1, "groups": 1}
    users = db_session.execute(text("SELECT COUNT(*) FROM users")).scalar()
    groups = db_session.execute(text("SELECT COUNT(*) FROM groups")).scalar()
    memberships = db_session.execute(text("SELECT COUNT(*) FROM memberships")).scalar()
    assert (users, groups, memberships) == (1, 1, 1)


def test_demo_login_works(app, client):
    """The demo seed's user can actually sign in (B-010 demo)."""
    session = app.state.db.session()
    try:
        load_seed_file(session, DEMO)
    finally:
        session.close()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "demo@example.org", "password": "demo-password-123"},
    )
    assert response.status_code == 200
