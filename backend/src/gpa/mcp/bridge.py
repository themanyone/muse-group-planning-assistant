"""The MCP bridge — this surface's one door into the backend (B-204).

Roadmap B-204's load-bearing sentence: the MCP server talks
**only** through the internal encrypted API (B-162 → B-166),
under its own service identity (B-164). This module is that
door, and it is deliberately the ONLY backend connection the
rest of this package holds:

* every call is built by the internal client as a B-162
  envelope with ``caller="mcp"`` and dispatched through the
  one dispatch path — registry lookup, scope check against the
  ``mcp`` caller's grants, the owning module's handler — so an
  MCP call can do exactly what the registry allows this caller
  and nothing else;
* each call is one transaction, on the wire door's law
  (B-163/B-166): commit on success, roll back on any refusal,
  so a tool call never leaves half an act behind;
* every dispatch is audited through the B-165 hook installed
  here, in the call's own transaction — actor ``module:mcp``,
  the operation, the scope, this identity's key id, and the
  result; never the payload;
* the identity is B-164's: a distinct Ed25519 key pair for the
  name ``mcp``, read from the identities directory in
  production or derived from the app secret in the documented
  development posture — never a borrowed human credential.

Nothing in this file imports a service, a router, or a model.
If an operation is not in the registry, this door refuses it
with ``internal_unknown_operation``, and the fix is to
register the operation (B-206 grows the registry's MCP-facing
use), never to widen this door.
"""

from __future__ import annotations

from importlib import metadata

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.db import Database
from gpa.internal import auditing, identity, registry
from gpa.internal.client import InternalClient
from gpa.internal.handlers import register_all

# The caller name this surface speaks as, everywhere: the
# registry's grant table, the identity store, and the audit
# stream all know the MCP surface by this one word.
CALLER = "mcp"


def package_version() -> str:
    """The backend version this server ships with.

    Read from the installed package metadata so a release can
    never report a version its own build did not stamp; the
    literal fallback is the development tree, whose metadata
    an editable install still provides in practice.
    """
    try:
        return metadata.version("group-planning-assistant-backend")
    except metadata.PackageNotFoundError:
        return "0.1.0"


class InternalBridge:
    """One MCP process's connection to the internal API.

    Owns the database handle (the chassis object — connections,
    never domain logic), a dedicated internal client (never the
    process-wide default: the MCP process's audit hook and
    settings are its own), and the ``mcp`` service identity.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        client: InternalClient | None = None,
    ) -> None:
        """Build the door from settings, with test seams.

        ``database`` and ``client`` are injectable so tests can
        stand the bridge on an isolated app database and watch
        its calls; a serving process passes neither and gets
        the deployment's own database and a fresh client.
        """
        self.settings = settings
        self._database = database or Database(settings.database_url)
        if database is None and settings.auto_create_tables:
            # The demo posture: a cold MCP process against a
            # fresh file creates the schema the same way the
            # API process's dev road does. Production serves
            # against a migrated database and sets this off.
            self._database.create_all()
        self._client = client or InternalClient(settings)
        # B-165: every dispatch from this process leaves its
        # audit line, exactly as the API and worker processes'
        # composition roots arrange for their own clients.
        auditing.install(client=self._client, settings=settings)
        store = identity.load_store(settings)
        self._identity = store.own.get(CALLER)

    @property
    def caller(self) -> str:
        """The internal caller name every bridge call speaks as."""
        return CALLER

    @property
    def key_id(self) -> str:
        """This process's service-identity key id (B-164).

        Empty only in the pathological case of an identities
        directory that holds no ``mcp`` seed — the calls
        themselves would still be scope-checked, and the audit
        lines would show the gap rather than hide it.
        """
        return self._identity.key_id if self._identity is not None else ""

    def new_session(self) -> Session:
        """Open one database session from the chassis factory."""
        return self._database.session()

    def call(self, operation: str, payload: dict | None = None) -> dict:
        """Make one internal call as ``mcp`` and commit it.

        The per-call transaction is the wire door's law: the
        handler runs in this call's session, success commits
        (handlers whose direct form already committed simply
        commit twice, which is a no-op), and ANY refusal or
        error rolls the whole call back before the error
        travels to the tool layer unchanged — one error
        language for the whole system.
        """
        session = self.new_session()
        try:
            data = self._client.call(
                session,
                caller=CALLER,
                operation=operation,
                payload=payload or {},
            )
            session.commit()
            return data
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def operations(self) -> list[dict]:
        """The registered internal operations, as plain data.

        Name, owning module, required scope, and the registry's
        own one-line summary for each — the factual basis the
        B-206 tool registry is generated from, served here so
        an agent (and a test) can see exactly which doors exist
        rather than guess.
        """
        register_all()
        return [
            {
                "name": operation.name,
                "owner": operation.owner,
                "required_scope": operation.required_scope,
                "summary": operation.summary,
            }
            for operation in sorted(registry.OPERATIONS.values(), key=lambda op: op.name)
        ]

    def info(self) -> dict:
        """This bridge's identity facts, for the server-info tool.

        Backend version, caller name, key id, and the count of
        registered operations — operational facts about the
        server itself, carrying no group content whatsoever.
        """
        return {
            "backend_version": package_version(),
            "caller": CALLER,
            "key_id": self.key_id,
            "operations_count": len(self.operations()),
        }
