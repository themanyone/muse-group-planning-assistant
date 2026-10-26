"""Error-correction services (roadmap B-173).

The acceptance, in the roadmap's words, is a kill-each-
dependency suite: for every external dependency (Meta model
API, mail, Facebook, Google) the breaker opens after a run
of failures, refuses further calls WITHOUT touching the
dependency, half-opens for one probe after its cooldown, and
closes on a successful probe — while work bound to other
dependencies, and work bound to none, sails on (the
bulkhead promise). At the job chassis the same story reads:
a job whose dependency is down is DEFERRED with its attempts
untouched, never burned against a wall and never dead-
lettered for someone else's outage.

The second half proves the data-repair crew: four kinds of
planted damage (a finished job's stale progress, orphaned
provenance, gapped chunk positions, a flyer alt-text status
lying about its text, an emptied digest) are fixed forward
by one ``data_repair`` run, the audit names every fix, and a
second run finds nothing left to fix.
"""

import pytest
from sqlalchemy import text

from gpa.models.core import AuditLog, Job
from gpa.models.groups import Candidate, Group
from gpa.models.notifications import Digest
from gpa.models.sources import Flyer, Provenance, Source, SourceChunk
from gpa.services import digests as digest_service
from gpa.services import jobs as job_service
from gpa.services import repair as repair_service
from gpa.services import resilience

from conftest import make_group, register


class FakeClock:
    """A monotonic clock the test walks by hand, so cooldowns
    are crossed by assignment, never by sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_resilience():
    """Breaker state is process memory: every test here starts
    and ends with a clean registry (composition also resets it
    per app, but these tests configure their own numbers)."""
    resilience.reset_all()
    yield
    resilience.reset_all()


def _boom(calls: list) -> object:
    """A dependency call that always fails, counting its runs."""

    def failing():
        calls.append(1)
        raise RuntimeError("provider is down")

    return failing


# ----------------------------------------------------------------
# The kill-each-dependency suite (breaker + bulkhead mechanics)
# ----------------------------------------------------------------


@pytest.mark.parametrize("dependency", resilience.DEPENDENCIES)
def test_kill_each_dependency_opens_its_breaker(dependency):
    """For EVERY named dependency: one failure at threshold 1
    opens its breaker, and the next call is refused with the
    dependency's plain name — without the call running at all.
    The other breakers stay closed (no shared fate)."""
    clock = FakeClock()
    resilience.configure(
        dependency, failure_threshold=1, cooldown_seconds=60.0, clock=clock
    )
    calls: list = []
    with pytest.raises(RuntimeError):
        resilience.call(dependency, _boom(calls))
    assert calls == [1]
    assert resilience.breaker_for(dependency).state == "open"

    with pytest.raises(resilience.DependencyUnavailable) as refused:
        resilience.call(dependency, _boom(calls))
    assert calls == [1], "an open breaker must not touch the dependency"
    label = resilience.DEPENDENCY_LABELS[dependency]
    assert label in str(refused.value)

    for other in resilience.DEPENDENCIES:
        if other != dependency:
            assert resilience.breaker_for(other).state == "closed"


def test_half_open_probe_decides():
    """After the cooldown exactly one probe passes: a failed
    probe re-opens the breaker, a successful one closes it and
    clears the failure run."""
    clock = FakeClock()
    resilience.configure(
        "google", failure_threshold=2, cooldown_seconds=30.0, clock=clock
    )
    calls: list = []
    for _ in range(2):
        with pytest.raises(RuntimeError):
            resilience.call("google", _boom(calls))
    assert resilience.breaker_for("google").state == "open"

    clock.advance(29.0)  # cooldown not yet served
    with pytest.raises(resilience.DependencyUnavailable):
        resilience.call("google", _boom(calls))
    assert len(calls) == 2

    clock.advance(2.0)  # probe window: the probe runs and fails
    with pytest.raises(RuntimeError):
        resilience.call("google", _boom(calls))
    assert len(calls) == 3
    assert resilience.breaker_for("google").state == "open"

    clock.advance(31.0)  # second probe: success closes
    assert resilience.call("google", lambda: "ok") == "ok"
    breaker = resilience.breaker_for("google")
    assert breaker.state == "closed"
    assert breaker.consecutive_failures == 0


def test_bulkhead_full_refuses_only_that_dependency():
    """A full mail compartment refuses mail work while Google
    work sails through the same registry — one integration's
    failure cannot starve the worker."""
    resilience.configure("mail", max_in_flight=1)
    bulkhead = resilience.bulkhead_for("mail")
    assert bulkhead.acquire() is True  # the one slot, held by "slow work"

    ran: list = []
    with pytest.raises(resilience.DependencyUnavailable):
        resilience.call("mail", lambda: ran.append("mail"))
    assert ran == [], "a full bulkhead refuses before the call runs"

    assert resilience.call("google", lambda: "sailed") == "sailed"
    bulkhead.release()
    assert resilience.call("mail", lambda: "now") == "now"


# ----------------------------------------------------------------
# The same story at the job chassis: defer, never burn
# ----------------------------------------------------------------


def _swap_handler(kind: str, handler):
    """Replace a job handler for one test and hand back the
    original — the registry is process-global, so a swap that
    is not undone would poison every later test."""
    original = job_service.HANDLERS.get(kind)
    job_service.register_handler(kind, handler)
    return original


def _restore_handler(kind: str, original) -> None:
    if original is None:
        job_service.HANDLERS.pop(kind, None)
    else:
        job_service.register_handler(kind, original)


def test_mail_outage_defers_jobs_without_spending_attempts(app, db_session):
    """Kill mail: notify jobs fail for real twice (attempts
    spent honestly), the breaker opens, and from then on
    notify jobs DEFER — queued, attempts frozen, error field
    naming the mail provider in plain words — while an
    unrelated job (data_repair, bound to no dependency) runs
    to done in the middle of the outage."""
    clock = FakeClock()
    resilience.configure(
        "mail", failure_threshold=2, cooldown_seconds=30.0, clock=clock
    )
    settings = app.state.settings

    def dead_mail(db, payload):
        raise RuntimeError("SMTP connection refused")

    original = _swap_handler("notify", dead_mail)
    try:
        first = job_service.enqueue(db_session, settings, kind="notify", payload={})
        second = job_service.enqueue(db_session, settings, kind="notify", payload={})
        repair_job = job_service.enqueue(
            db_session, settings, kind="data_repair", payload={}
        )

        # Two real runs, two real failures (one per job — the
        # claim order is by due time): the breaker opens.
        assert job_service.process_one(db_session, settings, "w-b173")
        assert job_service.process_one(db_session, settings, "w-b173")
        db_session.expire_all()
        attempts_after_open = {
            job.id: job_service.get_job(db_session, job.id).attempts
            for job in (first, second)
        }
        assert attempts_after_open == {first.id: 1, second.id: 1}
        assert resilience.breaker_for("mail").state == "open"

        # A mail job born DURING the outage, plus everything
        # already queued, now processes or defers.
        third = job_service.enqueue(db_session, settings, kind="notify", payload={})
        for _ in range(8):
            if not job_service.process_one(db_session, settings, "w-b173"):
                break
        db_session.expire_all()
        first = job_service.get_job(db_session, first.id)
        second = job_service.get_job(db_session, second.id)
        third = job_service.get_job(db_session, third.id)
        repair_job = job_service.get_job(db_session, repair_job.id)

        # The unrelated job ran to done despite the outage.
        assert repair_job.status == "done"
        # No mail job died or burned attempts against the wall:
        # all rest queued with a plain-words deferral, attempts
        # exactly where the real failures left them — and the
        # job born during the outage spent none at all.
        for job in (first, second, third):
            assert job.status == "queued"
            assert "Deferred:" in (job.last_error or "")
            assert "mail provider" in (job.last_error or "")
        assert first.attempts == 1 and second.attempts == 1
        assert third.attempts == 0, "a deferred job spends no attempt"
    finally:
        _restore_handler("notify", original)


def test_meta_outage_defers_extraction_but_not_mail(app, db_session):
    """Kill the Meta API instead: extraction defers while a
    mail-bound job (breaker closed) still runs — the bulkhead
    promise read from the other side."""
    clock = FakeClock()
    resilience.configure(
        "meta_ai", failure_threshold=1, cooldown_seconds=30.0, clock=clock
    )
    settings = app.state.settings

    def dead_model(db, payload):
        raise RuntimeError("model endpoint timed out")

    def fine_mail(db, payload):
        return None

    original_extract = _swap_handler("extract", dead_model)
    original_notify = _swap_handler("notify", fine_mail)
    try:
        extract_job = job_service.enqueue(
            db_session, settings, kind="extract", payload={}
        )
        mail_job = job_service.enqueue(db_session, settings, kind="notify", payload={})

        assert job_service.process_one(db_session, settings, "w-b173")
        assert resilience.breaker_for("meta_ai").state == "open"
        for _ in range(4):
            if not job_service.process_one(db_session, settings, "w-b173"):
                break
        db_session.expire_all()
        extract_job = job_service.get_job(db_session, extract_job.id)
        mail_job = job_service.get_job(db_session, mail_job.id)
        assert mail_job.status == "done"
        assert extract_job.status == "queued"
        assert extract_job.attempts == 1, "only the real run spent an attempt"
        assert "Meta model API" in (extract_job.last_error or "")
    finally:
        _restore_handler("extract", original_extract)
        _restore_handler("notify", original_notify)


# ----------------------------------------------------------------
# The data-repair crew
# ----------------------------------------------------------------


def _plant_damage(client, db_session):
    """Build one group and plant one of each damage kind.
    Returns (group_id, digest_id)."""
    owner = register(client)
    group = make_group(client, owner)
    group_id = group["id"]
    group_row = db_session.get(Group, group_id)

    # (a) A finished job resting at a stale derived progress.
    stale_job = Job(kind="notify", payload_json="{}", status="done", progress=40)
    db_session.add(stale_job)

    # (b) A source with gapped/duplicated chunk positions, a
    # candidate, and provenance pointing at a chunk and a
    # candidate that do not exist (planted with FK checks off,
    # the shape a hard delete on a lax connection leaves).
    source = Source(
        group_id=group_id, kind="chat_paste", title="Chat",
        granted_by=f"user:{owner['user_id']}", granted_scope="group-history",
    )
    db_session.add(source)
    db_session.flush()
    for position in (0, 2, 2):
        db_session.add(
            SourceChunk(source_id=source.id, position=position, text="words")
        )
    candidate = Candidate(
        group_id=group_id, kind="event", title="Picnic",
        created_by=f"user:{owner['user_id']}",
    )
    db_session.add(candidate)
    db_session.commit()
    # Orphaned provenance is planted through a RAW engine —
    # a connection with no foreign-key enforcement, which is
    # exactly the lax road (a hard delete on such a connection)
    # that leaves rows pointing at nothing in real deployments.
    from sqlalchemy import create_engine

    raw = create_engine(db_session.get_bind().url)
    with raw.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO provenance (id, candidate_id, chunk_id, field,"
                " quote, created_at) VALUES ('prov-orphan-chunk', :cand,"
                " 'chunk-that-is-gone', 'title', 'words', '2026-10-25 00:00:00')"
            ),
            {"cand": candidate.id},
        )
        connection.execute(
            text(
                "INSERT INTO provenance (id, candidate_id, chunk_id, field,"
                " quote, created_at) VALUES ('prov-orphan-candidate',"
                " 'candidate-that-is-gone', 'chunk-gone-too', 'title',"
                " 'words', '2026-10-25 00:00:00')"
            )
        )
    raw.dispose()

    # (c) Two flyers whose status lies about their text, both ways.
    db_session.add(
        Flyer(
            group_id=group_id, source_id=source.id,
            alt_text="awaiting description", alt_text_status="complete",
        )
    )
    db_session.add(
        Flyer(
            group_id=group_id, source_id=source.id,
            alt_text="A red barn dance poster with gold lettering and a fiddle",
            alt_text_status="awaiting_description",
        )
    )

    # (d) A digest whose renderings were emptied after compose.
    digest = digest_service.compose_digest(db_session, group=group_row)
    digest.body_text = ""
    digest.body_html = ""
    db_session.commit()
    return group_id, digest.id


def test_data_repair_fixes_forward_and_names_every_fix(client, db_session):
    group_id, digest_id = _plant_damage(client, db_session)

    repair_service.handle_data_repair(db_session, {})

    # (a) progress recomputed from status.
    stale = db_session.query(Job).filter(Job.kind == "notify").one()
    assert stale.progress == 100

    # (b) both orphan provenance rows removed; positions are
    # a clean 0..n-1 preserving the claimed order.
    assert db_session.query(Provenance).count() == 0
    positions = [
        chunk.position
        for chunk in db_session.query(SourceChunk).order_by(SourceChunk.position)
    ]
    assert positions == [0, 1, 2]

    # (c) both flyer statuses recomputed from their text.
    flyers = db_session.query(Flyer).all()
    by_text = {flyer.alt_text[:10]: flyer.alt_text_status for flyer in flyers}
    assert by_text["awaiting d"] == "awaiting_description"
    assert by_text["A red barn"] == "complete"

    # (d) the digest was re-rendered in place — still ONE row.
    digests = db_session.query(Digest).all()
    assert len(digests) == 1
    assert digests[0].id == digest_id
    assert digests[0].body_text and digests[0].body_html

    # The audit names the fixes (one repair.completed row
    # whose detail carries each repair in words).
    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "repair.completed")
        .all()
    )
    assert len(rows) == 1
    import json

    detail = json.loads(rows[0].detail_json or "{}")
    assert detail["fix_count"] >= 5
    joined = " ".join(detail["fixes"])
    for needle in ("progress set to 100", "provenance", "renumbered",
                   "alt_text_status", "re-rendered"):
        assert needle in joined, needle

    # Idempotent: a second run finds nothing left to fix.
    assert repair_service.run_repairs(db_session) == []
    db_session.rollback()


def test_data_repair_job_runs_through_the_chassis(app, db_session):
    """The crew's door is a job like any other: enqueued,
    claimed, done — with the audit row as its receipt."""
    settings = app.state.settings
    job = job_service.enqueue(db_session, settings, kind="data_repair", payload={})
    assert job_service.process_one(db_session, settings, "w-b173")
    db_session.expire_all()
    job = job_service.get_job(db_session, job.id)
    assert job.status == "done"
    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "repair.completed")
        .all()
    )
    assert len(rows) == 1
