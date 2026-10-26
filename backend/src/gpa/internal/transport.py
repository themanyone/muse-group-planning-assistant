"""Encrypted internal transport (roadmap §6A.4, B-163).

The second and third doors between processes:

* **Unix domain socket** (the named-pipe abstraction's Unix
  face; Windows named pipes are the same framing behind the
  same classes when that leg runs — NOT RUN on this Linux
  builder, recorded honestly in the phase papers).
* **Loopback TCP + mutual TLS**, certificates from the
  internal CA (`gpa.internal.ca`) — the profile for processes
  on separate local services, still never the open network.

Every framed message on either door is the WIRE form of the
envelope: payload sealed with AES-256-GCM, the whole message
signed (Ed25519, or the HMAC profile for shared-secret
channels). An unsigned message is not "accepted with a
warning" — it is refused before dispatch, and the refusal
itself is signed so a caller can trust even the door's "no".

Framing: a 4-byte big-endian length, then one JSON object.
One connection carries any number of request/response pairs.
"""

from __future__ import annotations

import json
import socket
import socketserver
import ssl
import struct
import threading
from typing import Callable

from gpa.errors import AppError
from gpa.internal import crypto
from gpa.internal.envelope import InternalRequest, InternalResult

# One message, at most 8 MiB framed — internal payloads are
# ids and small drafts by design (§6A.4); anything bigger is a
# bug or an attack, and the door says so.
MAX_FRAME = 8 * 1024 * 1024

_HEADER = struct.Struct(">I")


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def write_frame(sock: socket.socket, data: bytes) -> None:
    if len(data) > MAX_FRAME:
        raise AppError("internal_tampered", detail="An internal frame is too large.")
    sock.sendall(_HEADER.pack(len(data)) + data)


def read_frame(sock: socket.socket) -> bytes | None:
    """One frame, or None on a clean close between frames."""
    prefix = _read_exactly(sock, _HEADER.size)
    if prefix is None:
        return None
    (length,) = _HEADER.unpack(prefix)
    if length > MAX_FRAME:
        raise AppError("internal_tampered", detail="An internal frame is too large.")
    body = _read_exactly(sock, length)
    if body is None:
        raise AppError("internal_tampered", detail="An internal frame ended early.")
    return body


def _read_exactly(sock: socket.socket, count: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < count:
        piece = sock.recv(count - len(chunks))
        if not piece:
            return None if not chunks else bytes(chunks)
        chunks.extend(piece)
    return bytes(chunks)


# ---------------------------------------------------------------------------
# The wire form: seal + sign / verify + open
# ---------------------------------------------------------------------------
#
# A wire request is a JSON object:
#   {v, operation, caller, scope, nonce, timestamp,
#    idempotency_key, signer, payload_sealed, signature}
# The header fields ride in the clear AS AUTHENTICATED DATA —
# the GCM tag covers them, the signature covers everything —
# so routing and rejection never require decryption, and no
# tamper survives either check. The payload, the only part
# that can carry user data, never appears in the clear.
#
# A wire result mirrors it: {v, ok, nonce, error,
# data_sealed, signer, signature}.


class Channel:
    """One channel's crypto identity: the shared comms key,
    how this end signs, and how it verifies the other end.

    ``profile`` is "ed25519" (per-identity key pairs; the
    verifier resolves a signer id to a public key) or "hmac"
    (one shared secret both signs and verifies with). The two
    never mix on one channel — mixing them is how downgrade
    attacks are born."""

    def __init__(
        self,
        *,
        key: bytes,
        profile: str = "ed25519",
        signer_id: str = "",
        signing_key=None,
        hmac_secret: bytes = b"",
        public_keys: dict[str, str | list[str]] | None = None,
    ) -> None:
        if profile not in ("ed25519", "hmac"):
            raise AppError("internal_bad_key", detail=f"Unknown channel profile {profile!r}.")
        self.key = key
        self.profile = profile
        self.signer_id = signer_id
        self.signing_key = signing_key
        self.hmac_secret = hmac_secret
        self.public_keys = public_keys or {}

    # -- signing ---------------------------------------------------------
    def sign(self, data: bytes) -> str:
        if self.profile == "hmac":
            return crypto.sign_hmac(self.hmac_secret, data)
        if self.signing_key is None:
            raise AppError("internal_bad_key", detail="This channel end cannot sign.")
        return crypto.sign(self.signing_key, data)

    def verify(self, signer: str, signature: str, data: bytes) -> str:
        """Verify a signature; returns the KEY ID of the key
        that verified (B-164 — with rotation overlap a signer
        may have two accepted keys, and audit wants to know
        which one spoke). Any failure — unknown signer, bad
        math — is ``internal_bad_signature``."""
        if self.profile == "hmac":
            crypto.verify_hmac(self.hmac_secret, signature, data)
            import hashlib

            return hashlib.sha256(self.hmac_secret).hexdigest()[:16]
        accepted = self.public_keys.get(signer)
        if accepted is None:
            raise AppError(
                "internal_bad_signature",
                detail=f"Unknown internal signer {signer!r}.",
            )
        candidates = [accepted] if isinstance(accepted, str) else list(accepted)
        for public in candidates:
            try:
                crypto.verify(public, signature, data)
                return crypto.key_id_of(public)
            except AppError:
                continue
        raise AppError(
            "internal_bad_signature",
            detail="An internal message's signature did not verify.",
        )


def seal_request(channel: Channel, request: InternalRequest) -> bytes:
    """Envelope → wire bytes (client side)."""
    header = {
        "v": crypto.WIRE_VERSION,
        "operation": request.operation,
        "caller": request.caller,
        "scope": request.scope,
        "nonce": request.nonce,
        "timestamp": request.timestamp,
        "idempotency_key": request.idempotency_key,
        "signer": channel.signer_id,
    }
    aad = crypto.canonical_header_bytes(header)
    payload_json = json.dumps(request.payload, sort_keys=True).encode()
    message = dict(header)
    message["payload_sealed"] = crypto.seal(channel.key, payload_json, aad=aad)
    message["signature"] = channel.sign(crypto.canonical_header_bytes(message))
    return json.dumps(message, sort_keys=True).encode()


def open_request(channel: Channel, wire: bytes) -> InternalRequest:
    """Wire bytes → envelope (server side). Signature first —
    an unsigned or forged message is refused before a single
    byte is decrypted or dispatched."""
    request, _signer, _key_id = open_request_detailed(channel, wire)
    return request


def open_request_detailed(
    channel: Channel, wire: bytes
) -> tuple[InternalRequest, str, str]:
    """`open_request`, plus the authenticated facts B-164
    binds policy to: (envelope, signer name, key id)."""
    try:
        message = json.loads(wire)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError("internal_tampered", detail="An internal message was not JSON.") from exc
    if not isinstance(message, dict) or message.get("v") != crypto.WIRE_VERSION:
        raise AppError("internal_tampered", detail="An internal message has no wire version.")
    signature = message.get("signature")
    if not signature:
        raise AppError(
            "internal_bad_signature",
            detail="An unsigned internal message is never accepted.",
        )
    signer = str(message.get("signer", ""))
    key_id = channel.verify(
        signer,
        str(signature),
        crypto.canonical_header_bytes(message),
    )
    header = {k: message[k] for k in (
        "v", "operation", "caller", "scope", "nonce", "timestamp",
        "idempotency_key", "signer",
    )}
    payload = json.loads(
        crypto.open_sealed(channel.key, str(message["payload_sealed"]), aad=crypto.canonical_header_bytes(header))
    )
    request = InternalRequest(
        operation=message["operation"],
        caller=message["caller"],
        scope=message["scope"],
        payload=payload,
        nonce=message["nonce"],
        timestamp=message["timestamp"],
        idempotency_key=message.get("idempotency_key"),
        signature=signature,
    )
    return request, signer, key_id


def seal_result(channel: Channel, result: InternalResult, *, nonce: str) -> bytes:
    """Result → wire bytes (server side). The refusal of a bad
    message is itself a result, signed like any other."""
    header = {"v": crypto.WIRE_VERSION, "ok": result.ok, "nonce": nonce, "signer": channel.signer_id}
    message = dict(header)
    message["error"] = result.error
    # Result data is a dict by the envelope's law (possibly
    # empty); it is sealed exactly like a request payload —
    # results can carry personal facts too.
    message["data_sealed"] = crypto.seal(
        channel.key,
        json.dumps(result.data, sort_keys=True).encode(),
        aad=crypto.canonical_header_bytes(header),
    )
    message["signature"] = channel.sign(crypto.canonical_header_bytes(message))
    return json.dumps(message, sort_keys=True).encode()


def open_result(channel: Channel, wire: bytes, *, nonce: str) -> InternalResult:
    """Wire bytes → result (client side), verifying the
    server's signature and the echoed nonce — a response to
    some OTHER request is not an answer to this one."""
    try:
        message = json.loads(wire)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError("internal_tampered", detail="An internal reply was not JSON.") from exc
    signature = message.get("signature")
    if not signature:
        raise AppError("internal_bad_signature", detail="An unsigned internal reply is never accepted.")
    channel.verify(str(message.get("signer", "")), str(signature), crypto.canonical_header_bytes(message))
    if message.get("nonce") != nonce:
        raise AppError("internal_tampered", detail="An internal reply answers a different request.")
    data: dict = {}
    if message.get("data_sealed") is not None:
        header = {k: message[k] for k in ("v", "ok", "nonce", "signer")}
        data = json.loads(
            crypto.open_sealed(channel.key, message["data_sealed"], aad=crypto.canonical_header_bytes(header))
        )
    return InternalResult(
        ok=bool(message.get("ok")),
        operation="",
        data=data,
        error=message.get("error"),
    )


# ---------------------------------------------------------------------------
# Server: one dispatcher behind either door
# ---------------------------------------------------------------------------

# A dispatcher answers an opened envelope with a result. The
# production dispatcher (internal/server.py's wiring) runs the
# envelope through the InternalClient against a fresh session;
# tests register their own operations the same way.
Dispatcher = Callable[[InternalRequest], InternalResult]


class WireServer:
    """The crypto door itself: wire bytes in, wire bytes out,
    dispatch between verify and seal. Transport-agnostic —
    the socket runners below are thin shells around it.

    B-164's bindings live here, at the door, so no dispatcher
    can forget them: the envelope's caller must BE the
    authenticated signing identity (a worker's key cannot
    speak with an AI module's name), and — when a replay
    guard is installed — the message's nonce must be fresh
    within its window."""

    def __init__(
        self,
        channel: Channel,
        dispatcher: Dispatcher,
        *,
        replay_guard=None,
    ) -> None:
        self.channel = channel
        self.dispatcher = dispatcher
        self.replay_guard = replay_guard

    def handle(self, wire: bytes) -> bytes:
        nonce = ""
        try:
            request, signer, key_id = open_request_detailed(self.channel, wire)
            nonce = request.nonce
            if request.caller != signer:
                raise AppError(
                    "internal_bad_signature",
                    detail=(
                        f"An internal message's caller '{request.caller}' is not "
                        f"its signing identity '{signer}'."
                    ),
                )
            if self.replay_guard is not None:
                self.replay_guard.check(
                    key_id=key_id, nonce=request.nonce, timestamp=request.timestamp
                )
            result = self.dispatcher(request)
        except AppError as err:
            result = InternalResult(
                ok=False,
                operation="",
                error={
                    "code": err.code,
                    "title": err.title,
                    "detail": err.detail,
                    "status": err.status,
                },
            )
        return seal_result(self.channel, result, nonce=nonce)


class _FrameHandler(socketserver.BaseRequestHandler):
    """One connection: frames until the peer closes."""

    def handle(self) -> None:  # noqa: D102 — socketserver's contract
        wire_server: WireServer = self.server.wire_server  # type: ignore[attr-defined]
        while True:
            try:
                frame = read_frame(self.request)
            except (AppError, ConnectionError, OSError):
                return
            if frame is None:
                return
            try:
                write_frame(self.request, wire_server.handle(frame))
            except (ConnectionError, OSError):
                return


class UnixWireServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The Unix-socket door. Threading: a slow operation never
    blocks the door for the next caller."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: str, wire_server: WireServer) -> None:
        super().__init__(path, _FrameHandler)
        self.wire_server = wire_server


class TcpWireServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """The loopback-TCP door; the socket is wrapped in mTLS
    by ``serve_tcp_mtls`` before it reaches this class."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], wire_server: WireServer) -> None:
        super().__init__(address, _FrameHandler)
        self.wire_server = wire_server


def serve_in_thread(server: socketserver.BaseServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def serve_tcp_mtls(
    address: tuple[str, int], wire_server: WireServer, context: ssl.SSLContext
) -> TcpWireServer:
    """The loopback mTLS door: a TCP server whose listening
    socket is wrapped server-side in the mTLS context, so a
    connection without a CA-issued client leaf never reaches
    the frame layer at all."""
    server = TcpWireServer(address, wire_server)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class WireClient:
    """One connection, many calls. Not thread-safe by design —
    a caller that needs concurrency opens a client per thread,
    exactly like a database session."""

    def __init__(self, channel: Channel, sock: socket.socket) -> None:
        self.channel = channel
        self.sock = sock

    def call(self, request: InternalRequest) -> InternalResult:
        write_frame(self.sock, seal_request(self.channel, request))
        wire = read_frame(self.sock)
        if wire is None:
            raise AppError("internal_tampered", detail="The internal door closed mid-call.")
        result = open_result(self.channel, wire, nonce=request.nonce)
        if not result.ok:
            error = result.error or {}
            raise AppError(
                str(error.get("code", "internal_error")),
                detail=str(error.get("detail", "")),
            )
        return result

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def unix_client(channel: Channel, path: str) -> WireClient:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    return WireClient(channel, sock)


def tcp_client(
    channel: Channel,
    host: str,
    port: int,
    context: ssl.SSLContext,
    *,
    server_hostname: str = "localhost",
) -> WireClient:
    raw = socket.create_connection((host, port))
    wrapped = context.wrap_socket(raw, server_hostname=server_hostname)
    return WireClient(channel, wrapped)


def server_mtls_context(
    *, cert_pem: bytes, key_pem: bytes, ca_pem: bytes
) -> ssl.SSLContext:
    """Server side of the mTLS door: presents the server leaf,
    REQUIRES a client leaf from the internal CA."""
    import tempfile
    from pathlib import Path

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    with tempfile.TemporaryDirectory() as tmp:
        cert_file = Path(tmp) / "cert.pem"
        key_file = Path(tmp) / "key.pem"
        ca_file = Path(tmp) / "ca.pem"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)
        ca_file.write_bytes(ca_pem)
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        context.load_verify_locations(cafile=ca_file)
    return context


def client_mtls_context(
    *, cert_pem: bytes, key_pem: bytes, ca_pem: bytes
) -> ssl.SSLContext:
    """Client side: presents the module's leaf, verifies the
    server's against the internal CA and its loopback name."""
    import tempfile
    from pathlib import Path

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    with tempfile.TemporaryDirectory() as tmp:
        cert_file = Path(tmp) / "cert.pem"
        key_file = Path(tmp) / "key.pem"
        ca_file = Path(tmp) / "ca.pem"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)
        ca_file.write_bytes(ca_pem)
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        context.load_verify_locations(cafile=ca_file)
    return context


__all__ = [
    "MAX_FRAME",
    "Channel",
    "Dispatcher",
    "TcpWireServer",
    "UnixWireServer",
    "WireClient",
    "WireServer",
    "client_mtls_context",
    "open_request",
    "open_request_detailed",
    "open_result",
    "read_frame",
    "seal_request",
    "seal_result",
    "serve_in_thread",
    "serve_tcp_mtls",
    "server_mtls_context",
    "tcp_client",
    "unix_client",
    "write_frame",
]
