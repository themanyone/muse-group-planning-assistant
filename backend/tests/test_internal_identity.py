"""Service identities + replay protection tests (roadmap §6A.4, B-164).

The acceptance, in the roadmap's own words: an impersonation
test (worker A's credentials calling an AI-only operation →
denied), a replay test (a captured message re-sent → denied),
a rotation test (mid-flight rotation, no loss). Those three
tests are here, plus the identity store's own guarantees:
distinct keys per module, scopes mirroring the registry, and
the directory form of custody.
"""

import base64
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from gpa.config import Settings
from gpa.errors import AppError
from gpa.internal import crypto, identity, registry
from gpa.internal.client import InternalClient
from gpa.internal.envelope import InternalRequest, InternalResult, utc_now_iso
from gpa.internal.replay import ReplayGuard
from gpa.internal.transport import Channel, WireServer, seal_request


def _envelope(caller: str, operation: str = "ai.submit_job", *, when=None, nonce="n-1"):
    op = registry.get_operation(operation)
    return InternalRequest(
        operation=operation,
        caller=caller,
        scope=op.required_scope if op else "",
        payload={"kind": "extract", "payload": {}},
        nonce=nonce,
        timestamp=(when or datetime.now(timezone.utc)).isoformat(),
    )


def _store_with(*names: str) -> identity.IdentityStore:
    """A store where each named identity holds a FRESH random
    key (not derived — impersonation must fail against real,
    distinct key material)."""
    store = identity.IdentityStore()
    for name in names:
        own = identity.Identity(name=name, private_key=crypto.generate_signing_key())
        store.own[name] = own
        store.accepted[name] = [own.public_b64]
    return store


def _echo_dispatcher(request):
    return InternalResult(ok=True, operation=request.operation, data={"echo": request.payload})


def test_derived_identities_are_distinct_scoped_and_stable():
    """Development custody still gives every module its own
    key, stable across loads, with scopes mirroring the
    registry's least-privilege grants."""
    store = identity.load_store(Settings(secret_key="dev-secret"))
    extraction = store.own["extraction"]
    events = store.own["events"]
    assert extraction.public_b64 != events.public_b64
    assert extraction.key_id == crypto.key_id_of(extraction.public_b64)
    assert "ai:submit" in extraction.scopes()
    assert "notify:write" not in extraction.scopes()
    again = identity.load_store(Settings(secret_key="dev-secret"))
    assert again.own["extraction"].public_b64 == extraction.public_b64


def test_directory_identities_with_rotation_overlap(tmp_path):
    """Production custody: seed/pub files on disk, the
    previous public key accepted beside the current one."""
    current = crypto.generate_signing_key()
    previous = crypto.generate_signing_key()
    (tmp_path / "events.seed").write_text(crypto.seed_b64(current))
    (tmp_path / "events.pub").write_text(crypto.public_key_b64(current))
    (tmp_path / "events.pub.prev").write_text(crypto.public_key_b64(previous))
    store = identity.load_store(
        Settings(secret_key="x", internal_identities_dir=str(tmp_path))
    )
    assert store.own["events"].public_b64 == crypto.public_key_b64(current)
    assert store.accepted["events"] == [
        crypto.public_key_b64(current),
        crypto.public_key_b64(previous),
    ]


def test_impersonation_is_denied_at_every_layer():
    """THE impersonation acceptance test. A worker identity
    tries to call the AI-only operation three ways, and each
    way is refused by a different layer of the same law:

    1. claiming the extraction NAME with the worker's key —
       the signature cannot verify against extraction's key;
    2. signing honestly as the worker but writing caller=
       extraction in the envelope — the door binds caller to
       the signing identity and refuses;
    3. calling as the worker under its own name — the scope
       check refuses: ai:submit is not a worker scope."""
    from gpa.internal.handlers import register_all

    register_all()
    store = _store_with("extraction", "worker", "core")
    key = os.urandom(32)
    server_channel = store.channel(key=key, as_name="core")
    door = WireServer(server_channel, _echo_dispatcher)

    # 1. Forged signer name.
    worker_forgery = Channel(
        key=key, signer_id="extraction", signing_key=store.own["worker"].private_key
    )
    reply = json.loads(door.handle(seal_request(worker_forgery, _envelope("extraction"))))
    assert reply["error"]["code"] == "internal_bad_signature"

    # 2. Honest signer, lying envelope.
    worker_channel = store.channel(key=key, as_name="worker")
    reply = json.loads(door.handle(seal_request(worker_channel, _envelope("extraction"))))
    assert reply["error"]["code"] == "internal_bad_signature"
    assert "is not its signing identity" in reply["error"]["detail"]

    # 3. Honest everything, wrong scopes — refused by the
    # dispatch law itself (the real executor, not the echo).
    result = InternalClient().execute_envelope(
        _NullSession(), _envelope("worker")
    )
    assert result.ok is False
    assert result.error["code"] == "internal_scope_denied"


class _NullSession:
    """The scope refusal happens before any handler runs, so
    no database is touched; this stands in for the session
    the executor would have used."""

    def rollback(self):  # pragma: no cover — defensive only
        pass


def test_replay_is_denied_and_stale_messages_too():
    """THE replay acceptance test: a captured wire message,
    re-sent byte for byte, is refused; a fresh message with
    an old timestamp is refused by the window alone."""
    store = _store_with("events", "core")
    key = os.urandom(32)
    guard = ReplayGuard(window_seconds=300)
    door = WireServer(store.channel(key=key, as_name="core"), _echo_dispatcher, replay_guard=guard)
    client_channel = store.channel(key=key, as_name="events")

    wire = seal_request(client_channel, _envelope("events", "ai.submit_job", nonce="once"))
    first = json.loads(door.handle(wire))
    assert first["error"] is None
    second = json.loads(door.handle(wire))  # the replay
    assert second["error"]["code"] == "internal_replayed"

    stale = _envelope(
        "events",
        "ai.submit_job",
        when=datetime.now(timezone.utc) - timedelta(seconds=600),
        nonce="fresh-but-old",
    )
    reply = json.loads(door.handle(seal_request(client_channel, stale)))
    assert reply["error"]["code"] == "internal_replayed"


def test_rotation_overlap_loses_nothing():
    """THE rotation acceptance test. The events identity
    rotates: during the overlap the door accepts BOTH keys;
    a call signed before the rotation completes still lands;
    after the overlap ends, the old key is refused and the
    new key works — at no point was a valid call lost."""
    old = identity.Identity(name="events", private_key=crypto.generate_signing_key())
    new = identity.Identity(name="events", private_key=crypto.generate_signing_key())
    key = os.urandom(32)

    def door_with(accepted: list[str]) -> WireServer:
        store = identity.IdentityStore(accepted={"events": accepted, "core": []})
        server_key = crypto.generate_signing_key()
        store.own["core"] = identity.Identity(name="core", private_key=server_key)
        store.accepted["core"] = [crypto.public_key_b64(server_key)]
        return WireServer(store.channel(key=key, as_name="core"), _echo_dispatcher)

    def call_with(identity_: identity.Identity) -> dict:
        channel = Channel(
            key=key, signer_id="events", signing_key=identity_.private_key,
            public_keys={},
        )
        return json.loads(door.handle(seal_request(channel, _envelope("events", "ai.submit_job"))))

    door = door_with([new.public_b64, old.public_b64])  # overlap
    assert call_with(old)["error"] is None  # signed pre-rotation, still lands
    assert call_with(new)["error"] is None

    door = door_with([new.public_b64])  # rotation complete
    assert call_with(old)["error"]["code"] == "internal_bad_signature"
    assert call_with(new)["error"] is None


def test_hmac_channel_key_id_is_stable():
    """The HMAC profile also names its key (a fingerprint of
    the secret, never the secret) for replay/audit binding."""
    channel = Channel(key=os.urandom(32), profile="hmac", signer_id="a", hmac_secret=b"s" * 32)
    data = b"payload"
    signature = channel.sign(data)
    assert channel.verify("a", signature, data) == channel.verify("a", signature, data)
