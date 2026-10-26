"""Key management tests (roadmap B-169).

The acceptances: **a mint → lose → restore round trip** —
codes are minted, the master is "lost", one code brings it
back and the group's sealed data opens again; **a backup
bundle grepped for key material** — the real B-146 bundle,
searched for the master, a data key, and the codes in raw
and base64 forms. Plus the laws around them: minting kills
the old set, a code spends once, rotation re-wraps without
touching sealed data.
"""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_settings
from gpa.crypto import fields
from gpa.db import Database
from gpa.errors import AppError
from gpa.services import keymgmt

MASTER_A = base64.b64encode(b"A" * 32).decode()
MASTER_B = base64.b64encode(b"B" * 32).decode()


def _deployment(tmp_path, master_b64):
    settings = make_settings(tmp_path, master_key_b64=master_b64)
    fields.set_settings(settings)
    db = Database(settings.database_url)
    db.create_all()
    # group_keys rows carry a foreign key to a real group;
    # the tests seal for group "g1", so "g1" must exist.
    from gpa.models.groups import Group

    session = db.session()
    try:
        session.add(Group(id="g1", name="Test Group", slug="test-group"))
        session.commit()
    finally:
        session.close()
    return settings, db


def test_mint_lose_restore_round_trip(tmp_path):
    """THE B-169 acceptance: mint codes, seal data, lose the
    master (a deployment configured with a stranger's key),
    restore it from one code, and read the data again."""
    settings, db = _deployment(tmp_path, MASTER_A)
    session = db.session()
    try:
        codes = keymgmt.mint_recovery_codes(session, settings)
        assert len(codes) == 8
        fields.set_settings(settings)
        sealed = fields.seal_text(session, "g1", "chunk.text", "the picnic ledger")
        session.commit()

        # Lose the master: a new deployment, wrong key.
        lost_settings = make_settings(tmp_path, master_key_b64=MASTER_B)
        fields.set_settings(lost_settings)
        with pytest.raises(AppError):
            fields.open_text(session, "g1", "chunk.text", sealed)

        # Restore: one code returns the escrowed master...
        recovered = keymgmt.recover_master_key(session, lost_settings, codes[3])
        assert base64.b64encode(recovered).decode() == MASTER_A
        # ...installed as the deployment's key, the data opens.
        restored = make_settings(
            tmp_path, master_key_b64=base64.b64encode(recovered).decode()
        )
        fields.set_settings(restored)
        assert fields.open_text(session, "g1", "chunk.text", sealed) == "the picnic ledger"
    finally:
        session.close()
        db.engine.dispose()


def test_codes_are_one_time_and_minting_kills_the_old_set(tmp_path):
    settings, db = _deployment(tmp_path, MASTER_A)
    session = db.session()
    try:
        first = keymgmt.mint_recovery_codes(session, settings)
        keymgmt.recover_master_key(session, settings, first[0])
        with pytest.raises(AppError) as spent:
            keymgmt.recover_master_key(session, settings, first[0])
        assert spent.value.code == "recovery_code_invalid"

        second = keymgmt.mint_recovery_codes(session, settings)
        assert second != first
        with pytest.raises(AppError):
            keymgmt.recover_master_key(session, settings, first[1])
        with pytest.raises(AppError):
            keymgmt.recover_master_key(session, settings, "ZZZZZ-ZZZZZ")
        # The new set lives.
        keymgmt.recover_master_key(session, settings, second[0])
    finally:
        session.close()
        db.engine.dispose()


def test_rotation_rewraps_and_sealed_data_survives(tmp_path):
    settings_a, db = _deployment(tmp_path, MASTER_A)
    session = db.session()
    try:
        sealed = fields.seal_text(session, "g1", "chunk.text", "rotation witness")
        session.commit()
        from gpa.models.crypto import GroupKey

        before = session.query(GroupKey).filter(GroupKey.group_id == "g1").one()
        version_before = before.version

        settings_b = make_settings(tmp_path, master_key_b64=MASTER_B)
        fields.set_settings(settings_b)
        result = keymgmt.rotate_master_key(
            session, settings_b, old_master=base64.b64decode(MASTER_A)
        )
        assert result == {"groups": 1}
        after = session.query(GroupKey).filter(GroupKey.group_id == "g1").one()
        assert after.version == version_before + 1
        assert after.rotated_at is not None
        assert fields.open_text(session, "g1", "chunk.text", sealed) == "rotation witness"

        # A rotation attempted with the WRONG old key stops
        # whole: nothing is re-wrapped by a failed rotation.
        wrapped_now = after.wrapped_key
        with pytest.raises(AppError):
            keymgmt.rotate_master_key(
                session, settings_b, old_master=b"Z" * 32
            )
        session.expire_all()
        still = session.query(GroupKey).filter(GroupKey.group_id == "g1").one()
        assert still.wrapped_key == wrapped_now
    finally:
        session.close()
        db.engine.dispose()


def test_backup_bundle_holds_no_key_material(tmp_path):
    """THE B-169 backup acceptance: the REAL B-146 bundle
    script runs against this deployment; every file in the
    bundle is grepped for the master key, an unwrapped data
    key, and every recovery code — raw and base64 — and
    none is found."""
    settings, db = _deployment(tmp_path, MASTER_A)
    session = db.session()
    try:
        codes = keymgmt.mint_recovery_codes(session, settings)
        fields.set_settings(settings)
        fields.seal_text(session, "g1", "chunk.text", "bundle witness")
        session.commit()
        data_key = fields._existing_data_key(session, "g1", settings)
    finally:
        session.close()
        db.engine.dispose()

    bundle = tmp_path / "bundle"
    env = dict(
        os.environ,
        GPA_DATABASE_URL=settings.database_url,
        GPA_STORAGE_DIR=str(settings.storage_dir),
        GPA_SECRET_KEY=settings.secret_key,
        GPA_MASTER_KEY_B64=MASTER_A,
        GPA_ENV="test",
    )
    root = Path(__file__).resolve().parent.parent
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "backup.py"), "--out", str(bundle)],
        env=env, capture_output=True, text=True, cwd=root, timeout=120,
    )
    assert run.returncode == 0, run.stderr

    haystack = b""
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            haystack += path.read_bytes()
    needles = [
        base64.b64decode(MASTER_A),
        MASTER_A.encode(),
        data_key,
        base64.b64encode(data_key),
    ] + [code.encode() for code in codes]
    for needle in needles:
        assert needle not in haystack
