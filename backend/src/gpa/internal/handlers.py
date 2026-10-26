"""Operation handlers — the internal API's composition root (B-162).

This module is the ONE place that imports the owning services
to wire them to operation names. It is imported lazily (the
client's first call, or a composition root at startup) because
the services import the client, and an eager import here would
close the circle.

The operations are the five families the roadmap names —
candidate creation, event publication, notification request,
AI job submission, integration call — plus the two supporting
operations the same families need (general job submission,
RSVP-promotion notification). Phase 19 (B-207) adds the first
READ-and-profile family — group reads, the member roster, a
group-settings edit, and person/seat profile reads and edits —
registered so the MCP surface's domain tools (and any future
surface) travel this same door instead of reaching around it.
Every handler:

* takes ids and values from the envelope payload — never ORM
  rows (the wire shape is the in-process shape, B-166);
* re-fetches the rows it needs inside the caller's session;
* keeps the direct call's transaction discipline exactly —
  handlers that replaced non-committing calls do not commit;
  job submission, whose direct form commits, still commits;
* returns plain data: ids and counts, never rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.internal import registry
from gpa.internal.client import CallContext
from gpa.internal.registry import Operation
from gpa.models.events import Event, Rsvp
from gpa.models.groups import Group, Membership, role_at_least
from gpa.models.identity import User
from gpa.models.nonprofit import Announcement
from gpa.models.notifications import Digest, Notification
from gpa.models.planning import Decision, OpenQuestion, Task
from gpa.models.sources import Source
from gpa.services import calendar_views as calendar_service
from gpa.services import candidates as candidate_service
from gpa.services import events as event_service
from gpa.services import announcements as announcement_service
from gpa.services import digests as digest_service
from gpa.services import planning as planning_service
from gpa.services import sources as source_service
from gpa.services import rsvps as rsvp_service
from gpa.services import groups as group_service
from gpa.services import identity as identity_service
from gpa.services import jobs as job_service
from gpa.services import notifications as notify_service
from gpa.services import outbound as outbound_service
from gpa.services import extraction as extraction_service
from gpa.services import audit as audit_service
from gpa.services import usage as usage_service
from gpa.ai.client import ModelResult
from gpa.services import transcribe as transcribe_service
from gpa.services import vision as vision_service


def _need(payload: dict, *keys: str) -> None:
    """Refuse a payload missing a required key, in the internal
    API's own problem code — a malformed internal call is a bug
    in a caller, and it should fail as loudly as a bad request
    at the external door."""
    missing = [key for key in keys if key not in payload]
    if missing:
        raise AppError(
            "internal_bad_payload",
            detail=f"Payload is missing: {', '.join(missing)}.",
        )


def _group(db: Session, group_id: str | None) -> Group | None:
    """Fetch the payload's group, or refuse a dangling id."""
    if group_id is None:
        return None
    group = db.get(Group, group_id)
    if group is None:
        raise AppError("not_found", detail="That group could not be found.")
    return group


def _moment(value: str | None) -> datetime | None:
    """Parse an ISO-8601 payload moment (``Z`` tolerated)."""
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- candidates.create ---------------------------------------------------


def _candidates_create(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Draft one candidate into the review queue (extraction
    module's door; the row's one author is
    ``candidates.create_candidate``). Does not commit."""
    _need(payload, "group_id", "kind", "title", "created_by")
    row = candidate_service.create_candidate(
        db,
        group_id=payload["group_id"],
        kind=payload["kind"],
        title=payload["title"],
        created_by=payload["created_by"],
        status=payload.get("status", "pending"),
        source_id=payload.get("source_id"),
        draft=payload.get("draft"),
        confidence=payload.get("confidence"),
        prompt_name=payload.get("prompt_name", ""),
        prompt_version=payload.get("prompt_version"),
        job_id=payload.get("job_id"),
        flags=payload.get("flags"),
    )
    return {"candidate_id": row.id}


# --- events.publish --------------------------------------------------------


def _events_publish(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Publish one draft event (the organiser's approval act for
    a directly-entered event). Delegates to the events service,
    whose transaction discipline is unchanged."""
    _need(payload, "group_id", "event_id", "actor")
    group = _group(db, payload["group_id"])
    event = event_service.publish_event(
        db, actor_label=payload["actor"], group=group, event_id=payload["event_id"]
    )
    return {"event_id": event.id, "version": event.version}


# --- notify.request --------------------------------------------------------


def _notify_request(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Write notification rows (never sends — the outbox law).

    The audience is either one named recipient or every
    recipient of an event (the notifications module resolves
    that list, preferences and quiet hours included, exactly
    as the direct loops did). Does not commit: the caller's
    transaction carries the rows, as it always did.
    """
    _need(payload, "kind", "subject", "body_text", "audience")
    group = _group(db, payload.get("group_id"))
    audience = payload["audience"]
    recipients: list[dict] = []
    if "event_id" in audience:
        recipients = notify_service.event_recipients(db, audience["event_id"])
    elif "label" in audience:
        user = None
        if audience.get("user_id"):
            user = db.get(User, audience["user_id"])
        recipients = [
            {"user": user, "label": audience["label"], "email": audience.get("email", "")}
        ]
    else:
        raise AppError(
            "internal_bad_payload",
            detail="Audience must name an event_id or a label.",
        )
    created_ids: list[str] = []
    suppressed = 0
    for recipient in recipients:
        row = notify_service.create_notification(
            db,
            group=group,
            recipient_user=recipient["user"],
            recipient_label=recipient["label"],
            recipient_email=recipient["email"],
            kind=payload["kind"],
            subject=payload["subject"],
            body_text=payload["body_text"],
            body_html=payload.get("body_html", ""),
            event_id=payload.get("event_id"),
            urgent=payload.get("urgent", False),
            send_after=_moment(payload.get("send_after")),
            reminder_rule=payload.get("reminder_rule", ""),
            rsvp_id=payload.get("rsvp_id"),
        )
        if row is None:
            suppressed += 1
        else:
            created_ids.append(row.id)
    return {"created_ids": created_ids, "suppressed": suppressed}


def _notify_rsvp_promotion(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Tell a waitlisted member they have a place (the RSVP
    module's one cross-module notification, as an operation)."""
    _need(payload, "group_id", "event_id", "rsvp_id")
    group = _group(db, payload["group_id"])
    event = db.get(Event, payload["event_id"])
    rsvp = db.get(Rsvp, payload["rsvp_id"])
    if event is None or rsvp is None:
        raise AppError("not_found", detail="That event or RSVP could not be found.")
    notify_service.notify_rsvp_promotion(db, group=group, event=event, rsvp=rsvp)
    return {}


# --- jobs.enqueue / ai.submit_job ------------------------------------------

# The job kinds that are AI work: extraction, vision,
# transcription — the kinds whose submission §6A.4 names as
# the ai.submit_job operation. Read from the owning modules so
# a renamed kind cannot drift from this list.
AI_JOB_KINDS = frozenset(
    {
        extraction_service.JOB_KIND,
        vision_service.JOB_KIND,
        transcribe_service.JOB_KIND,
    }
)


def _enqueue(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Commit one queued job row (submission commits — the
    direct form did, so the operation does)."""
    _need(payload, "kind")
    job = job_service.enqueue(
        db,
        ctx.settings,
        kind=payload["kind"],
        payload=payload.get("payload") or {},
        run_at=_moment(payload.get("run_at")),
        max_attempts=payload.get("max_attempts"),
    )
    return {"job_id": job.id}


def _ai_submit_job(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Submit AI work: jobs.enqueue with the kind checked
    against the AI kinds — a caller may not use the AI door to
    submit arbitrary background work."""
    _need(payload, "kind")
    if payload["kind"] not in AI_JOB_KINDS:
        raise AppError(
            "internal_bad_payload",
            detail=f"'{payload['kind']}' is not an AI job kind.",
        )
    return _enqueue(db, payload, ctx)


# --- integrations.emit -------------------------------------------------------


def _integrations_emit(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Announce one act to the group's subscribed endpoints
    (webhook deliveries + their jobs, in the caller's
    transaction — outbound.emit does not commit, and neither
    does this). Event acts let the handler build the wire data
    from the event row, so callers pass facts, not shapes."""
    _need(payload, "group_id", "kind")
    group = _group(db, payload["group_id"])
    if "data" in payload:
        data = payload["data"]
    elif "event_id" in payload:
        event = db.get(Event, payload["event_id"])
        if event is None:
            raise AppError("not_found", detail="That event could not be found.")
        data = outbound_service.event_data(event, **(payload.get("event_extra") or {}))
    else:
        raise AppError(
            "internal_bad_payload",
            detail="Emit needs either data or an event_id.",
        )
    deliveries = outbound_service.emit(db, group=group, kind=payload["kind"], data=data)
    return {"deliveries": len(deliveries)}


# --- groups.get / groups.list_members / groups.update (B-207) -------------
#
# The group family the MCP domain tools stand on. Reads return
# plain serialised facts — the same fields the HTTP API's group
# and roster responses carry — never rows, and never a field the
# API itself withholds (no budget internals, no digest wiring:
# those are operator settings screens, not a roster's business).
# The settings edit is deliberately NARROWER than the HTTP
# door's: only the five plain fields below travel this
# operation, and an unknown field is refused by name rather
# than silently set — a surface that can set any attribute by
# string is a surface that can set role ladders by typo.

# The group-settings fields the groups.update operation carries.
_GROUP_EDITABLE_FIELDS = frozenset(
    {"name", "description", "visibility", "home_timezone", "default_place"}
)


def _group_data(group: Group) -> dict:
    """One group as plain data: the fields every surface shows."""
    return {
        "group_id": group.id,
        "name": group.name,
        "slug": group.slug,
        "description": group.description,
        "visibility": group.visibility,
        "home_timezone": group.home_timezone,
        "default_place": group.default_place,
    }


def _seat_data(seat) -> dict:
    """One agent seat as plain data, profile included (B-202)."""
    return {
        "seat_id": seat.id,
        "group_id": seat.group_id,
        "name": seat.name,
        "role": seat.role,
        "description": seat.description,
        "avatar_url": seat.avatar_url,
    }


def _groups_get(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Read one group by id (the groups service's own fetch,
    whose not_found is the answer for a dangling id)."""
    _need(payload, "group_id")
    return _group_data(group_service.get_group(db, payload["group_id"]))


def _groups_list_members(db: Session, payload: dict, ctx: CallContext) -> dict:
    """The group's full roster: person memberships AND seats.

    One roster, both principal kinds (the seat-equality law,
    B-202): a caller planning with a group needs its people and
    its seats in one answer, each kind labelled as itself —
    persons by membership (role + invite status), seats by
    their seat rows (role + profile).
    """
    _need(payload, "group_id")
    group = group_service.get_group(db, payload["group_id"])
    members = [
        {
            "user_id": user.id,
            "email": user.email,
            "name": user.name,
            "role": membership.role,
            "status": membership.status,
        }
        for membership, user in group_service.list_members(db, group)
    ]
    seats = [_seat_data(seat) for seat in group_service.list_seats(db, group)]
    return {
        "group_id": group.id,
        "members": members,
        "seats": seats,
        "count": len(members) + len(seats),
    }


def _groups_update(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Edit a group's plain settings through the groups service.

    The service owns the edit, its validation (visibility is
    the closed vocabulary), and its audit line — attributed to
    the payload's ``actor`` label, so a surface's act is
    recorded as that surface's principal, never as the gateway.
    The service commits, as its direct form does.
    """
    _need(payload, "group_id", "actor")
    changes = payload.get("changes") or {}
    unknown = sorted(set(changes) - _GROUP_EDITABLE_FIELDS)
    if unknown:
        raise AppError(
            "internal_bad_payload",
            detail=(
                "groups.update carries only "
                f"{', '.join(sorted(_GROUP_EDITABLE_FIELDS))}; "
                f"refused: {', '.join(unknown)}."
            ),
        )
    if not changes:
        raise AppError(
            "internal_bad_payload", detail="groups.update needs at least one change."
        )
    group = group_service.get_group(db, payload["group_id"])
    updated = group_service.update_group(
        db, actor_label=payload["actor"], group=group, changes=changes
    )
    return _group_data(updated)


# --- profile.get / profile.update (B-207) ----------------------------------
#
# One profile concept, two principal kinds (B-202's law): a
# person's profile is identity's fields (name, timezone,
# locale — never the email, never credentials), and a seat's
# profile is its display face (name, description, avatar URL —
# never its role or scopes, which are administration, not
# profile). The payload names exactly one principal: user_id
# for a person, seat_id + group_id for a seat (a seat exists
# only inside its one group, and another group's seat id is a
# not_found, exactly as at the HTTP door).


def _user_profile(user: User) -> dict:
    """One person's profile as plain data (no credential fields)."""
    return {
        "kind": "user",
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "timezone": user.timezone,
        "locale": user.locale,
    }


def _seat_profile(seat) -> dict:
    """One seat's profile as plain data (no role/scope admin)."""
    return {"kind": "seat", **_seat_data(seat)}


def _profile_target(payload: dict) -> str:
    """Which principal a profile payload names: user | seat.

    Both ids or neither is a malformed call, refused as such —
    a profile operation that guessed its target could edit the
    wrong principal's face, which is precisely the failure this
    family's shape exists to prevent.
    """
    has_user = bool(payload.get("user_id"))
    has_seat = bool(payload.get("seat_id"))
    if has_user == has_seat:
        raise AppError(
            "internal_bad_payload",
            detail="A profile payload names exactly one of user_id or seat_id.",
        )
    return "seat" if has_seat else "user"


def _profile_get(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Read one principal's profile (person or seat, see above)."""
    if _profile_target(payload) == "seat":
        _need(payload, "group_id")
        group = group_service.get_group(db, payload["group_id"])
        return _seat_profile(group_service.get_seat(db, group, payload["seat_id"]))
    return _user_profile(identity_service.get_user(db, payload["user_id"]))


# The per-kind profile fields a profile.update payload may set.
_PROFILE_USER_FIELDS = frozenset({"name", "timezone", "locale"})
_PROFILE_SEAT_FIELDS = frozenset({"name", "description", "avatar_url"})


def _profile_update(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Edit one principal's profile through its owning service.

    Persons: identity.update_profile (only the fields given).
    Seats: groups.update_seat_profile, whose audit line carries
    the payload's ``actor`` label — a seat editing its own face
    is audited as the seat (B-202), and the service refuses an
    emptied name. Fields outside the kind's own set are refused
    by name, for the same reason groups.update refuses its
    unknown fields: profile is a closed vocabulary per kind.
    """
    _need(payload, "actor")
    changes = payload.get("changes") or {}
    if _profile_target(payload) == "seat":
        _need(payload, "group_id")
        unknown = sorted(set(changes) - _PROFILE_SEAT_FIELDS)
        if unknown:
            raise AppError(
                "internal_bad_payload",
                detail=f"A seat profile has no fields: {', '.join(unknown)}.",
            )
        group = group_service.get_group(db, payload["group_id"])
        seat = group_service.update_seat_profile(
            db,
            actor_label=payload["actor"],
            group=group,
            seat_id=payload["seat_id"],
            changes=changes,
        )
        return _seat_profile(seat)
    unknown = sorted(set(changes) - _PROFILE_USER_FIELDS)
    if unknown:
        raise AppError(
            "internal_bad_payload",
            detail=f"A person profile has no fields: {', '.join(unknown)}.",
        )
    user = identity_service.get_user(db, payload["user_id"])
    updated = identity_service.update_profile(
        db,
        user,
        name=changes.get("name"),
        timezone=changes.get("timezone"),
        locale=changes.get("locale"),
    )
    return _user_profile(updated)


# --- candidates.list / candidates.get / the decision doors (B-208) --------
#
# The review-queue family. The queue is the product's centre
# of gravity — everything AI drafts lands here, and only a
# principal's decision moves it — so this family's law is the
# strictest in the catalogue:
#
# * Reads (candidates:read) return the queue's own shapes: the
#   list rows the HTTP queue serves, and for one candidate its
#   full detail — draft, flags, per-field provenance, and the
#   multi-approver progress (recorded / required, B-111).
# * Decisions (candidates:decide) are the three reviewer acts
#   — approve, reject, edit — and they delegate to the SAME
#   services the HTTP doors call, under the SAME rules. Above
#   all, candidates.approve travels groups.approve_candidate:
#   the one approval law (B-203) lives inside that service
#   (the group's review_min_role, its approvals-required
#   count, seat standing exactly equal to a person's), so an
#   approval arriving over this bus is checked identically to
#   one arriving over HTTP — there is no second, weaker door
#   for a surface to find. The payload therefore carries the
#   approving principal's facts (label, role, and its user or
#   seat id) exactly as the HTTP door's principal carries them.
# * candidates:decide maps only from the publish credential
#   rung (and admin): the HTTP decision doors are publish-scope
#   doors, and a draft-scoped seat key — which tops out at
#   draft — cannot decide, exactly as at those doors.


def _candidate_data(candidate) -> dict:
    """One candidate as plain data: the queue row's own fields.

    Mirrors schemas' candidate_row (draft and flags decoded
    from their JSON columns by the owning service's helpers),
    with ids named for the wire (candidate_id, not a bare id).
    """
    return {
        "candidate_id": candidate.id,
        "group_id": candidate.group_id,
        "kind": candidate.kind,
        "title": candidate.title,
        "status": candidate.status,
        "created_by": candidate.created_by,
        "approved_by": candidate.approved_by,
        "draft": candidate_service.draft_of(candidate),
        "confidence": candidate.confidence,
        "flags": candidate_service.flags_of(candidate),
        "source_id": candidate.source_id,
        "prompt_name": candidate.prompt_name,
        "prompt_version": candidate.prompt_version,
        "job_id": candidate.job_id,
        "merged_into_id": candidate.merged_into_id,
        "resulting_entity_type": candidate.resulting_entity_type,
        "resulting_entity_id": candidate.resulting_entity_id,
    }


def _candidates_list(db: Session, payload: dict, ctx: CallContext) -> dict:
    """The group's review queue, with the queue's own triage
    filters (kind, status, minimum confidence), newest first."""
    _need(payload, "group_id")
    group = group_service.get_group(db, payload["group_id"])
    rows = candidate_service.list_candidates(
        db,
        group,
        kind=payload.get("kind"),
        status=payload.get("status"),
        min_confidence=payload.get("min_confidence"),
    )
    return {
        "group_id": group.id,
        "candidates": [_candidate_data(row) for row in rows],
        "count": len(rows),
    }


def _candidates_get(db: Session, payload: dict, ctx: CallContext) -> dict:
    """One candidate in full: its row, its per-field provenance
    (the evidence the queue renders), and its approval progress
    under the group's multi-approver rule (recorded/required)."""
    _need(payload, "group_id", "candidate_id")
    group = group_service.get_group(db, payload["group_id"])
    candidate = candidate_service.get_candidate(db, group, payload["candidate_id"])
    recorded, required = group_service.review_progress(db, candidate)
    return {
        **_candidate_data(candidate),
        "provenance": candidate_service.provenance_of(db, candidate),
        "approvals_recorded": recorded,
        "approvals_required": required,
    }


def _candidates_approve(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Approve one candidate through the one approval law.

    The payload carries the approver's facts — ``actor`` label,
    ``role`` in the group, and exactly one of ``seat_id`` /
    ``user_id`` — and groups.approve_candidate applies the
    whole law (role floor, distinct-approver counting, the
    mint) in its own transaction. A seat below the group's
    approver role meets seat_cannot_approve here, exactly as
    at the HTTP door; below the approvals count the act is
    recorded and the candidate stays pending, which the
    returned row's status says plainly.
    """
    _need(payload, "group_id", "candidate_id", "actor", "role")
    group = group_service.get_group(db, payload["group_id"])
    candidate = group_service.approve_candidate(
        db,
        actor_label=payload["actor"],
        actor_is_seat=bool(payload.get("seat_id")),
        group=group,
        candidate_id=payload["candidate_id"],
        actor_user_id=payload.get("user_id"),
        actor_role=payload["role"],
        actor_seat_id=payload.get("seat_id"),
    )
    return _candidate_data(candidate)


def _candidates_reject(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Reject one candidate (the row and its draft stay — a
    rejection is a decision, not an erasure)."""
    _need(payload, "group_id", "candidate_id", "actor")
    group = group_service.get_group(db, payload["group_id"])
    candidate = candidate_service.reject_candidate(
        db, actor_label=payload["actor"], group=group, candidate_id=payload["candidate_id"]
    )
    return _candidate_data(candidate)


def _candidates_edit(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Edit one candidate's draft (a reviewer's correction:
    the draft is replaced, flags recomputed, the candidate
    marked edited — still undecided, still the queue's)."""
    _need(payload, "group_id", "candidate_id", "actor", "draft")
    group = group_service.get_group(db, payload["group_id"])
    candidate = candidate_service.edit_candidate(
        db,
        actor_label=payload["actor"],
        group=group,
        candidate_id=payload["candidate_id"],
        draft=payload["draft"],
    )
    return _candidate_data(candidate)


# --- events / RSVPs / calendar family (B-209) -----------------------------------
#
# The third domain family: the group's events as an agent
# reads and answers them. Three different laws meet here,
# and the operations are shaped by which law governs each:
#
# * VISIBILITY is the events service's bright line (B-049):
#   drafts exist only for organisers and above. The read
#   operations therefore carry the viewer's ROLE in their
#   payloads and delegate to the service's own visible forms
#   (list_events / get_visible_event) — an agent holding a
#   member's credential sees exactly what that member sees,
#   and a draft asked for below organiser is a 404, never a
#   redacted row. No viewer is named, so the B-105 sub-group
#   scope (which is stated over persons) does not narrow
#   further here — the same posture internal callers take.
# * CANCELLATION is an organiser act at the HTTP door
#   (publish scope + organiser role). The service states no
#   role law of its own, so the operation states it: the
#   payload's role must clear organiser, refused as
#   forbidden — the one place in this registry where a role
#   law lives in the handler, recorded here so a reader does
#   not go hunting the service for it.
# * ANSWERING (rsvps.set) is participation, not governance:
#   the HTTP door is draft-scoped, the answer is the
#   principal's own, and the service's upsert + waitlist
#   rebalance + promotion notices all ride along unchanged.


def _event_data(event: Event) -> dict:
    """Serialise one event row for the bus: the core of the
    HTTP projection (identity, when/where, status) without
    its level machinery — an internal consumer asked for the
    event, not for a rendering of it."""
    return {
        "event_id": event.id,
        "group_id": event.group_id,
        "title": event.title,
        "description": event.description,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "timezone": event.timezone,
        "all_day": event.all_day,
        "place_name": event.place_name,
        "place_address": event.place_address,
        "status": event.status,
        "host_label": event.host_label,
        "capacity": event.capacity,
        "cancelled_reason": event.cancelled_reason,
        "version": event.version,
    }


def _rsvp_data(row: Rsvp) -> dict:
    """Serialise one RSVP row for the bus (the HTTP response's
    fields, keyed the bus's way)."""
    return {
        "rsvp_id": row.id,
        "event_id": row.event_id,
        "principal_label": row.principal_label,
        "status": row.status,
        "guests": row.guests,
        "note": row.note,
        "waitlisted": row.waitlisted,
        "waitlist_position": row.waitlist_position,
    }


def _events_list(db: Session, payload: dict, ctx: CallContext) -> dict:
    """List the group's events as the payload's role may see
    them, soonest first (the service's order), optionally
    narrowed by status — the filter can narrow visibility,
    never widen it."""
    _need(payload, "group_id", "role")
    group = _group(db, payload["group_id"])
    events = event_service.list_events(
        db, group, role=payload["role"], status=payload.get("status")
    )
    return {
        "group_id": group.id,
        "events": [_event_data(event) for event in events],
        "count": len(events),
    }


def _events_get(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Read one event through the bright line: a draft asked
    for below organiser is a 404 from the service itself."""
    _need(payload, "group_id", "event_id", "role")
    group = _group(db, payload["group_id"])
    event = event_service.get_visible_event(
        db, group, payload["event_id"], payload["role"]
    )
    return _event_data(event)


def _events_cancel(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Cancel one event with the canceller's reason. The role
    law (organiser and above) is stated HERE (see the family
    note above); the act itself — the state change, the
    change-history row, the urgent tell-everyone fan-out
    through notify.request — is the service's, unchanged."""
    _need(payload, "group_id", "event_id", "actor", "role")
    if not role_at_least(payload["role"], "organiser"):
        raise AppError(
            "forbidden",
            detail="Only an organiser or above may cancel an event.",
        )
    group = _group(db, payload["group_id"])
    event = event_service.cancel_event(
        db,
        actor_label=payload["actor"],
        group=group,
        event_id=payload["event_id"],
        reason=payload.get("reason") or "",
    )
    return _event_data(event)


def _rsvps_list(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Every answer to one event, arrival order, plus the
    service's summary counts a calendar surface shows."""
    _need(payload, "group_id", "event_id")
    group = _group(db, payload["group_id"])
    event = event_service.get_event(db, group, payload["event_id"])
    rows = rsvp_service.list_rsvps(db, group, payload["event_id"])
    return {
        "group_id": group.id,
        "event_id": event.id,
        "rsvps": [_rsvp_data(row) for row in rows],
        "summary": rsvp_service.summary(db, event),
    }


def _rsvps_set(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Set the payload principal's own answer (the upsert is
    the service's; the waitlist rebalance and promotion
    notices ride along). The principal answers as itself:
    label plus its seat or user id, at least one of which
    the payload must carry."""
    _need(payload, "group_id", "event_id", "principal_label", "status")
    if not payload.get("seat_id") and not payload.get("user_id"):
        raise AppError(
            "internal_bad_payload",
            detail="rsvps.set names a seat_id or a user_id: an answer belongs to someone.",
        )
    group = _group(db, payload["group_id"])
    row = rsvp_service.set_rsvp(
        db,
        ctx.settings,
        principal_label=payload["principal_label"],
        user_id=payload.get("user_id"),
        seat_id=payload.get("seat_id"),
        group=group,
        event_id=payload["event_id"],
        status=payload["status"],
        guests=payload.get("guests") or 0,
        note=payload.get("note") or "",
    )
    return _rsvp_data(row)


def _utc_naive(value: str) -> datetime:
    """Parse a payload moment into the store's convention —
    naive UTC — the same conversion the calendar HTTP door
    applies to its window (an aware moment is converted; a
    naive one is taken as UTC already)."""
    moment = _moment(value)
    if moment.tzinfo is not None:
        from datetime import timezone

        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _calendar_agenda(db: Session, payload: dict, ctx: CallContext) -> dict:
    """One page of the group's agenda over a window: the
    calendar service's occurrence expansion (recurrences
    materialised), its cursor carried through opaquely —
    the caller passes it back for the next page and never
    reads inside it."""
    _need(payload, "group_id", "window_start", "window_end")
    group = _group(db, payload["group_id"])
    items, next_cursor = calendar_service.agenda(
        db,
        group,
        window_start=_utc_naive(payload["window_start"]),
        window_end=_utc_naive(payload["window_end"]),
        q=payload.get("q"),
        cursor=payload.get("cursor"),
        limit=payload.get("limit") or 50,
    )
    return {"group_id": group.id, "items": items, "next_cursor": next_cursor}


# --- sources / extraction family (B-210) -----------------------------------------
#
# The fourth domain family: the group's raw material and
# the asking for work over it. Sources are what extraction
# drafts FROM; the queue (B-208) is what it drafts INTO —
# the two families meet at the extraction request, which is
# this family's one consequential act.
#
# * READS are metadata-shaped except sources.get, which
#   returns the chunks OPENED through the sources service's
#   one read funnel (chunks are sealed at rest, B-167; the
#   funnel is where they open, and an agent reading a source
#   is reading it for its words). Positions are the service's
#   own order — the order provenance quotes point into.
# * CREATION states its permission record on the row
#   (granted_by = the payload's actor, granted_scope
#   verbatim), as the service writes it at every door: a
#   source never exists without the record of who granted
#   the group its material.
# * The extraction REQUEST is publish-grade + organiser at
#   its HTTP door (it spends the group's AI budget and
#   commissions judgement over people's words), so the
#   operation states the same two gates as events.cancel
#   does: the credential mapping for the scope, the role
#   law here. The run itself stays the worker's: this
#   operation QUEUES, exactly as the door does (202 there;
#   the queued job's id here).
# * DELETION is not in this family, deliberately: a source's
#   delete/restore lives with the tombstone law (B-080) at
#   the human doors for now, and no agent tool should be
#   the first surface to carry an erasure.


def _source_data(db: Session, source: Source) -> dict:
    """Serialise one source row for the bus, with its chunk
    count — the count a list view needs without opening a
    single sealed chunk."""
    return {
        "source_id": source.id,
        "group_id": source.group_id,
        "kind": source.kind,
        "title": source.title,
        "status": source.status,
        "stage": source.stage,
        "progress": source.progress,
        "granted_by": source.granted_by,
        "granted_scope": source.granted_scope,
        "url": source.url,
        "chunk_count": source_service.chunk_count(db, source),
    }


def _sources_list(db: Session, payload: dict, ctx: CallContext) -> dict:
    """List the group's sources (metadata only — no chunk is
    opened to build a list)."""
    _need(payload, "group_id")
    group = _group(db, payload["group_id"])
    sources = source_service.list_sources(db, group)
    return {
        "group_id": group.id,
        "sources": [_source_data(db, source) for source in sources],
        "count": len(sources),
    }


def _sources_get(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Read one source in full: its metadata plus its chunks
    in position order, opened through the service's funnel —
    text and sender as the group may read them."""
    _need(payload, "group_id", "source_id")
    group = _group(db, payload["group_id"])
    source = source_service.get_source(db, group, payload["source_id"])
    data = _source_data(db, source)
    data["chunks"] = [
        {
            "position": chunk.position,
            "text": chunk.text,
            "sender": chunk.sender,
            "sent_at": chunk.sent_at.isoformat() if chunk.sent_at else None,
        }
        for chunk in source_service.list_chunks(db, source)
    ]
    return data


def _sources_create(db: Session, payload: dict, ctx: CallContext) -> dict:
    """File one new source with its chunks, in order. The
    permission record is the payload's: granted_by is the
    actor label, granted_scope travels verbatim (including
    empty — an unscoped grant is a stated fact, not an
    oversight this operation papers over)."""
    _need(payload, "group_id", "actor", "kind")
    group = _group(db, payload["group_id"])
    source = source_service.create_source(
        db,
        actor_label=payload["actor"],
        group=group,
        kind=payload["kind"],
        title=payload.get("title") or "",
        granted_scope=payload.get("granted_scope") or "",
        chunks=payload.get("chunks") or [],
    )
    return _source_data(db, source)


def _sources_append(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Append chunks to an existing source (positions are
    the service's to assign, continuing its order). The
    service deliberately does not commit — its HTTP callers
    own sync transactions — so this operation, whose whole
    act the append is, commits it."""
    _need(payload, "group_id", "source_id", "chunks")
    group = _group(db, payload["group_id"])
    source = source_service.get_source(db, group, payload["source_id"])
    source_service.append_chunks(db, source=source, chunks=payload["chunks"])
    db.commit()
    return _source_data(db, source)


def _extraction_request(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Ask for an extraction run over one source: the role
    law (organiser and above) is stated here as at the door;
    the act queues the durable job through the extraction
    service (which submits it over ai.submit_job and stamps
    the job's id into its payload), and returns the job's
    id — the run itself is the worker's, never this call's."""
    _need(payload, "group_id", "source_id", "actor", "role")
    if not role_at_least(payload["role"], "organiser"):
        raise AppError(
            "forbidden",
            detail="Only an organiser or above may request an extraction run.",
        )
    group = _group(db, payload["group_id"])
    job = extraction_service.request_extraction(
        db,
        ctx.settings,
        actor_label=payload["actor"],
        group=group,
        source_id=payload["source_id"],
        kinds=payload.get("kinds"),
    )
    return {
        "job_id": job.id,
        "source_id": payload["source_id"],
        "kinds": payload.get("kinds") or extraction_service.ALL_KINDS,
    }


# --- planning family (B-211) -------------------------------------------------------
#
# The fifth domain family: what approval MINTED — the
# group's tasks, its decisions log, and its open questions.
# B-208's queue decides; this family is where the decided
# things live and keep changing.
#
# * The reads are the planning service's own lists (tasks
#   soonest-due first, decisions newest first, questions
#   oldest open first) plus the decision CHAIN — the
#   supersede walk, which an agent answering "what did we
#   decide about X?" needs whole: the current text alone
#   would erase that it replaced something, and when.
# * The writes are DRAFT-grade, as at their doors: planning
#   upkeep (retitle a task, claim it, complete it, resolve
#   a question) is the group's ordinary member work. The
#   consequential edge is resolve-with-decision: resolving
#   a question WITH decision text mints a Decision in the
#   same transaction — settled truth, not a note — and the
#   operation delegates to the service act that does
#   exactly that, so the mint's law is the service's.
# * A task's suggested owner is EVIDENCE, never an
#   assignment (the model's own law): the serialiser keeps
#   suggested_owner_label visibly separate from owner_id,
#   and update passes changes through the service, which
#   states the same distinction at its door.
# * Creation of tasks/decisions/questions directly is NOT
#   here: direct entry exists at the HTTP doors, but over
#   this surface new planning items are born the agent way
#   — drafted as candidates (B-204) and approved (B-208) —
#   so everything an agent originates carries provenance.


def _task_data(task: Task) -> dict:
    """Serialise one task row for the bus: the work, its
    state, its owner (id + the label captured at assignment)
    and — separately, as evidence — its suggested owner."""
    return {
        "task_id": task.id,
        "group_id": task.group_id,
        "event_id": task.event_id,
        "title": task.title,
        "notes": task.notes,
        "status": task.status,
        "owner_id": task.owner_id,
        "owner_label": task.owner_label,
        "suggested_owner_label": task.suggested_owner_label,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "candidate_id": task.candidate_id,
        "created_by": task.created_by,
    }


def _decision_data(decision: Decision) -> dict:
    """Serialise one decision row for the bus, with its
    place in the supersede chain stated by id both ways."""
    return {
        "decision_id": decision.id,
        "group_id": decision.group_id,
        "event_id": decision.event_id,
        "text": decision.text,
        "status": decision.status,
        "supersedes_id": decision.supersedes_id,
        "superseded_by_id": decision.superseded_by_id,
        "candidate_id": decision.candidate_id,
        "created_by": decision.created_by,
    }


def _question_data(question: OpenQuestion) -> dict:
    """Serialise one open-question row for the bus: its
    words, who it is asked of, and — once settled — who
    settled it and which decision the answer became."""
    return {
        "question_id": question.id,
        "group_id": question.group_id,
        "event_id": question.event_id,
        "text": question.text,
        "status": question.status,
        "asked_of_id": question.asked_of_id,
        "asked_of_label": question.asked_of_label,
        "resolution_decision_id": question.resolution_decision_id,
        "resolved_by": question.resolved_by,
        "created_by": question.created_by,
    }


def _planning_list_tasks(db: Session, payload: dict, ctx: CallContext) -> dict:
    """List the group's tasks (the service's soonest-due
    order), optionally narrowed by status or event."""
    _need(payload, "group_id")
    group = _group(db, payload["group_id"])
    tasks = planning_service.list_tasks(
        db, group, status=payload.get("status"), event_id=payload.get("event_id")
    )
    return {
        "group_id": group.id,
        "tasks": [_task_data(task) for task in tasks],
        "count": len(tasks),
    }


def _planning_list_decisions(db: Session, payload: dict, ctx: CallContext) -> dict:
    """List the decisions log, newest first; ``current_only``
    asks for the settled-truth view (superseded rows left
    out, the chain still walkable via decision_chain)."""
    _need(payload, "group_id")
    group = _group(db, payload["group_id"])
    decisions = planning_service.list_decisions(
        db, group, current_only=bool(payload.get("current_only"))
    )
    return {
        "group_id": group.id,
        "decisions": [_decision_data(row) for row in decisions],
        "count": len(decisions),
    }


def _planning_decision_chain(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Walk one decision's whole supersede chain, oldest
    first: what was first decided, what replaced it, and
    what stands now — the honest answer to 'what did we
    decide?', which is a history, not a sentence."""
    _need(payload, "group_id", "decision_id")
    group = _group(db, payload["group_id"])
    chain = planning_service.decision_chain(db, group, payload["decision_id"])
    return {
        "group_id": group.id,
        "chain": [_decision_data(row) for row in chain],
        "count": len(chain),
    }


def _planning_list_questions(db: Session, payload: dict, ctx: CallContext) -> dict:
    """List the group's questions (the service's oldest-open
    order), optionally narrowed by status."""
    _need(payload, "group_id")
    group = _group(db, payload["group_id"])
    questions = planning_service.list_questions(
        db, group, status=payload.get("status")
    )
    return {
        "group_id": group.id,
        "questions": [_question_data(row) for row in questions],
        "count": len(questions),
    }


def _planning_update_task(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Edit one task through the service's change vocabulary
    (title, notes, due moment, status, owner — an explicit
    ``owner_id: None`` unassigns, recorded like any other
    act). A ``due_at`` in changes is a payload moment and is
    converted here, at the bus's edge."""
    _need(payload, "group_id", "task_id", "actor", "changes")
    changes = dict(payload["changes"])
    if isinstance(changes.get("due_at"), str):
        changes["due_at"] = _moment(changes["due_at"])
    group = _group(db, payload["group_id"])
    task = planning_service.update_task(
        db,
        actor_label=payload["actor"],
        group=group,
        task_id=payload["task_id"],
        changes=changes,
    )
    return _task_data(task)


def _planning_resolve_question(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Resolve one open question. With ``decision_text`` the
    service mints the answer as a Decision in the same
    transaction and links it — the returned row names it in
    resolution_decision_id, so the caller can tell a
    settled-with-truth resolution from a plain closing."""
    _need(payload, "group_id", "question_id", "actor")
    group = _group(db, payload["group_id"])
    question = planning_service.resolve_question(
        db,
        actor_label=payload["actor"],
        group=group,
        question_id=payload["question_id"],
        decision_text=payload.get("decision_text"),
    )
    return _question_data(question)


# --- elicitation degradation (B-216) ---------------------------------------------
#
# The fallback half of the MCP elicitation bridge: when a
# client cannot be asked (no elicitation capability, or the
# human declined or cancelled), the agent's question must not
# evaporate. This operation files it as one of the group's
# OPEN questions (B-086) — the planning service's own create
# door, so the row carries the same shape and audit as a
# question a person asked — with the asking agent's identity
# (the payload's ``actor``) as its author of record, and then
# routes it to the organiser inbox: one notification per
# active organiser-and-above PERSON member, naming the agent
# and the question. Seats are not notified (an inbox belongs
# to a person, B-212's law); a group with no organiser person
# still keeps the question — the queue is the durable home,
# the inbox is the routing, in that order.
#
# The fact-kind vocabulary is closed HERE as at the MCP
# module's door (the two doors state the same set in their
# own words: ``place``, ``time``, ``yes_no``, ``free_text``)
# because this operation is reachable without the MCP module
# in between. And the operation's answer carries the slice's
# law as data: ``approval`` is False, always — filing or
# answering this question approves nothing, ever; a linked
# candidate (the draft the fact would inform) is checked to
# be still pending and is then left exactly as it was.

# The closed fact-kind vocabulary, stated at this door too
# (see the family comment above): the kinds an agent may be
# missing, each with an answer shape a human can fill in.
_ELICITATION_FACT_KINDS = ("place", "time", "yes_no", "free_text")


def _elicitation_ask(db: Session, payload: dict, ctx: CallContext) -> dict:
    """File one agent question as an open question (B-216).

    Creates the OpenQuestion through the planning service
    (which commits, as its direct form does — the bridge's
    own commit afterwards is the documented no-op second
    commit), then writes the organiser notifications inside
    this call's transaction. A named candidate must exist in
    this group and still be pending: informing a draft that
    has already been decided is a question with no home, and
    it is refused as a conflict rather than filed loose.
    """
    _need(payload, "group_id", "actor", "question", "fact_kind")
    # The vocabulary door: an invented kind is refused before
    # any row exists, in the same words at either door.
    if payload["fact_kind"] not in _ELICITATION_FACT_KINDS:
        raise AppError(
            "validation_error",
            detail=(
                f"Unknown fact kind {payload['fact_kind']!r}: an elicitation "
                f"can ask about one of {', '.join(_ELICITATION_FACT_KINDS)}."
            ),
        )
    group = _group(db, payload["group_id"])
    # The context check: a candidate named as the question's
    # reason must be this group's and still undecided. The
    # candidate is READ here and never written — informing is
    # not deciding, and this operation holds no approval act.
    candidate = None
    if payload.get("candidate_id") is not None:
        candidate = candidate_service.get_candidate(
            db, group, payload["candidate_id"]
        )
        if candidate.status != "pending":
            raise AppError(
                "conflict",
                detail=(
                    "That draft has already been decided, so a missing "
                    "fact can no longer inform it."
                ),
            )
    # The durable half: the question itself, asked by the
    # agent (its label is the author of record), optionally
    # tied to the event it belongs to. The service validates
    # the words and the event's visibility at organiser
    # sight — the routing role, since organisers receive it.
    question = planning_service.create_question(
        db,
        actor_label=payload["actor"],
        group=group,
        viewer_role="organiser",
        text=str(payload["question"]),
        event_id=payload.get("event_id"),
    )
    # The routing half: one inbox notification per active
    # organiser-and-above person. The kind's first segment
    # ("elicitation") is its preference category — a category
    # with no switches set defaults to on, as every kind's
    # does, so the routing reaches organisers by default.
    organisers = (
        db.query(Membership)
        .filter(
            Membership.group_id == group.id,
            Membership.status == "active",
        )
        .all()
    )
    notified = 0
    for membership in organisers:
        # Role is a ladder, not a label match: owner, admin,
        # and organiser all receive the group's questions.
        if not role_at_least(membership.role, "organiser"):
            continue
        user = db.get(User, membership.user_id)
        if user is None:
            continue
        row = notify_service.create_notification(
            db,
            group=group,
            recipient_user=user,
            recipient_label=f"user:{user.id}",
            kind="elicitation.question",
            subject=f"A question from {payload['actor']}",
            body_text=(
                f"{payload['actor']} is missing a fact "
                f"({payload['fact_kind']}) and asks: {question.text}\n\n"
                "An answer can inform a draft; it can never be an approval."
            ),
            event_id=payload.get("event_id"),
        )
        if row is not None:
            notified += 1
    # The answer's shape states the law as data: which road
    # was taken (the filed question IS the degradation), who
    # was told, and — explicitly — that nothing was approved.
    result = _question_data(question)
    result.update(
        {
            "fact_kind": payload["fact_kind"],
            "candidate_id": candidate.id if candidate is not None else None,
            "notified_organisers": notified,
            "degraded": True,
            "approval": False,
        }
    )
    return result


# --- sampling authorisation + record (B-217) --------------------------------------
#
# The backend half of the MCP sampling policy. Sampling
# itself happens on the CONNECTED CLIENT's model — no
# handler in this process can perform it — so this
# operation is the half that belongs here, run BEFORE the
# server asks the client, in the ask's own transaction:
#
# * the BUDGET gates, both read from B-047's ledger (the
#   AiUsage table): a group whose daily AI budget is already
#   spent (usage.budget_state says paused) may not commission
#   more model work through anyone's model; and a group may
#   make at most ``max_calls_per_day`` sampling calls per UTC
#   day, counted on the ledger's own sampling rows.
# * the RECORD of the call: one AiUsage row (operation
#   ``mcp_sampling`` — the client's model bills the client,
#   so the server-side cost and token counts are honestly
#   zero; the row exists so the day cap above can be counted
#   and the call is visible in the group's spend history)
#   and one hash-chained audit row (action ``mcp.sampling``)
#   naming the actor, the prompt's LENGTH, and the token
#   ceiling — never the prompt's words, exactly as B-204's
#   logging law keeps payloads out of every record.
#
# Because the record is written before the ask, a sampling
# attempt is on the group's record even when the client's
# model then refuses or the transport fails — "audit-logged
# per call" means per call ATTEMPTED under the policy, and a
# failed attempt that left no trace would be the one worth
# hiding. The operation's answer states today's sampling
# count and the group's budget state, so the tool can tell
# the agent exactly where the group stands.


def _sampling_sample(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Authorise and record one MCP sampling call (B-217).

    Budget gates first (a refused call writes nothing — the
    record is of authorised calls, and a refusal's own trace
    is the B-165 dispatch audit every internal call leaves),
    then the ledger row and the audit row land together in
    this call's transaction, before the client is asked.
    """
    _need(payload, "group_id", "actor", "prompt_chars", "max_tokens")
    group = _group(db, payload["group_id"])
    # The day cap arrives in the payload from the MCP
    # policy module (the settings live in that process);
    # the default matches the setting's own default, so a
    # caller that omits it still meets a cap, never none.
    max_calls = int(payload.get("max_calls_per_day", 20))
    # Gate one: the group's daily AI budget, B-047's own
    # state — spent is spent, on whichever model the work
    # would run, and sampling is model work.
    state = usage_service.budget_state(db, group)
    if state["paused"]:
        raise AppError(
            "sampling_budget_exceeded",
            detail=(
                "This group has spent its daily AI budget, so no "
                "further model work — including sampling — may be "
                "commissioned today."
            ),
        )
    # Gate two: today's sampling calls, counted on the
    # ledger's sampling rows through the usage module's own
    # counter — one table, one definition of "today".
    todays = usage_service.count_operation_today(db, group.id, "mcp_sampling")
    if todays >= max_calls:
        raise AppError(
            "sampling_budget_exceeded",
            detail=(
                f"This group has made its {max_calls} sampling "
                "calls for today; the cap resets at UTC midnight."
            ),
        )
    # The record: the ledger row first (the count above will
    # include this call for the next caller), then the audit
    # row — both inside this transaction, neither committing
    # here; the bridge owns the commit, as at every door.
    usage_service.record_usage(
        db,
        result=ModelResult(
            text="",
            model_id="client-sampled",
            tier="spark",
            input_tokens=0,
            output_tokens=0,
            cost_micros=0,
        ),
        group_id=group.id,
        operation="mcp_sampling",
    )
    audit_service.record(
        db,
        actor=str(payload["actor"]),
        action="mcp.sampling",
        group_id=group.id,
        entity_type="group",
        entity_id=group.id,
        detail={
            "prompt_chars": int(payload["prompt_chars"]),
            "max_tokens": int(payload["max_tokens"]),
            "calls_today": todays + 1,
        },
    )
    # The answer: what was recorded and where the group
    # stands — counts and state, no prompt words, as ever.
    return {
        "group_id": group.id,
        "actor": str(payload["actor"]),
        "calls_today": todays + 1,
        "max_calls_per_day": max_calls,
        "budget_paused": False,
        "spent_today_micros": state["spent_today_micros"],
        "recorded": True,
    }


# --- notifications / digests / announcements family (B-212) -------------------------
#
# The sixth domain family: how the group hears from itself.
# Three different person-laws meet here, and the operations
# are shaped by them:
#
# * ANNOUNCING is a PERSON's act (the B-043 law, enforced in
#   the announcements service itself): a seat may draft for
#   the queue but may never announce. The create operation
#   therefore passes the caller's seat-ness THROUGH to the
#   service, which refuses a seat in its own words — this
#   surface adds no second, weaker rule, and an agent
#   holding a person's publish credential may announce in
#   that person's name, on the record, exactly as the
#   HTTP door (organiser + publish) allows the person.
# * INBOXES and PREFERENCES belong to a person, never to a
#   seat (the notifications doors state it outright), so
#   those operations name a USER — there is no seat form of
#   them to offer, and the payload has no seat_id to give.
# * The digest PREVIEW composes and stores an UNSENT
#   digest (compose only). Running one — composing AND
#   delivering to every member — stays at the human door
#   in this slice: delivery is the consequential half, and
#   an agent surface should earn the preview before it
#   earns the send. The organiser role law rides along
#   anyway, as at the run door.


def _announcement_data(row: Announcement) -> dict:
    """Serialise one announcement for the bus: the board's
    words, its pin, its expiry, and its author of record."""
    return {
        "announcement_id": row.id,
        "group_id": row.group_id,
        "subgroup_id": row.subgroup_id,
        "title": row.title,
        "body": row.body,
        "pinned": row.pinned,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "expired": announcement_service.is_expired(row),
        "created_by": row.created_by,
        "author_user_id": row.author_user_id,
    }


def _announcements_list(db: Session, payload: dict, ctx: CallContext) -> dict:
    """The board as the service orders it (pinned first,
    then newest). Expired posts are honoured only for an
    organiser role — the door's own rule, stated here."""
    _need(payload, "group_id", "role")
    group = _group(db, payload["group_id"])
    include_expired = bool(payload.get("include_expired")) and role_at_least(
        payload["role"], "organiser"
    )
    rows = announcement_service.list_announcements(
        db, group, visible_ids=None, include_expired=include_expired
    )
    return {
        "group_id": group.id,
        "announcements": [_announcement_data(row) for row in rows],
        "count": len(rows),
    }


def _announcements_create(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Post to the board. Two laws, each in its own place:
    the ROLE law (organiser and above) is the door's,
    stated here; the PERSON law (never a seat) is the
    service's, passed through untouched — a seat caller
    meets the service's own refusal, not a paraphrase."""
    _need(payload, "group_id", "actor", "role", "title", "body")
    if not payload.get("seat_id") and not payload.get("user_id"):
        raise AppError(
            "internal_bad_payload",
            detail="announcements.create names a seat_id or a user_id: an announcement has an author.",
        )
    if not role_at_least(payload["role"], "organiser"):
        raise AppError(
            "forbidden",
            detail="Only an organiser or above may announce.",
        )
    group = _group(db, payload["group_id"])
    row = announcement_service.create_announcement(
        db,
        actor_label=payload["actor"],
        actor_user_id=payload.get("user_id"),
        actor_is_seat=bool(payload.get("seat_id")),
        group=group,
        title=payload["title"],
        body=payload["body"],
        pinned=bool(payload.get("pinned")),
        expires_at=(
            _utc_naive(payload["expires_at"]) if payload.get("expires_at") else None
        ),
    )
    return _announcement_data(row)


def _person(db: Session, user_id: str) -> User:
    """Fetch the payload's person, or refuse a dangling id
    (inboxes and preferences belong to people; a missing
    person is a not_found, never an empty inbox)."""
    user = db.get(User, user_id)
    if user is None:
        raise AppError("not_found", detail="That person could not be found.")
    return user


def _notification_data(row: Notification) -> dict:
    """Serialise one inbox row for the bus: what was sent,
    to whom, its kind and state, and whether it is read —
    the words an agent triaging a person's inbox works
    from (bodies ride along; the inbox IS the record)."""
    return {
        "notification_id": row.id,
        "group_id": row.group_id,
        "event_id": row.event_id,
        "kind": row.kind,
        "subject": row.subject,
        "body_text": row.body_text,
        "status": row.status,
        "urgent": row.urgent,
        "read_at": row.read_at.isoformat() if row.read_at else None,
    }


def _notifications_inbox(db: Session, payload: dict, ctx: CallContext) -> dict:
    """One person's inbox across their groups (the service's
    own read: the same rows the email channel sends from),
    with the unread count stated, not derived by the caller."""
    _need(payload, "user_id")
    user = _person(db, payload["user_id"])
    rows = notify_service.inbox(
        db, user=user, unread_only=bool(payload.get("unread_only"))
    )
    return {
        "user_id": user.id,
        "items": [_notification_data(row) for row in rows],
        "unread": sum(1 for row in rows if row.read_at is None),
    }


def _notifications_preferences(db: Session, payload: dict, ctx: CallContext) -> dict:
    """One person's effective notification policy in one
    group: every channel × category switch with defaults
    filled in, plus their quiet hours — the service's view,
    returned as the service states it."""
    _need(payload, "group_id", "user_id")
    group = _group(db, payload["group_id"])
    user = _person(db, payload["user_id"])
    return notify_service.preferences_view(db, user=user, group=group)


def _digest_data(row: Digest) -> dict:
    """Serialise one stored digest for the bus: its week,
    its subject and text, and whether it was ever sent. The
    HTML rendering stays at the surfaces that render; the
    bus carries the words."""
    return {
        "digest_id": row.id,
        "group_id": row.group_id,
        "week_start": row.week_start.isoformat() if row.week_start else None,
        "week_end": row.week_end.isoformat() if row.week_end else None,
        "subject": row.subject,
        "body_text": row.body_text,
        "created_by": row.created_by,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


def _digests_list(db: Session, payload: dict, ctx: CallContext) -> dict:
    """The group's stored digests, newest first — composed
    previews and sent runs alike, told apart by sent_at."""
    _need(payload, "group_id")
    group = _group(db, payload["group_id"])
    rows = (
        db.query(Digest)
        .filter(Digest.group_id == group.id)
        .order_by(Digest.created_at.desc(), Digest.id.desc())
        .all()
    )
    return {
        "group_id": group.id,
        "digests": [_digest_data(row) for row in rows],
        "count": len(rows),
    }


def _digests_preview(db: Session, payload: dict, ctx: CallContext) -> dict:
    """Compose this week's digest and store it UNSENT (the
    digest service's compose step only — delivery is not in
    this operation). The organiser role law rides from the
    run door: the preview renders the group's whole week,
    and that is organiser-grade sight."""
    _need(payload, "group_id", "role")
    if not role_at_least(payload["role"], "organiser"):
        raise AppError(
            "forbidden",
            detail="Only an organiser or above may compose the digest.",
        )
    group = _group(db, payload["group_id"])
    digest = digest_service.compose_digest(db, group=group)
    db.commit()
    return _digest_data(digest)


def register_all() -> None:
    """Register every operation (idempotent). Called lazily by
    the client's first call and eagerly by composition roots."""
    registry.register(
        Operation(
            "candidates.create",
            "extraction",
            "candidates:write",
            "Draft one candidate into a group's review queue.",
        ),
        _candidates_create,
    )
    registry.register(
        Operation(
            "events.publish",
            "events",
            "events:publish",
            "Publish one draft event.",
        ),
        _events_publish,
    )
    registry.register(
        Operation(
            "notify.request",
            "notifications",
            "notify:write",
            "Write notification rows for an audience; never sends.",
        ),
        _notify_request,
    )
    registry.register(
        Operation(
            "notify.rsvp_promotion",
            "notifications",
            "notify:write",
            "Notify a waitlisted member promoted to a place.",
        ),
        _notify_rsvp_promotion,
    )
    registry.register(
        Operation(
            "jobs.enqueue",
            "jobs",
            "jobs:write",
            "Commit one queued background job.",
        ),
        _enqueue,
    )
    registry.register(
        Operation(
            "ai.submit_job",
            "jobs",
            "ai:submit",
            "Submit one AI job (extraction, vision, transcription).",
        ),
        _ai_submit_job,
    )
    registry.register(
        Operation(
            "integrations.emit",
            "integrations",
            "integrations:write",
            "Announce one act to the group's subscribed endpoints.",
        ),
        _integrations_emit,
    )
    registry.register(
        Operation(
            "groups.get",
            "groups",
            "groups:read",
            "Read one group's plain facts by id.",
        ),
        _groups_get,
    )
    registry.register(
        Operation(
            "groups.list_members",
            "groups",
            "groups:read",
            "List a group's full roster: person memberships and seats.",
        ),
        _groups_list_members,
    )
    registry.register(
        Operation(
            "groups.update",
            "groups",
            "groups:write",
            "Edit a group's plain settings (name, description, visibility, timezone, place).",
        ),
        _groups_update,
    )
    registry.register(
        Operation(
            "profile.get",
            "identity",
            "profile:read",
            "Read one principal's profile (a person's or a seat's).",
        ),
        _profile_get,
    )
    registry.register(
        Operation(
            "profile.update",
            "identity",
            "profile:write",
            "Edit one principal's profile through its owning service.",
        ),
        _profile_update,
    )
    registry.register(
        Operation(
            "candidates.list",
            "extraction",
            "candidates:read",
            "List a group's review queue with the triage filters.",
        ),
        _candidates_list,
    )
    registry.register(
        Operation(
            "candidates.get",
            "extraction",
            "candidates:read",
            "Read one candidate in full: draft, provenance, approval progress.",
        ),
        _candidates_get,
    )
    registry.register(
        Operation(
            "candidates.approve",
            "groups",
            "candidates:decide",
            "Approve one candidate through the one approval law (B-203).",
        ),
        _candidates_approve,
    )
    registry.register(
        Operation(
            "candidates.reject",
            "extraction",
            "candidates:decide",
            "Reject one candidate; the row and its draft stay.",
        ),
        _candidates_reject,
    )
    registry.register(
        Operation(
            "candidates.edit",
            "extraction",
            "candidates:decide",
            "Edit one candidate's draft as a reviewer; it stays undecided.",
        ),
        _candidates_edit,
    )
    registry.register(
        Operation(
            "events.list",
            "events",
            "events:read",
            "List a group's events as a role may see them (drafts are organiser-only).",
        ),
        _events_list,
    )
    registry.register(
        Operation(
            "events.get",
            "events",
            "events:read",
            "Read one event through the visibility bright line.",
        ),
        _events_get,
    )
    registry.register(
        Operation(
            "events.cancel",
            "events",
            "events:write",
            "Cancel one event with a reason (organiser and above).",
        ),
        _events_cancel,
    )
    registry.register(
        Operation(
            "rsvps.list",
            "events",
            "events:read",
            "List one event's RSVPs with the summary counts.",
        ),
        _rsvps_list,
    )
    registry.register(
        Operation(
            "rsvps.set",
            "events",
            "rsvps:write",
            "Set one principal's own RSVP answer to an event.",
        ),
        _rsvps_set,
    )
    registry.register(
        Operation(
            "calendar.agenda",
            "events",
            "events:read",
            "Read one page of a group's agenda over a time window.",
        ),
        _calendar_agenda,
    )
    registry.register(
        Operation(
            "sources.list",
            "extraction",
            "sources:read",
            "List a group's sources (metadata and chunk counts).",
        ),
        _sources_list,
    )
    registry.register(
        Operation(
            "sources.get",
            "extraction",
            "sources:read",
            "Read one source in full, chunks opened in order.",
        ),
        _sources_get,
    )
    registry.register(
        Operation(
            "sources.create",
            "extraction",
            "sources:write",
            "File one new source with its chunks and permission record.",
        ),
        _sources_create,
    )
    registry.register(
        Operation(
            "sources.append",
            "extraction",
            "sources:write",
            "Append chunks to an existing source, continuing its order.",
        ),
        _sources_append,
    )
    registry.register(
        Operation(
            "extraction.request",
            "extraction",
            "extraction:request",
            "Queue an extraction run over one source (organiser and above).",
        ),
        _extraction_request,
    )
    registry.register(
        Operation(
            "planning.list_tasks",
            "planning",
            "planning:read",
            "List a group's tasks, soonest-due first.",
        ),
        _planning_list_tasks,
    )
    registry.register(
        Operation(
            "planning.list_decisions",
            "planning",
            "planning:read",
            "List a group's decisions log, newest first.",
        ),
        _planning_list_decisions,
    )
    registry.register(
        Operation(
            "planning.decision_chain",
            "planning",
            "planning:read",
            "Walk one decision's whole supersede chain, oldest first.",
        ),
        _planning_decision_chain,
    )
    registry.register(
        Operation(
            "planning.list_questions",
            "planning",
            "planning:read",
            "List a group's open questions, oldest open first.",
        ),
        _planning_list_questions,
    )
    registry.register(
        Operation(
            "planning.update_task",
            "planning",
            "planning:write",
            "Edit one task (title, notes, due moment, status, owner).",
        ),
        _planning_update_task,
    )
    registry.register(
        Operation(
            "planning.resolve_question",
            "planning",
            "planning:write",
            "Resolve one open question, optionally minting its answer as a decision.",
        ),
        _planning_resolve_question,
    )
    registry.register(
        Operation(
            "elicitation.ask",
            "planning",
            "planning:write",
            "File one agent question as an open question and route it to the organiser inbox (never an approval).",
        ),
        _elicitation_ask,
    )
    registry.register(
        Operation(
            "sampling.sample",
            "ai",
            "sampling:request",
            "Authorise and record one MCP sampling call against the group's AI budget ledger and audit chain (B-217).",
        ),
        _sampling_sample,
    )
    registry.register(
        Operation(
            "announcements.list",
            "announcements",
            "announcements:read",
            "List a group's announcement board, pinned first.",
        ),
        _announcements_list,
    )
    registry.register(
        Operation(
            "announcements.create",
            "announcements",
            "announcements:write",
            "Post one announcement to the board (organiser person only).",
        ),
        _announcements_create,
    )
    registry.register(
        Operation(
            "notifications.inbox",
            "notifications",
            "notifications:read",
            "Read one person's notification inbox across their groups.",
        ),
        _notifications_inbox,
    )
    registry.register(
        Operation(
            "notifications.preferences",
            "notifications",
            "notifications:read",
            "Read one person's effective notification policy in a group.",
        ),
        _notifications_preferences,
    )
    registry.register(
        Operation(
            "digests.list",
            "notifications",
            "notifications:read",
            "List a group's stored digests, newest first.",
        ),
        _digests_list,
    )
    registry.register(
        Operation(
            "digests.preview",
            "notifications",
            "notifications:write",
            "Compose this week's digest and store it UNSENT (organiser and above).",
        ),
        _digests_preview,
    )
