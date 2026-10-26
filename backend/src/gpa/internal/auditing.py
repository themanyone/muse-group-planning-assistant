"""Internal API audit (roadmap §6A.4, B-165).

Every internal call leaves a line in the B-019 audit stream:
**caller identity, operation, scope, result — never the
payload.** The line is written by the client's audit hook
inside the caller's own transaction, so the story and the act
commit or roll back together.

What a line carries, and why:

* ``actor`` — ``module:<caller>``: the internal stream's
  actors are modules, and the prefix keeps them unmistakable
  beside the ``user:``/``seat:`` actors of the human stream.
* ``action`` — ``internal.<operation>``.
* ``detail`` — the caller again (for filter symmetry), its
  **key id** (B-164: which key spoke, so a rotation-era line
  can be attributed to old key or new), the operation, the
  scope exercised, the owning module (the roadmap's "module
  tag"), and the result: ``ok`` or the refusal's error code.
* ``entity_id`` — the envelope nonce, so one call's line can
  be picked out of the stream.
* ``group_id`` — the payload's group id when it carries one:
  an identifier, not content — the same class of fact the
  human stream already records for every group act.

The acceptance this serves: a full flow's internal story —
who called what, in what order, with what result — can be
reconstructed from audit alone, with no user content in it.
"""

from __future__ import annotations

from gpa.internal import registry
from gpa.services import audit


def make_audit_hook(*, store=None, settings=None):
    """Build the hook the client calls per dispatch.

    ``store`` (an IdentityStore) resolves caller → key id;
    without one, a store is loaded lazily from settings on
    first use and kept — key derivation is not free, and a
    hook runs on every call."""
    resolved: dict[str, object] = {}

    def key_id_for(caller: str) -> str:
        if "store" not in resolved:
            if store is not None:
                resolved["store"] = store
            else:
                from gpa.config import get_settings
                from gpa.internal import identity

                resolved["store"] = identity.load_store(settings or get_settings())
        loaded = resolved["store"]
        own = loaded.own.get(caller)  # type: ignore[attr-defined]
        if own is not None:
            return own.key_id
        accepted = loaded.accepted.get(caller, [])  # type: ignore[attr-defined]
        if accepted:
            from gpa.internal import crypto

            return crypto.key_id_of(accepted[0])
        return ""

    def hook(db, envelope, *, ok: bool, error_code: str | None) -> None:
        operation = registry.get_operation(envelope.operation)
        group_id = envelope.payload.get("group_id")
        audit.record(
            db,
            actor=f"module:{envelope.caller}",
            action=f"internal.{envelope.operation}",
            group_id=group_id if isinstance(group_id, str) else None,
            entity_type="internal_call",
            entity_id=envelope.nonce,
            detail={
                "caller": envelope.caller,
                "key_id": key_id_for(envelope.caller),
                "operation": envelope.operation,
                "scope": envelope.scope,
                "module": operation.owner if operation else "",
                "result": "ok" if ok else (error_code or "error"),
            },
        )

    return hook


def install(*, client=None, settings=None, store=None) -> None:
    """Install the hook on a client (the shared default when
    none is named). Composition roots call this once: the
    API app, the workers — every process that dispatches
    internal calls keeps the same story."""
    from gpa.internal.client import default_client

    target = client if client is not None else default_client()
    target.audit_hook = make_audit_hook(store=store, settings=settings)


__all__ = ["install", "make_audit_hook"]
