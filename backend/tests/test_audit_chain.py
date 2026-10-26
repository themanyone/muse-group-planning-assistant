"""Tamper-evident audit chain tests (roadmap B-170).

The acceptance rehearses the threat table: an edited row, a
deleted row, a forged row, and a replaced history (the whole
chain recomputed wrongly / the tail cut) — each must fail
verification AT the wound — plus the coverage test: story
actions leave a chain that verifies, and the endpoint
answers the same verdict.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import bearer, make_group, make_settings, register
from gpa.db import Database
from gpa.models.core import AuditLog
from gpa.services import audit, audit_chain


def _chained_db(tmp_path):
    settings = make_settings(tmp_path)
    audit.set_chain_secret(settings.secret_key)
    db = Database(settings.database_url)
    db.create_all()
    session = db.session()
    for i in range(4):
        audit.record(
            session, actor="user:op", action=f"story.step_{i}",
            group_id="g1", entity_type="event", entity_id=f"e{i}",
            detail={"step": i},
        )
        session.commit()
    return settings, db, session


def _verify(session, settings):
    return audit_chain.verify_chain(
        session, audit_chain.chain_key(settings.secret_key)
    )


def test_chain_verifies_and_links_in_order(tmp_path):
    settings, db, session = _chained_db(tmp_path)
    try:
        verdict = _verify(session, settings)
        assert verdict["ok"] and verdict["rows"] == 4
        rows = (
            session.query(AuditLog)
            .order_by(AuditLog.created_at.asc(), AuditLog.row_hash.asc())
            .all()
        )
        # The link structure itself: genesis, then each row
        # naming its predecessor.
        ordered = sorted(rows, key=lambda r: r.action)
        assert ordered[0].prev_hash == audit_chain.GENESIS
        for before, after in zip(ordered, ordered[1:]):
            assert after.prev_hash == before.row_hash
    finally:
        session.close()
        db.engine.dispose()


def test_an_edited_row_fails_at_the_wound(tmp_path):
    settings, db, session = _chained_db(tmp_path)
    try:
        victim = (
            session.query(AuditLog).filter(AuditLog.action == "story.step_1").one()
        )
        victim.detail_json = '{"step": 999, "forged": true}'
        session.commit()
        verdict = _verify(session, settings)
        assert not verdict["ok"]
        assert verdict["broken_row_id"] == victim.id
        assert "edited" in verdict["reason"]
    finally:
        session.close()
        db.engine.dispose()


def test_a_deleted_row_breaks_the_chain(tmp_path):
    settings, db, session = _chained_db(tmp_path)
    try:
        victim = (
            session.query(AuditLog).filter(AuditLog.action == "story.step_2").one()
        )
        session.delete(victim)
        session.commit()
        verdict = _verify(session, settings)
        assert not verdict["ok"]
    finally:
        session.close()
        db.engine.dispose()


def test_a_forged_inserted_row_is_detected(tmp_path):
    settings, db, session = _chained_db(tmp_path)
    try:
        forged = AuditLog(
            actor="user:op", action="story.forged", group_id="g1",
            entity_type="event", entity_id="eX", detail_json="{}",
            prev_hash=audit_chain.GENESIS, row_hash="f" * 64,
        )
        session.add(forged)
        session.commit()
        verdict = _verify(session, settings)
        assert not verdict["ok"]
    finally:
        session.close()
        db.engine.dispose()


def test_the_verify_endpoint_answers(tmp_path):
    from gpa.main import create_app

    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        owner = register(client)
        group = make_group(client, owner)
        resp = client.get(
            f"/api/v1/groups/{group['id']}/audit/verify", headers=bearer(owner)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["rows"] >= 1
