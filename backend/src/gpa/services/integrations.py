"""The integration framework (roadmap B-127).

One record per connected outside system per group, behind
which Phase 12's concrete integrations (Facebook Events,
Google sync) — and Phase 11's own Samtal bridge — register:

- **kind** from the closed vocabulary in
  models/integrations.py (a typo'd kind is refused, never
  stored);
- **config** as a JSON object, sealed before it touches the
  database (services/sealing.py) and NEVER read back through
  the API — responses carry only the config keys' NAMES, so
  an admin can confirm "the token is in there" without the
  token ever crossing the wire again;
- **health**: a check per kind that records its verdict on
  the row — status, the moment it ran, the last success, and
  the last error IN PLAIN WORDS an admin can act on. Phase
  12's kinds answer honestly until their slices land:
  "not connected yet", on the record, not a fake green.
"""

import json

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.integrations import INTEGRATION_KINDS, Integration
from gpa.services import audit
from gpa.services import sealing


def _validate_kind(kind: str) -> str:
    if kind not in INTEGRATION_KINDS:
        raise AppError(
            "validation_error",
            detail=f"Integration kind must be one of: {', '.join(INTEGRATION_KINDS)}.",
        )
    return kind


def create_integration(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    kind: str,
    name: str = "",
    config: dict | None = None,
) -> Integration:
    """Register one integration; its config is sealed in
    this call and the plaintext is never kept."""
    _validate_kind(kind)
    row = Integration(
        group_id=group.id,
        kind=kind,
        name=name or kind,
        config_sealed=sealing.seal(settings, json.dumps(config or {})),
        status="unconfigured",
        created_by=actor_label,
    )
    db.add(row)
    audit.record(
        db, actor=actor_label, action="integration.created",
        group_id=group.id, entity_type="integration", entity_id=row.id,
        detail={"kind": kind, "config_keys": sorted((config or {}).keys())},
    )
    db.commit()
    return row


def get_integration(db: Session, group: Group, integration_id: str) -> Integration:
    """Fetch one integration inside its group, or a clean
    404 — ids never leak across group walls (the B-023 rule,
    kept for every new surface)."""
    row = db.get(Integration, integration_id)
    if row is None or row.group_id != group.id:
        raise AppError("not_found", detail="That integration could not be found.")
    return row


def list_integrations(db: Session, group: Group) -> list[Integration]:
    """A group's integrations, oldest first."""
    return (
        db.query(Integration)
        .filter(Integration.group_id == group.id)
        .order_by(Integration.created_at, Integration.id)
        .all()
    )


def read_config(settings: Settings, integration: Integration) -> dict:
    """The integration's config, unsealed — INTERNAL use
    only (health checks, Phase 12's clients). No router
    returns this; the wire carries key names, never values."""
    if not integration.config_sealed:
        return {}
    try:
        return json.loads(sealing.open_sealed(settings, integration.config_sealed))
    except ValueError:
        return {}


def config_keys(settings: Settings, integration: Integration) -> list[str]:
    """The NAMES of the config keys held — the only config
    fact the API ever states."""
    return sorted(read_config(settings, integration).keys())


def update_integration(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    integration_id: str,
    name: str | None = None,
    config: dict | None = None,
) -> Integration:
    """Rename and/or re-configure one integration. A present
    config replaces the sealed config WHOLE and the health
    verdict resets to unconfigured — yesterday's green was
    about yesterday's config."""
    row = get_integration(db, group, integration_id)
    changed = []
    if name is not None:
        row.name = name
        changed.append("name")
    if config is not None:
        row.config_sealed = sealing.seal(settings, json.dumps(config))
        row.status = "unconfigured"
        row.last_error = ""
        changed.append("config")
    audit.record(
        db, actor=actor_label, action="integration.updated",
        group_id=group.id, entity_type="integration", entity_id=row.id,
        detail={"fields": changed},
    )
    db.commit()
    return row


def delete_integration(
    db: Session, *, actor_label: str, group: Group, integration_id: str
) -> None:
    """Remove one integration and its sealed config with it."""
    row = get_integration(db, group, integration_id)
    db.delete(row)
    audit.record(
        db, actor=actor_label, action="integration.deleted",
        group_id=group.id, entity_type="integration", entity_id=row.id,
        detail={"kind": row.kind},
    )
    db.commit()


def check_health(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    integration_id: str,
) -> Integration:
    """Run this integration's health check and record the
    verdict on the row — the record is the deliverable: an
    admin reading the list later sees WHEN it was checked
    and WHAT it said, in plain words."""
    row = get_integration(db, group, integration_id)
    row.last_checked_at = now_utc()
    status, message = _run_check(db, settings, row)
    row.status = status
    if status == "ok":
        row.last_error = ""
        row.last_success_at = now_utc()
    else:
        row.last_error = message
    audit.record(
        db, actor=actor_label, action="integration.health_checked",
        group_id=group.id, entity_type="integration", entity_id=row.id,
        detail={"kind": row.kind, "status": status},
    )
    db.commit()
    return row


def _run_check(db: Session, settings: Settings, row: Integration) -> tuple[str, str]:
    """One kind's check, returning (status, plain words).

    - samtal: the client boundary answers or it does not —
      in this environment the unavailable client's refusal
      IS the honest verdict, recorded verbatim.
    - facebook_events / google_calendar: their slices are
      Phase 12's. Until then the check says so and the
      status stays unconfigured — never a fake green.
    - custom: nothing remote exists to check; a config that
      unseals is the whole of its health."""
    if row.kind == "samtal":
        from gpa.services import samtal as samtal_service

        try:
            samtal_service.get_client().ping()
            return "ok", ""
        except samtal_service.SamtalUnavailable as err:
            return "error", str(err)
        except Exception as err:  # A broken client is a health fact too.
            return "error", f"The Samtal connection failed its check: {err}"
    if row.kind == "facebook_events":
        # B-131: the check is the client boundary's ping,
        # gated on the config carrying a Page and its token.
        # In this environment the boundary is the unavailable
        # client (no Page is connected), and ITS refusal is
        # the verdict — recorded, never dressed up as a green.
        from gpa.services import facebook_events as facebook_service

        config = read_config(settings, row)
        missing = [k for k in ("page_id", "access_token") if not config.get(k)]
        if missing:
            return (
                "unconfigured",
                "Not connected yet — add "
                f"{' and '.join(missing)} to this integration's "
                "config, then check again.",
            )
        try:
            facebook_service.get_client().ping()
            return "ok", ""
        except facebook_service.FacebookUnavailable as err:
            return "error", str(err)
        except Exception as err:  # A broken client is a health fact too.
            return "error", f"The Facebook connection failed its check: {err}"
    if row.kind == "google_calendar":
        # B-132: same shape as the Facebook check — config
        # gate first, then the boundary's ping, whose refusal
        # in this environment IS the honest verdict.
        from gpa.services import google_calendar as google_service

        config = read_config(settings, row)
        missing = [k for k in ("calendar_id", "access_token") if not config.get(k)]
        if missing:
            return (
                "unconfigured",
                "Not connected yet — add "
                f"{' and '.join(missing)} to this integration's "
                "config, then check again.",
            )
        try:
            google_service.get_client().ping()
            return "ok", ""
        except google_service.GoogleUnavailable as err:
            return "error", str(err)
        except Exception as err:  # A broken client is a health fact too.
            return "error", f"The Google connection failed its check: {err}"
    # custom
    try:
        sealing.open_sealed(settings, row.config_sealed) if row.config_sealed else ""
    except ValueError:
        return "error", "The stored config could not be unsealed — re-enter it."
    return "ok", ""
