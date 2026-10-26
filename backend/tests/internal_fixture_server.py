"""A standalone internal-API server for the transport tests.

NOT a test module (no test_ prefix): the cross-process test
in test_internal_transport.py launches this file as its own
Python process, so the round trip it proves is a REAL process
boundary — real Unix socket, real separate memory, real keys
held on only one side.

Configuration rides the environment, the way a deployed
process would receive it:

* ``GPA_FIXTURE_SOCKET`` — the Unix socket path to bind.
* ``GPA_FIXTURE_KEY_B64`` — the shared comms key (base64).
* ``GPA_FIXTURE_SERVER_SEED`` — this server's Ed25519 seed.
* ``GPA_FIXTURE_CLIENT_PUB`` — the client's public key.

It registers one operation, ``test.echo``, whose handler
hands the payload straight back — the transport is what is
under test, and an echo is the honest probe.
"""

import base64
import os
import sys
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gpa.internal import crypto, registry
from gpa.internal.client import InternalClient
from gpa.internal.transport import Channel, UnixWireServer, WireServer, serve_in_thread


def main() -> None:
    socket_path = os.environ["GPA_FIXTURE_SOCKET"]
    key = base64.b64decode(os.environ["GPA_FIXTURE_KEY_B64"])
    server_key = crypto.signing_key_from_seed(os.environ["GPA_FIXTURE_SERVER_SEED"])
    client_pub = os.environ["GPA_FIXTURE_CLIENT_PUB"]

    registry.register(
        registry.Operation(
            name="test.echo",
            owner="events",
            required_scope="test:echo",
            summary="Fixture echo for the transport tests.",
        ),
        lambda db, payload, ctx: {"echo": payload},
    )
    registry.grant("events", "test:echo")

    # The echo never touches the database, but the dispatcher
    # contract hands every call a session — a real one, on a
    # throwaway file, so the shape is the production shape.
    engine = create_engine(f"sqlite:///{socket_path}.db")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    client = InternalClient()

    def dispatch(request):
        session = sessions()
        try:
            return client.execute_envelope(session, request)
        finally:
            session.close()

    channel = Channel(
        key=key,
        signer_id="fixture-server",
        signing_key=server_key,
        public_keys={"events": client_pub},
    )
    server = UnixWireServer(socket_path, WireServer(channel, dispatch))
    serve_in_thread(server)
    print("ready", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
