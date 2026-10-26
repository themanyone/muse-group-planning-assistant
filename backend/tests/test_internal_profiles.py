"""In-process profile, honestly (roadmap §6A.4, B-166).

Two guarantees under test:

1. **Profile parity.** The same internal-API probes pass
   against the in-process profile (dispatch in this process)
   and the split-process profile (sealed + signed over a real
   Unix socket to a server dispatching elsewhere) — unchanged.
   The boundary is real, typed, scoped, and swappable without
   the modules knowing.

2. **Job payloads at rest are sealed.** A copied database
   yields no readable job payloads, while the chassis reads
   them normally — and rows written before the sealing
   (legacy plaintext) still read, because the prefix is the
   version marker.
"""

import json
import os

import pytest

from conftest import make_group, register
from gpa.errors import AppError
from gpa.internal import crypto
from gpa.internal.client import InternalClient
from gpa.internal.handlers import register_all
from gpa.internal.transport import Channel, UnixWireServer, WireServer, serve_in_thread, unix_client
from gpa.models.core import Job
from gpa.models.groups import Candidate
from gpa.services import jobs as job_service


# ---------------------------------------------------------------------------
# The probes — written once, run against both profiles.
# ---------------------------------------------------------------------------


def probe_candidates_create(call, db_session, group_id):
    data = call(
        db_session, caller="groups", operation="candidates.create",
        payload={
            "group_id": group_id, "kind": "event", "title": "Profile probe",
            "created_by": "user:probe", "draft": {"title": "Profile probe"},
        },
    )
    row = db_session.get(Candidate, data["candidate_id"])
    assert row is not None and row.title == "Profile probe"


def probe_jobs_and_ai(call, db_session):
    data = call(
        db_session, caller="integrations", operation="jobs.enqueue",
        payload={"kind": "digest_run", "payload": {"group_id": "g1"}},
    )
    assert db_session.get(Job, data["job_id"]) is not None
    with pytest.raises(AppError) as caught:
        call(
            db_session, caller="extraction", operation="ai.submit_job",
            payload={"kind": "digest_run"},
        )
    assert caught.value.code == "internal_bad_payload"


def probe_scope_denial(call, db_session):
    with pytest.raises(AppError) as caught:
        call(
            db_session, caller="sources", operation="notify.request",
            payload={"kind": "x", "subject": "x", "body_text": "x",
                     "audience": {"label": "user:nobody"}},
        )
    assert caught.value.code == "internal_scope_denied"


def probe_unknown_operation(call, db_session):
    with pytest.raises(AppError) as caught:
        call(db_session, caller="events", operation="nope.nope")
    assert caught.value.code == "internal_unknown_operation"


PROBES = [
    probe_jobs_and_ai,
    probe_scope_denial,
    probe_unknown_operation,
]


@pytest.fixture(params=["in-process", "split"])
def profile(request, app, tmp_path):
    """One call-interface, two profiles. The split profile's
    server dispatches against the same app database, from
    its own sessions, behind the real wire door."""
    register_all()
    if request.param == "in-process":
        yield InternalClient().call
        return
    key = os.urandom(32)
    client_key = crypto.generate_signing_key()
    server_key = crypto.generate_signing_key()
    server_channel = Channel(
        key=key, signer_id="core", signing_key=server_key,
        public_keys={
            name: crypto.public_key_b64(client_key)
            for name in ("groups", "integrations", "extraction", "sources", "events")
        },
    )
    executor = InternalClient()

    def dispatch(envelope):
        session = app.state.db.session()
        try:
            return executor.execute_envelope(session, envelope)
        finally:
            session.close()

    path = str(tmp_path / "profile.sock")
    server = UnixWireServer(path, WireServer(server_channel, dispatch))
    serve_in_thread(server)
    # The wire door binds the envelope's caller to the
    # signing identity (B-164), so this profile's client
    # signs as whichever caller each probe names — one
    # short-lived WireClient per call, all holding the one
    # client key and accepting the server's.
    def call(db, *, caller, operation, payload=None, idempotency_key=None):
        channel = Channel(
            key=key, signer_id=caller, signing_key=client_key,
            public_keys={"core": crypto.public_key_b64(server_key)},
        )
        client = unix_client(channel, path)
        try:
            envelope_client = InternalClient(wire=client)
            return envelope_client.call(
                db, caller=caller, operation=operation,
                payload=payload, idempotency_key=idempotency_key,
            )
        finally:
            client.close()

    yield call
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("probe", PROBES, ids=[p.__name__ for p in PROBES])
def test_probe_passes_in_both_profiles(profile, probe, db_session):
    probe(profile, db_session)


def test_candidates_probe_in_both_profiles(profile, client, db_session):
    owner = register(client)
    group = make_group(client, owner)
    probe_candidates_create(profile, db_session, group["id"])


# ---------------------------------------------------------------------------
# Job payloads at rest
# ---------------------------------------------------------------------------

SECRET = "the surprise picnic at Ana's boathouse"


def test_job_payloads_are_sealed_at_rest(app, db_session):
    """Queue a job whose payload carries personal content;
    the stored column is sealed (prefix, no readable trace),
    payload_of reads it back whole, and the worker's dispatch
    hands the handler the opened payload."""
    seen: list[dict] = []
    job_service.register_handler("probe_kind", lambda db, payload: seen.append(payload))
    job = job_service.enqueue(
        db_session, app.state.settings, kind="probe_kind",
        payload={"note": SECRET, "group_id": "g1"},
    )
    stored = db_session.get(Job, job.id).payload_json
    assert stored.startswith(job_service.PAYLOAD_PREFIX)
    assert SECRET not in stored
    assert "boathouse" not in stored
    assert job_service.payload_of(db_session.get(Job, job.id)) == {
        "note": SECRET, "group_id": "g1",
    }
    assert job_service.process_one(db_session, app.state.settings, "w-probe") is True
    assert seen == [{"note": SECRET, "group_id": "g1"}]


def test_legacy_plaintext_job_rows_still_read(app, db_session):
    """A row written before B-166 (plaintext JSON, no prefix)
    opens through the same law — the upgrade must not strand
    a single queued job."""
    legacy = Job(kind="legacy", payload_json=json.dumps({"note": SECRET}))
    db_session.add(legacy)
    db_session.commit()
    assert job_service.payload_of(legacy) == {"note": SECRET}


def test_arq_boundary_seals_too(app):
    """The arq adapter's serialisation boundary (Redis) uses
    the same seal: what the queue would hold is unreadable,
    and the worker side opens it."""
    settings = app.state.settings
    wire = job_service.seal_payload({"note": SECRET}, settings)
    assert wire.startswith(job_service.PAYLOAD_PREFIX)
    assert SECRET not in wire
    assert job_service.open_payload(wire, settings) == {"note": SECRET}
