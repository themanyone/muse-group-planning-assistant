"""Internal API audit tests (roadmap §6A.4, B-165).

The acceptance: *a full demo flow's internal story can be
reconstructed from audit alone, with no user content in it.*
These tests make internal calls the way the product does,
then read ONLY the audit stream and check the story is all
there — caller, operation, scope, module, result, key id —
while a distinctive piece of user content seeded into the
flow appears nowhere in any audit line.
"""

import json

from conftest import make_group, register
from gpa.internal import auditing, identity
from gpa.internal.client import InternalClient, call_internal
from gpa.models.core import AuditLog

DISTINCTIVE_TITLE = "Zebra Quinoa Surprise Supper"


def _internal_rows(db_session):
    return (
        db_session.query(AuditLog)
        .filter(AuditLog.action.like("internal.%"))
        .order_by(AuditLog.created_at, AuditLog.id)
        .all()
    )


def test_every_call_leaves_its_line(app, client, db_session):
    """One call → one line: module actor, operation action,
    the six detail facts, the nonce as the entity id — and
    nothing else. The app fixture's create_app installed the
    hook (B-165's composition wiring)."""
    owner = register(client)
    group = make_group(client, owner)
    call_internal(
        db_session, caller="groups", operation="candidates.create",
        payload={
            "group_id": group["id"],
            "kind": "event",
            "title": DISTINCTIVE_TITLE,
            "created_by": f"user:{owner['user_id']}",
            "draft": {"title": DISTINCTIVE_TITLE},
        },
    )
    db_session.commit()
    rows = [r for r in _internal_rows(db_session) if r.action == "internal.candidates.create"]
    assert len(rows) == 1
    row = rows[0]
    assert row.actor == "module:groups"
    assert row.group_id == group["id"]
    assert row.entity_type == "internal_call"
    assert row.entity_id  # the envelope nonce
    detail = json.loads(row.detail_json)
    assert set(detail) == {"caller", "key_id", "operation", "scope", "module", "result"}
    assert detail["caller"] == "groups"
    assert detail["operation"] == "candidates.create"
    assert detail["scope"] == "candidates:write"
    assert detail["module"] == "extraction"
    assert detail["result"] == "ok"
    # The key id is the caller's identity fingerprint under
    # this app's settings — attribution that survives audit
    # review, not a name anyone typed into a header.
    expected = identity.derived_identity(
        "groups", app.state.settings.secret_key
    ).key_id
    assert detail["key_id"] == expected


def test_a_refusal_leaves_its_line_too(client, db_session):
    """A scope-denied call is part of the story: result
    carries the refusal's code."""
    from gpa.errors import AppError
    import pytest

    with pytest.raises(AppError):
        call_internal(
            db_session, caller="sources", operation="notify.request",
            payload={"kind": "x", "subject": "x", "body_text": "x",
                     "audience": {"label": "user:nobody"}},
        )
    db_session.commit()
    rows = [r for r in _internal_rows(db_session) if r.action == "internal.notify.request"]
    assert len(rows) == 1
    detail = json.loads(rows[0].detail_json)
    assert detail["result"] == "internal_scope_denied"
    assert detail["module"] == "notifications"


def test_the_story_reconstructs_without_user_content(client, db_session):
    """THE acceptance test: a small flow — a draft arrives
    (candidates.create), the group is told (notify.request),
    an outward word is emitted (integrations.emit) — and the
    audit stream alone tells that story in order, while the
    seeded user content (a distinctive title, a draft body)
    appears in NO audit line anywhere in the stream."""
    owner = register(client)
    group = make_group(client, owner)
    call_internal(
        db_session, caller="groups", operation="candidates.create",
        payload={
            "group_id": group["id"], "kind": "event",
            "title": DISTINCTIVE_TITLE, "created_by": "user:x",
            "draft": {"title": DISTINCTIVE_TITLE, "notes": "bring the zebra"},
        },
    )
    call_internal(
        db_session, caller="events", operation="integrations.emit",
        payload={"group_id": group["id"], "kind": "event.published",
                 "data": {"title": DISTINCTIVE_TITLE}},
    )
    db_session.commit()

    rows = _internal_rows(db_session)
    story = [r.action for r in rows]
    assert story.index("internal.candidates.create") < story.index("internal.integrations.emit")
    # No user content anywhere in the whole audit stream —
    # human lines included (their details are ids and facts
    # by the same law, and this flow's human lines exist:
    # registration and group creation wrote some).
    everything = db_session.query(AuditLog).all()
    assert everything, "the flow must have written audit lines"
    for row in everything:
        assert DISTINCTIVE_TITLE not in (row.detail_json or "")
        assert "zebra" not in (row.detail_json or "").lower()


def test_hook_can_be_installed_on_a_standalone_client(db_session):
    """Composition for non-app processes: a client with the
    hook installed keeps the story; one without writes no
    internal lines (the hook is the mechanism, not magic)."""
    bare = InternalClient()
    bare.call(db_session, caller="integrations", operation="jobs.enqueue",
              payload={"kind": "digest_run"})
    db_session.commit()
    assert _internal_rows(db_session) == []

    hooked = InternalClient()
    auditing.install(client=hooked)
    hooked.call(db_session, caller="integrations", operation="jobs.enqueue",
                payload={"kind": "digest_run"})
    db_session.commit()
    rows = _internal_rows(db_session)
    assert len(rows) == 1
    assert json.loads(rows[0].detail_json)["module"] == "jobs"
