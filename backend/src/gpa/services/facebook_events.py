"""Facebook Event creation (roadmap B-131).

B-058 built the draft and stopped there on purpose: creating
a real Facebook Event is posting outward as a Page — an act
that belongs to a human pressing a button. This module is
that button, and the boundary it presses through:

- The **protocol** is all the domain code knows: create,
  update, cancel, delete, and read back one Page event, in
  Meta's own field names (the B-058 draft's shape, unchanged).
- The **fixture double** is the deliverable in this
  environment: it behaves the way the Graph API behaves
  (sequential ids, a URL per event, copies that can be read
  back and edited behind the assistant's back — the
  reconciliation job's planted drift has to come from
  somewhere) against invented data only.
- The **live client** is NOT built here and nothing pretends
  otherwise: no Facebook Page or group account is connected
  to this project in this environment, no real token exists
  anywhere in this repository, and publishing is Volmarr's
  word alone besides. The process client is therefore the
  *unavailable* client, whose every call refuses in plain
  words; the recorded-fixture integration test is this
  slice's acceptance, and the roadmap's "live smoke test on
  a throwaway event" is recorded in the test report as
  **NOT RUN — it awaits a connected account and Volmarr's
  word**.

The publish law, kept from B-058 and the whole project:

- a PUBLISHED event can be created on the Page; a draft
  event is 404 here as on every outward surface (the bright
  line), and a cancelled event is announced as cancelled,
  never re-announced as live;
- once created, the outside copy is a COPY: a later edit
  here shows as "differs from Facebook copy" until a person
  presses the button again — nothing overwrites either side
  silently, in either direction;
- cancelling the event here does not reach out on its own:
  the next press carries the cancellation out, and the copy
  ends as a cancelled event on the Page, readable as such.
"""

import hashlib
import json
from typing import Protocol

from sqlalchemy.orm import Session

from gpa.config import Settings
from gpa.errors import AppError
from gpa.models.events import Event
from gpa.models.groups import Group
from gpa.models.integrations import ExternalEventLink, Integration
from gpa.services import audit
from gpa.services import facebook as draft_service
from gpa.services import integrations as integration_service

CONNECTOR_KIND = "facebook_events"


class FacebookUnavailable(Exception):
    """No live Facebook connection exists in this deployment.

    Raised by the unavailable client; the publish door turns
    it into a plain-words refusal. A configuration fact, not
    a failure of the platform.
    """


class FacebookClient(Protocol):
    """What the assistant may ask Facebook, and nothing more.

    Payloads are the B-058 draft shape (Meta's field names).
    Returned events carry ``id`` and ``url`` beside the
    payload fields, plus ``cancelled: true`` when the copy
    was cancelled on the Page side.
    """

    def ping(self) -> bool:
        """True when the connection answers."""
        ...

    def create_event(self, page_id: str, payload: dict) -> dict:
        """Create one event on one Page; returns the stored
        copy ({id, url, **payload})."""
        ...

    def update_event(self, page_id: str, external_id: str, payload: dict) -> dict:
        """Replace one Page event's fields with ``payload``."""
        ...

    def cancel_event(self, page_id: str, external_id: str) -> dict:
        """Cancel one Page event (the copy stays, marked)."""
        ...

    def delete_event(self, page_id: str, external_id: str) -> None:
        """Remove one Page event entirely."""
        ...

    def get_event(self, page_id: str, external_id: str) -> dict | None:
        """Read one Page event back, or None when it is gone."""
        ...


class UnavailableFacebookClient:
    """The default process client: no account is connected.

    Every call raises FacebookUnavailable with the same
    plain sentence — the honest alternative to silently
    succeeding against nothing, which would fake an
    integration the team has not wired (and may not wire
    without Volmarr's word: publishing is his to order).
    """

    MESSAGE = (
        "No Facebook Page is connected to this deployment — "
        "event creation runs against recorded fixtures until "
        "a deployment connects a Page and a person presses "
        "the publish button."
    )

    def ping(self) -> bool:
        raise FacebookUnavailable(self.MESSAGE)

    def create_event(self, page_id: str, payload: dict) -> dict:
        raise FacebookUnavailable(self.MESSAGE)

    def update_event(self, page_id: str, external_id: str, payload: dict) -> dict:
        raise FacebookUnavailable(self.MESSAGE)

    def cancel_event(self, page_id: str, external_id: str) -> dict:
        raise FacebookUnavailable(self.MESSAGE)

    def delete_event(self, page_id: str, external_id: str) -> None:
        raise FacebookUnavailable(self.MESSAGE)

    def get_event(self, page_id: str, external_id: str) -> dict | None:
        raise FacebookUnavailable(self.MESSAGE)


class FixtureFacebookClient:
    """The recorded-fixture double (tests + offline demos).

    Behaves the way the protocol says the Graph API behaves:
    ids are sequential per client, every event has a URL,
    updates replace the payload whole, a cancellation keeps
    the copy standing and marked, and a delete removes it.
    Tests reach behind it — :meth:`edit_event` and
    :meth:`remove_event` — to plant the outside drift the
    reconciliation job (B-137) exists to notice. Its data is
    invented; its domain is the reserved ``.example`` one, so
    no URL it mints can ever point at a real event.
    """

    def __init__(self) -> None:
        self._events: dict[str, dict] = {}
        self._next = 0

    def ping(self) -> bool:
        return True

    def _url(self, external_id: str) -> str:
        return f"https://www.facebook.example/events/{external_id}"

    def create_event(self, page_id: str, payload: dict) -> dict:
        self._next += 1
        external_id = f"fb-event-{self._next}"
        stored = {
            "id": external_id,
            "url": self._url(external_id),
            "page_id": page_id,
            **payload,
        }
        self._events[external_id] = stored
        return dict(stored)

    def update_event(self, page_id: str, external_id: str, payload: dict) -> dict:
        stored = self._events.get(external_id)
        if stored is None:
            raise FacebookUnavailable(
                f"The Facebook copy {external_id} no longer exists."
            )
        stored.update(payload)
        stored.pop("cancelled", None)
        return dict(stored)

    def cancel_event(self, page_id: str, external_id: str) -> dict:
        stored = self._events.get(external_id)
        if stored is None:
            raise FacebookUnavailable(
                f"The Facebook copy {external_id} no longer exists."
            )
        stored["cancelled"] = True
        return dict(stored)

    def delete_event(self, page_id: str, external_id: str) -> None:
        self._events.pop(external_id, None)

    def get_event(self, page_id: str, external_id: str) -> dict | None:
        stored = self._events.get(external_id)
        return dict(stored) if stored is not None else None

    # -- Behind the glass: how tests plant outside drift. --

    def edit_event(self, external_id: str, **fields) -> None:
        """Change the Page copy directly, as a person editing
        on Facebook would — the assistant is not told."""
        self._events[external_id].update(fields)

    def remove_event(self, external_id: str) -> None:
        """Delete the Page copy directly (a Page admin's act)."""
        self._events.pop(external_id, None)


_client: FacebookClient = UnavailableFacebookClient()


def set_client(client: FacebookClient | None) -> None:
    global _client
    _client = client if client is not None else UnavailableFacebookClient()


def get_client() -> FacebookClient:
    return _client


# ---------------------------------------------------------------------
# The publish door
# ---------------------------------------------------------------------


def payload_for(db: Session, event: Event) -> dict:
    """The canonical Facebook payload for one event — the
    B-058 draft's payload, the exact dict whose hash the
    link row remembers."""
    return draft_service.build_draft(db, event)["payload"]


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def projected(outside: dict, payload: dict) -> dict:
    """An outside copy projected onto the payload's keys —
    the comparison B-137's reconciliation and the press's
    repair both use: the outside system's envelope fields
    (its own id, its URL) ride along in its records and are
    not part of the event's facts."""
    return {key: outside.get(key) for key in payload}


def _integration(db: Session, group: Group) -> Integration:
    """The group's Facebook connection, or a plain refusal.

    Creating outward needs a connected Page: an integration
    row whose sealed config names the page and carries its
    token (the token never leaves the envelope here — it is
    checked for presence, because the client boundary is
    where a deployment's live client would read it)."""
    row = (
        db.query(Integration)
        .filter(
            Integration.group_id == group.id,
            Integration.kind == CONNECTOR_KIND,
        )
        .order_by(Integration.created_at, Integration.id)
        .first()
    )
    if row is None:
        raise AppError(
            "validation_error",
            detail=(
                "No Facebook Page is connected for this group. "
                "Connect one under Integrations first — creating "
                "a Facebook event needs the group's own Page."
            ),
        )
    return row


def integration_config(settings: Settings, row: Integration) -> dict:
    """The connection's config, with its plain-words gate:
    a page id and a token must both be present before any
    press can travel."""
    config = integration_service.read_config(settings, row)
    missing = [key for key in ("page_id", "access_token") if not config.get(key)]
    if missing:
        raise AppError(
            "validation_error",
            detail=(
                "The Facebook connection is incomplete — its "
                f"config is missing: {', '.join(missing)}. Add "
                "them under Integrations, then press publish again."
            ),
        )
    return config


def _client_or_refusal() -> FacebookClient:
    try:
        client = get_client()
        client.ping()
    except FacebookUnavailable as err:
        raise AppError("validation_error", detail=str(err)) from err
    return client


def _link(db: Session, event: Event) -> ExternalEventLink | None:
    return (
        db.query(ExternalEventLink)
        .filter(
            ExternalEventLink.event_id == event.id,
            ExternalEventLink.connector_kind == CONNECTOR_KIND,
        )
        .one_or_none()
    )


def facebook_state(db: Session, *, group: Group, event: Event) -> dict:
    """Where this event stands on Facebook, in plain facts:
    not published, published (and whether the copy still
    matches the event here), or cancelled there."""
    link = _link(db, event)
    if link is None:
        return {
            "status": "not_published",
            "external_id": None,
            "url": None,
            "differs": False,
        }
    if event.status == "cancelled":
        return {
            "status": "cancelled",
            "external_id": link.external_id,
            "url": link.external_url,
            "differs": False,
        }
    differs = link.payload_hash != payload_hash(payload_for(db, event))
    return {
        "status": "published",
        "external_id": link.external_id,
        "url": link.external_url,
        "differs": differs,
    }


def publish_event(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    event: Event,
) -> dict:
    """THE PRESS (organiser, publish scope — the router's
    doors). Create the event's Facebook copy, or bring the
    existing copy back into agreement, or carry a
    cancellation out. Returns the facebook_state dict plus
    ``action``: created | updated | cancelled | unchanged.

    Nothing here runs without the press: edits made here do
    NOT travel on their own (they show as `differs` until
    this door is used), and nothing on the Page side is ever
    read back into the event — the copy is a copy.
    """
    integration = _integration(db, group)
    config = integration_config(settings, integration)
    client = _client_or_refusal()
    page_id = str(config["page_id"])
    link = _link(db, event)

    if event.status == "cancelled":
        if link is None:
            # B-058's law: a cancelled event is announced as
            # cancelled by the calendar, never created as live.
            raise AppError(
                "conflict",
                detail=(
                    "A cancelled event is not created on Facebook — "
                    "it is announced as cancelled, not as live."
                ),
            )
        client.cancel_event(page_id, link.external_id)
        audit.record(
            db, actor=actor_label, action="facebook.cancelled",
            group_id=group.id, entity_type="event", entity_id=event.id,
            detail={"external_id": link.external_id},
        )
        db.commit()
        return {**facebook_state(db, group=group, event=event), "action": "cancelled"}

    payload = payload_for(db, event)  # Draft events 404 inside.
    digest = payload_hash(payload)
    if link is not None and link.payload_hash == digest:
        # Our side has not moved since the last press. One
        # Page-side case still acts: a copy that was DELETED
        # over there is recreated by this press (the link
        # follows it to its new id) — restoring a deletion is
        # the press's own business. A copy that was EDITED
        # over there is NOT overwritten: it stands for a
        # person to settle (B-132's one-way law, B-137's
        # report) — the press returns "unchanged" and the
        # organiser's road is unpublish + publish, deliberate.
        outside = client.get_event(page_id, link.external_id)
        if outside is not None:
            return {**facebook_state(db, group=group, event=event), "action": "unchanged"}
        if outside is None:
            stored = client.create_event(page_id, payload)
            link.external_id = stored["id"]
            link.external_url = stored.get("url", link.external_url)
            audit.record(
                db, actor=actor_label, action="facebook.created",
                group_id=group.id, entity_type="event", entity_id=event.id,
                detail={"external_id": link.external_id, "url": link.external_url},
            )
            db.commit()
            return {**facebook_state(db, group=group, event=event), "action": "created"}
    if link is None:
        stored = client.create_event(page_id, payload)
        link = ExternalEventLink(
            group_id=group.id, event_id=event.id,
            connector_kind=CONNECTOR_KIND,
            external_id=stored["id"], external_url=stored.get("url", ""),
            payload_hash=digest,
        )
        db.add(link)
        action = "created"
    else:
        stored = client.update_event(page_id, link.external_id, payload)
        link.external_url = stored.get("url", link.external_url)
        link.payload_hash = digest
        action = "updated"
    audit.record(
        db, actor=actor_label, action=f"facebook.{action}",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"external_id": link.external_id, "url": link.external_url},
    )
    db.commit()
    return {**facebook_state(db, group=group, event=event), "action": action}


def unpublish_event(
    db: Session,
    settings: Settings,
    *,
    actor_label: str,
    group: Group,
    event: Event,
) -> dict:
    """Remove the Facebook copy (the reversible half of the
    press): the Page event is deleted and the link row goes
    with it. The event here is untouched."""
    integration = _integration(db, group)
    config = integration_config(settings, integration)
    client = _client_or_refusal()
    link = _link(db, event)
    if link is None:
        raise AppError(
            "not_found", detail="This event has no Facebook copy to remove."
        )
    client.delete_event(str(config["page_id"]), link.external_id)
    external_id = link.external_id
    db.delete(link)
    audit.record(
        db, actor=actor_label, action="facebook.removed",
        group_id=group.id, entity_type="event", entity_id=event.id,
        detail={"external_id": external_id},
    )
    db.commit()
    return facebook_state(db, group=group, event=event)
