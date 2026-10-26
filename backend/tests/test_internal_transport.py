"""Encrypted internal transport tests (roadmap §6A.4, B-163).

The acceptance, in the roadmap's own words: *a packet-capture
or pipe-dump style test proves no readable personal content
crosses an internal boundary, and an unsigned message is never
accepted.* These tests build exactly that evidence:

* a TAP sits between a real client and a real server on a
  real Unix socket and records every byte — the recording is
  then searched for the payload's secrets;
* an unsigned hand-made frame is refused before dispatch;
* the round trip also runs across a REAL process boundary
  (a subprocess server), and over loopback TCP with mutual
  TLS from a locally minted internal CA — where a client
  with the WRONG CA's leaf cannot even complete a handshake.

Windows named pipes: NOT RUN on this Linux builder (the
framing and wire form behind the pipe are these same classes;
the pipe leg itself is recorded honestly in the papers).
"""

import base64
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time

import pytest

from gpa.errors import AppError
from gpa.internal import crypto, registry
from gpa.internal.client import InternalClient
from gpa.internal.envelope import InternalRequest, utc_now_iso
from gpa.internal.transport import (
    Channel,
    TcpWireServer,
    UnixWireServer,
    WireServer,
    client_mtls_context,
    open_request,
    read_frame,
    seal_request,
    serve_in_thread,
    serve_tcp_mtls,
    server_mtls_context,
    tcp_client,
    unix_client,
    write_frame,
)

SECRET_TEXT = "Ana's private note: the surprise party is at hers"
SECRET_EMAIL = "ana.secret@example.org"


def _channel_pair():
    """A matched client/server channel pair with fresh keys."""
    key = os.urandom(32)
    client_key = crypto.generate_signing_key()
    server_key = crypto.generate_signing_key()
    # The client speaks AS the events module (the envelope's
    # caller) — B-164 binds caller to the signing identity,
    # so the fixture signs with the events name, not a pet
    # name.
    client = Channel(
        key=key,
        signer_id="events",
        signing_key=client_key,
        public_keys={"fixture-server": crypto.public_key_b64(server_key)},
    )
    server = Channel(
        key=key,
        signer_id="fixture-server",
        signing_key=server_key,
        public_keys={"events": crypto.public_key_b64(client_key)},
    )
    return client, server


@pytest.fixture()
def echo_world(app):
    """A WireServer over the app's world: the real registry,
    the real client executor, a REAL operation (test.echo,
    registered here for the transport's sake)."""
    if registry.get_operation("test.echo") is None:
        registry.register(
            registry.Operation(
                name="test.echo",
                owner="events",
                required_scope="test:echo",
                summary="Transport test echo.",
            ),
            lambda db, payload, ctx: {"echo": payload},
        )
        registry.grant("events", "test:echo")
    client = InternalClient()

    def dispatch(request):
        session = app.state.db.session()
        try:
            return client.execute_envelope(session, request)
        finally:
            session.close()

    return dispatch


def _request(payload=None):
    import secrets as _secrets

    return InternalRequest(
        operation="test.echo",
        caller="events",
        scope="test:echo",
        payload=payload or {"note": SECRET_TEXT, "email": SECRET_EMAIL},
        nonce=_secrets.token_urlsafe(16),
        timestamp=utc_now_iso(),
    )


def test_wire_roundtrip_and_tamper_refusals():
    """Seal → open recovers the envelope; every tamper —
    payload, header, dropped signature — is refused, in the
    error language, before anything is decrypted or run."""
    client_channel, server_channel = _channel_pair()
    wire = seal_request(client_channel, _request())
    opened = open_request(server_channel, wire)
    assert opened.payload["note"] == SECRET_TEXT

    message = json.loads(wire)
    # Drop the signature: never accepted.
    unsigned = dict(message)
    unsigned.pop("signature")
    with pytest.raises(AppError) as caught:
        open_request(server_channel, json.dumps(unsigned).encode())
    assert caught.value.code == "internal_bad_signature"

    # Flip a character in the sealed payload: the signature
    # (which covers it) refuses it.
    flipped = dict(message)
    sealed = flipped["payload_sealed"]
    flipped["payload_sealed"] = ("A" if sealed[0] != "A" else "B") + sealed[1:]
    with pytest.raises(AppError) as caught:
        open_request(server_channel, json.dumps(flipped).encode())
    assert caught.value.code == "internal_bad_signature"

    # A signer nobody knows: refused.
    stranger = dict(message)
    stranger["signer"] = "mallory"
    with pytest.raises(AppError) as caught:
        open_request(server_channel, json.dumps(stranger).encode())
    assert caught.value.code == "internal_bad_signature"


class _Tap:
    """A recording middleman: listens on one Unix path,
    forwards to the real server path, saves every byte in
    both directions — the pipe-dump the roadmap asks for."""

    def __init__(self, listen_path: str, target_path: str) -> None:
        self.captured = bytearray()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(listen_path)
        self._listener.listen(1)
        self._target_path = target_path
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        conn, _ = self._listener.accept()
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream.connect(self._target_path)

        def pump(source, sink):
            try:
                while True:
                    data = source.recv(65536)
                    if not data:
                        break
                    self.captured.extend(data)
                    sink.sendall(data)
            except OSError:
                pass

        t1 = threading.Thread(target=pump, args=(conn, upstream), daemon=True)
        t2 = threading.Thread(target=pump, args=(upstream, conn), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)


def test_unix_socket_tap_finds_no_readable_personal_content(tmp_path, echo_world):
    """THE B-163 acceptance test: a real call crosses a real
    Unix socket through a tap; the tap's recording is then
    searched — the payload's personal content is nowhere in
    it, in either direction, while the call itself succeeded
    (so the test cannot pass by the call simply failing)."""
    client_channel, server_channel = _channel_pair()
    real_path = str(tmp_path / "real.sock")
    tap_path = str(tmp_path / "tap.sock")
    server = UnixWireServer(real_path, WireServer(server_channel, echo_world))
    serve_in_thread(server)
    tap = _Tap(tap_path, real_path)
    tap.start()
    try:
        client = unix_client(client_channel, tap_path)
        result = client.call(_request())
        client.close()
        assert result.ok is True
        assert result.data == {"echo": {"note": SECRET_TEXT, "email": SECRET_EMAIL}}
    finally:
        server.shutdown()
        server.server_close()

    captured = bytes(tap.captured)
    assert captured, "the tap must have recorded the call"
    assert SECRET_TEXT.encode() not in captured
    assert SECRET_EMAIL.encode() not in captured
    # The frames parse as wire messages whose payloads are
    # sealed blobs — the shape of the privacy, not just luck.
    frames = []
    offset = 0
    while offset < len(captured):
        length = int.from_bytes(captured[offset : offset + 4], "big")
        frames.append(json.loads(captured[offset + 4 : offset + 4 + length]))
        offset += 4 + length
    assert any("payload_sealed" in frame for frame in frames)
    assert all("payload" not in frame for frame in frames)


def test_unsigned_frame_is_refused_before_dispatch(tmp_path, echo_world):
    """A hand-made plaintext frame — payload in the clear, no
    signature — gets a signed refusal and the dispatcher
    never runs."""
    client_channel, server_channel = _channel_pair()
    dispatched = []

    def counting_dispatch(request):
        dispatched.append(request)
        return echo_world(request)

    path = str(tmp_path / "door.sock")
    server = UnixWireServer(path, WireServer(server_channel, counting_dispatch))
    serve_in_thread(server)
    try:
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.connect(path)
        forged = {
            "v": 1,
            "operation": "test.echo",
            "caller": "events",
            "scope": "test:echo",
            "nonce": "forged",
            "timestamp": "2026-10-25T12:00:00+00:00",
            "idempotency_key": None,
            "signer": "events",
            "payload": {"note": SECRET_TEXT},  # in the clear!
        }
        write_frame(raw, json.dumps(forged).encode())
        reply = read_frame(raw)
        raw.close()
        assert reply is not None
        parsed = json.loads(reply)
        assert parsed["error"]["code"] == "internal_bad_signature"
        assert parsed["signature"], "even the refusal is signed"
        assert dispatched == []
    finally:
        server.shutdown()
        server.server_close()


def test_cross_process_unix_roundtrip(tmp_path):
    """The round trip across a REAL process boundary: the
    fixture server runs as its own Python process; this test
    holds only the client's keys."""
    key = os.urandom(32)
    client_key = crypto.generate_signing_key()
    server_key = crypto.generate_signing_key()
    socket_path = str(tmp_path / "proc.sock")
    env = dict(os.environ)
    env.update(
        {
            "GPA_FIXTURE_SOCKET": socket_path,
            "GPA_FIXTURE_KEY_B64": base64.b64encode(key).decode(),
            "GPA_FIXTURE_SERVER_SEED": crypto.seed_b64(server_key),
            "GPA_FIXTURE_CLIENT_PUB": crypto.public_key_b64(client_key),
            "PYTHONPATH": os.pathsep.join(
                [os.path.join(os.path.dirname(__file__), "..", "src")]
                + ([os.environ["PYTHONPATH"]] if "PYTHONPATH" in os.environ else [])
            ),
        }
    )
    fixture = os.path.join(os.path.dirname(__file__), "internal_fixture_server.py")
    proc = subprocess.Popen(
        [sys.executable, fixture],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 30
        while not os.path.exists(socket_path):
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"fixture server died: {out}")
            assert time.time() < deadline, "fixture server never bound its socket"
            time.sleep(0.05)
        channel = Channel(
            key=key,
            signer_id="events",
            signing_key=client_key,
            public_keys={"fixture-server": crypto.public_key_b64(server_key)},
        )
        client = unix_client(channel, socket_path)
        result = client.call(_request())
        client.close()
        assert result.ok is True
        assert result.data == {"echo": {"note": SECRET_TEXT, "email": SECRET_EMAIL}}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_loopback_mtls_roundtrip_and_wrong_ca_refused(tmp_path, echo_world):
    """The third door: loopback TCP with mutual TLS from a
    locally minted internal CA. A correct client completes
    the encrypted round trip; a client presenting a leaf from
    a DIFFERENT CA cannot complete the handshake at all."""
    from gpa.internal import ca

    ca_cert, ca_key = ca.create_ca()
    server_cert, server_key = ca.issue_leaf(ca_cert, ca_key, "core", server=True)
    client_cert, client_key_pem = ca.issue_leaf(ca_cert, ca_key, "events", server=False)
    rogue_ca_cert, rogue_ca_key = ca.create_ca("rogue-ca")
    rogue_cert, rogue_key_pem = ca.issue_leaf(rogue_ca_cert, rogue_ca_key, "events", server=False)

    client_channel, server_channel = _channel_pair()
    server = serve_tcp_mtls(
        ("127.0.0.1", 0),
        WireServer(server_channel, echo_world),
        server_mtls_context(cert_pem=server_cert, key_pem=server_key, ca_pem=ca_cert),
    )
    serve_in_thread(server)
    port = server.socket.getsockname()[1]
    try:
        good = tcp_client(
            client_channel,
            "127.0.0.1",
            port,
            client_mtls_context(cert_pem=client_cert, key_pem=client_key_pem, ca_pem=ca_cert),
        )
        result = good.call(_request())
        good.close()
        assert result.ok is True

        with pytest.raises(ssl.SSLError):
            tcp_client(
                client_channel,
                "127.0.0.1",
                port,
                client_mtls_context(cert_pem=rogue_cert, key_pem=rogue_key_pem, ca_pem=rogue_ca_cert),
            )
    finally:
        server.shutdown()
        server.server_close()


def test_hmac_profile_roundtrip():
    """The shared-secret profile seals, signs and opens with
    the same guarantees (and refuses a wrong secret)."""
    secret = os.urandom(32)
    key = os.urandom(32)
    client = Channel(key=key, profile="hmac", signer_id="a", hmac_secret=secret)
    server = Channel(key=key, profile="hmac", signer_id="b", hmac_secret=secret)
    wire = seal_request(client, _request())
    assert open_request(server, wire).payload["email"] == SECRET_EMAIL
    wrong = Channel(key=key, profile="hmac", signer_id="b", hmac_secret=os.urandom(32))
    with pytest.raises(AppError) as caught:
        open_request(wrong, wire)
    assert caught.value.code == "internal_bad_signature"
