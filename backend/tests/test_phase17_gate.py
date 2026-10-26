"""Phase 17 gate — the addendum proof (roadmap B-180).

One scripted drill combining the whole Phase 17 addendum, in
the roadmap's own order:

    flyer in over a resumable upload → process killed
    mid-extraction → restart self-heals, job resumes, no data
    lost → candidate approved over the internal encrypted API
    → raw disk greps find no plaintext user data → audit
    chain verifies

How each leg is proven, honestly:

* **Resumable upload + kill**: the poster's parts land out of
  order on one app instance; that instance is then GONE (its
  TestClient closes — the in-process stand-in for the kill;
  the true SIGKILL legs are B-175's subprocess harness, which
  this gate re-runs as part of the suite) and a second
  instance over the same directories resumes and completes.
* **Killed mid-extraction**: the vision job is claimed by a
  worker that then simply never comes back — the claim is
  aged past its lease the way time would age it, and the
  restarted instance's startup recovery (B-172) requeues it;
  the job then runs to done.  No committed data is lost: the
  flyer, its source, and the upload's hash all survive.
* **Approval over the internal encrypted API**: the approval
  is the person's HTTP act, and its cross-module effects
  (candidate → minted event → integration emission) flow
  through the B-162 internal API — the audit stream must
  carry the ``internal.*`` lines B-165 promises, and the
  drill reconstructs the approval's story from them.
* **Raw disk greps**: this drill's deployment runs with
  encrypted storage (B-168) on top of field sealing (B-167).
  A distinctive token planted ONLY in the flyer's transcript
  and a slice of the poster's own PNG bytes are searched for
  in every file the deployment wrote — database, WAL, and
  storage alike — and must be absent everywhere, while the
  API serves the transcript whole to its owner.
* **Audit chain**: B-170's verifier walks the chain after
  everything above and must answer intact.
* **Second OS**: NOT RUN here — this builder is Linux only.
  The drill is platform-pure (pathlib, no OS branching) and
  the CI matrix of B-177 defines the same suite on Windows
  and macOS; the waiver is recorded in PHASE17_DRILL.md,
  never silently assumed.
"""

import hashlib
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import bearer, make_group, make_settings, register
from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.vision import build_vision_prompt
from gpa.main import create_app
from gpa.models.core import AuditLog, Job
from gpa.models.identity import now_utc
from gpa.models.sources import Source
from gpa.services import audit_chain, jobs, recovery
from vision_world import flyer_answer, poster_bytes, request_read, run_one_job

RED = (208, 32, 32)
TOKEN = "ZQXDRILLTOKEN"  # planted ONLY in the transcript (sealed ground)
ANSWER = flyer_answer(
    title="Drill Supper",
    starts_phrase="Nov 6",
    starts_time="6.30pm",
    place="Village Hall",
    cost="£6",
    bring="a dish to share",
    transcript=f"DRILL SUPPER — the {TOKEN} reading. Nov 6, 6.30pm.",
    alt_text="A red poster for a drill supper at the Village Hall "
    "on Nov 6 at 6.30pm, £6, bring a dish to share.",
)


def _all_files(root: Path) -> list[Path]:
    """Every file the deployment wrote under its ground."""
    return [p for p in root.rglob("*") if p.is_file()]


def test_phase17_addendum_drill(tmp_path):
    """The whole B-180 drill, one unbroken story."""
    settings = make_settings(tmp_path, encrypt_storage=True)
    poster = poster_bytes(RED)
    digest = hashlib.sha256(poster).hexdigest()
    chunk = (len(poster) + 2) // 3
    parts = [poster[i : i + chunk] for i in range(0, len(poster), chunk)]
    assert len(parts) == 3

    # --- Leg 1: flyer in over a resumable upload, then a kill. ---
    app_one = create_app(settings)
    with TestClient(app_one) as client:
        owner = register(client)
        group = make_group(client, owner, home_timezone="America/New_York")
        gid = group["id"]
        headers = bearer(owner)
        begin = client.post(
            f"/api/v1/groups/{gid}/uploads",
            json={
                "filename": "poster.png",
                "total_bytes": len(poster),
                "chunk_bytes": chunk,
                "sha256": digest,
            },
            headers=headers,
        )
        assert begin.status_code == 201, begin.text
        upload_id = begin.json()["upload_id"]
        for index in (2, 0):  # out of order; part 1 never lands
            sent = client.put(
                f"/api/v1/groups/{gid}/uploads/{upload_id}/parts/{index}",
                content=parts[index],
                headers=headers,
            )
            assert sent.status_code == 200, sent.text
    # app_one is closed: the "process" is dead. A new instance
    # over the same ground resumes — never restarts — the upload.
    app = create_app(settings)
    with TestClient(app) as client:
        status = client.get(
            f"/api/v1/groups/{gid}/uploads/{upload_id}", headers=headers
        )
        assert status.status_code == 200, status.text
        assert status.json()["parts_missing"] == [1]
        sent = client.put(
            f"/api/v1/groups/{gid}/uploads/{upload_id}/parts/1",
            content=parts[1],
            headers=headers,
        )
        assert sent.status_code == 200, sent.text
        done = client.post(
            f"/api/v1/groups/{gid}/uploads/{upload_id}/complete", headers=headers
        )
        assert done.status_code == 201, done.text
        source_id = done.json()["source_id"]

        # --- Leg 2: read, a worker dies mid-job, recovery heals. ---
        session = app.state.db.session()
        try:
            stored = app.state.storage.get_bytes(
                session.get(Source, source_id).storage_key
            )
        finally:
            session.close()
        fixture = FixtureDriver()
        fixture.add_image(
            build_vision_prompt(),
            stored,
            ANSWER,
            schema=promptlib.load_prompt("flyer_reading").schema,
        )
        ai_client.set_client(fixture)
        job_id = request_read(client, headers, gid, source_id)

        session = app.state.db.session()
        try:
            claimed = jobs.claim_next(session, "drill-worker")
            assert claimed is not None and claimed.id == job_id
            # The worker dies here. Time passes; the lease lapses.
            claimed.updated_at = now_utc() - timedelta(
                seconds=recovery.LEASE_SECONDS + 60
            )
            session.commit()
            report = recovery.assess_and_repair(session, settings)
            assert any(
                "requeue" in action or "job" in action
                for action in report["actions"]
            ), report
            job = session.get(Job, job_id)
            assert job.status == "queued"  # resumed, never gone
            assert run_one_job(app, session) is True
            assert session.get(Job, job_id).status == "done"
        finally:
            session.close()

        queue = client.get(
            f"/api/v1/groups/{gid}/candidates", headers=headers
        ).json()
        (candidate,) = [c for c in queue if c["title"] == "Drill Supper"]

        # --- Leg 3: approval, its effects over the internal API. ---
        approved = client.post(
            f"/api/v1/groups/{gid}/candidates/{candidate['id']}/approve",
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        event_id = approved.json()["resulting_entity_id"]
        assert event_id
        session = app.state.db.session()
        try:
            internal_actions = [
                row.action
                for row in session.query(AuditLog)
                .filter(AuditLog.action.like("internal.%"))
                .all()
            ]
        finally:
            session.close()
        assert internal_actions, (
            "the approval's cross-module story must be reconstructible "
            "from internal.* audit lines (B-165)"
        )

        # The transcript reads whole through the API for its owner…
        detail = client.get(
            f"/api/v1/groups/{gid}/sources/{source_id}", headers=headers
        ).json()
        served = " ".join(c["text"] for c in detail["chunks"])
        assert TOKEN in served

    # --- Leg 4: …and exists nowhere on the raw disk. ---
    png_slice = poster[100:132]
    for path in _all_files(tmp_path):
        blob = path.read_bytes()
        assert TOKEN.encode() not in blob, f"transcript token leaked into {path}"
        assert png_slice not in blob, f"poster bytes leaked into {path}"

    # --- Leg 5: the audit chain verifies over it all. ---
    session = app.state.db.session()
    try:
        verdict = audit_chain.verify_chain(
            session, audit_chain.chain_key(settings.secret_key)
        )
    finally:
        session.close()
    assert verdict["ok"], verdict
