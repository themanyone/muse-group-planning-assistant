"""Polls (roadmap B-087).

A poll asks the group a question and counts the answers.
Two kinds: ``choice`` (options are words) and ``date`` (options
carry proposed instants; the winner can mint or move an event,
on an organiser's action — never automatically).

The vote is the delicate part, and it obeys the commit-before-
answer law (carried from Samtal, roadmap §6A.5): a vote is
COMMITTED before any tally that includes it is computed.
``cast_vote`` writes and commits the vote row and returns; the
results a caller is shown are read afterwards, from committed
rows only. Under SQLAlchemy's session discipline that means
the tally query runs against the database state, never against
objects the vote's own transaction had in flight.

One member, one CURRENT vote is a database fact, not a hope:
``poll_votes`` is unique per (poll, voter), and re-voting
updates the row. Concurrent votes by the same member race to
that constraint; the loser of the insert race re-reads and
updates instead — the invariant holds either way, and the
transaction test proves it under real threads.
"""

import time as time_module

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group
from gpa.models.identity import now_utc
from gpa.models.planning import Poll, PollOption, PollVote
from gpa.services import audit
from gpa.services import events as event_service
from gpa.services import planning as planning_service

# How hard cast_vote retries a lost write race before it admits
# defeat. With WAL + busy_timeout, real contention resolves in
# one or two attempts; five is generous, and the failure stays a
# plain 409 rather than a corrupted tally.
_VOTE_ATTEMPTS = 5


def get_poll(db: Session, group: Group, poll_id: str) -> Poll:
    """One poll of this group, or 404."""
    poll = db.get(Poll, poll_id)
    if poll is None or poll.group_id != group.id:
        raise AppError("not_found", detail="That poll could not be found.")
    return poll


def get_option(db: Session, poll: Poll, option_id: str) -> PollOption:
    """One option OF THIS POLL, or 404 — an option id from
    another poll must never accept a vote here."""
    option = db.get(PollOption, option_id)
    if option is None or option.poll_id != poll.id:
        raise AppError("not_found", detail="That option could not be found on this poll.")
    return option


def list_options(db: Session, poll: Poll) -> list[PollOption]:
    """A poll's options, in the order they were offered."""
    return (
        db.query(PollOption)
        .filter(PollOption.poll_id == poll.id)
        .order_by(PollOption.position, PollOption.created_at, PollOption.id)
        .all()
    )


def create_poll(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    viewer_role: str | None,
    question: str,
    kind: str,
    options: list[dict],
    event_id: str | None = None,
    closes_at=None,
) -> Poll:
    """Create a poll with its options (B-087). ``options`` are
    dicts: ``{label, starts_at?, ends_at?}`` — date polls REQUIRE
    an instant per option (a date option without a date is a
    label, and calling it a date poll would be a lie); choice
    polls forbid them."""
    if kind not in ("choice", "date"):
        raise AppError("validation_error", detail="A poll is a 'choice' poll or a 'date' poll.")
    if not question or not question.strip():
        raise AppError("validation_error", detail="A poll needs its question.")
    if len(options) < 2:
        raise AppError("validation_error", detail="A poll needs at least two options.")
    if event_id is not None:
        planning_service.visible_event(db, group, event_id, viewer_role)
    poll = Poll(
        group_id=group.id,
        event_id=event_id,
        question=question.strip()[:240],
        kind=kind,
        status="open",
        closes_at=closes_at,
        created_by=actor_label,
    )
    db.add(poll)
    db.flush()
    for position, spec in enumerate(options):
        label = str(spec.get("label") or "").strip()
        starts_at = spec.get("starts_at")
        ends_at = spec.get("ends_at")
        if kind == "date":
            if starts_at is None:
                raise AppError(
                    "validation_error",
                    detail="Every option of a date poll needs its proposed moment.",
                )
            if not label:
                label = starts_at.strftime("%A %d %B %Y, %H:%M")
        elif not label:
            raise AppError("validation_error", detail="Every option needs its words.")
        db.add(
            PollOption(
                poll_id=poll.id, label=label[:240],
                starts_at=starts_at, ends_at=ends_at, position=position,
            )
        )
    audit.record(
        db, actor=actor_label, action="poll.created",
        group_id=group.id, entity_type="poll", entity_id=poll.id,
        detail={"kind": kind, "options": len(options)},
    )
    db.commit()
    return poll


def list_polls(
    db: Session, group: Group, *, status: str | None = None, event_id: str | None = None
) -> list[Poll]:
    """The group's polls, newest first."""
    query = db.query(Poll).filter(Poll.group_id == group.id)
    if status is not None:
        query = query.filter(Poll.status == status)
    if event_id is not None:
        query = query.filter(Poll.event_id == event_id)
    return query.order_by(Poll.created_at.desc(), Poll.id.desc()).all()


def _poll_open(poll: Poll) -> bool:
    """Voting is open: the poll is open AND its closing moment
    (when it has one) has not passed."""
    if poll.status != "open":
        return False
    return poll.closes_at is None or now_utc() < poll.closes_at


def cast_vote(
    db: Session,
    *,
    actor_label: str,
    user_id: str | None,
    seat_id: str | None,
    group: Group,
    poll_id: str,
    option_id: str,
) -> PollVote:
    """Cast (or change) the caller's vote — and COMMIT it here
    (B-087, the commit-before-answer law).

    The upsert races safely: an existing row is updated; a
    missing one is inserted under the (poll, voter) unique
    constraint; if a concurrent vote wins the insert, the retry
    loop re-reads and updates that row instead. The audit row
    and the vote commit together or not at all.
    """
    poll = get_poll(db, group, poll_id)
    option = get_option(db, poll, option_id)
    if not _poll_open(poll):
        raise AppError("conflict", detail="That poll is closed — its tally is final.")
    last_error: Exception | None = None
    for attempt in range(_VOTE_ATTEMPTS):
        try:
            vote = (
                db.query(PollVote)
                .filter(
                    PollVote.poll_id == poll.id,
                    PollVote.voter_label == actor_label,
                )
                .one_or_none()
            )
            if vote is None:
                vote = PollVote(
                    poll_id=poll.id, option_id=option.id,
                    voter_label=actor_label, user_id=user_id, seat_id=seat_id,
                )
                db.add(vote)
            else:
                vote.option_id = option.id
                vote.updated_at = now_utc()
            db.flush()
            audit.record(
                db, actor=actor_label, action="poll.voted",
                group_id=group.id, entity_type="poll", entity_id=poll.id,
                detail={"option_id": option.id},
            )
            db.commit()
            return vote
        except (IntegrityError, OperationalError) as exc:
            # A concurrent vote by the same member won the write
            # race (unique constraint / writer lock). Roll back
            # and retry as an update — the member still ends with
            # exactly one current vote.
            db.rollback()
            last_error = exc
            if attempt + 1 < _VOTE_ATTEMPTS:
                time_module.sleep(0.02 * (attempt + 1))
    raise AppError(
        "conflict",
        detail="That vote could not be recorded under contention — please try once more.",
    ) from last_error


def my_vote(
    db: Session, *, voter_label: str, poll: Poll
) -> PollVote | None:
    """The caller's current vote on a poll, if any."""
    return (
        db.query(PollVote)
        .filter(PollVote.poll_id == poll.id, PollVote.voter_label == voter_label)
        .one_or_none()
    )


def poll_results(db: Session, poll: Poll) -> dict:
    """The tally, read from committed vote rows (B-087): one
    entry per option with its count, the total, and the leading
    option ids (more than one on a tie — a tie is a fact the
    response states, not a coin it flips)."""
    options = list_options(db, poll)
    counts: dict[str, int] = {option.id: 0 for option in options}
    for (option_id,) in (
        db.query(PollVote.option_id).filter(PollVote.poll_id == poll.id).all()
    ):
        if option_id in counts:
            counts[option_id] += 1
    total = sum(counts.values())
    best = max(counts.values(), default=0)
    leaders = [option.id for option in options if counts[option.id] == best] if total else []
    return {
        "poll": poll,
        "options": [
            {"option": option, "votes": counts[option.id]} for option in options
        ],
        "total_votes": total,
        "leader_option_ids": leaders,
    }


def close_poll(
    db: Session, *, actor_label: str, group: Group, poll_id: str
) -> Poll:
    """Close a poll (organiser act, held at the router door):
    the tally freezes; later votes are refused."""
    poll = get_poll(db, group, poll_id)
    if poll.status != "open":
        raise AppError("conflict", detail="That poll is already closed.")
    poll.status = "closed"
    poll.closed_at = now_utc()
    audit.record(
        db, actor=actor_label, action="poll.closed",
        group_id=group.id, entity_type="poll", entity_id=poll.id,
    )
    db.commit()
    return poll


def apply_winner(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    poll_id: str,
    title: str | None = None,
):
    """Apply a DATE poll's winner to the calendar (B-087) — an
    organiser's act, never automatic: with an attached event,
    the event MOVES to the winning slot through the change
    engine (material change, history, needs_reannounce ride
    along); without one, a new event is MINTED at the winning
    slot through direct entry (its manual source records the
    poll as its provenance). A tie refuses in plain words: the
    group has not chosen, and the backend will not choose for
    it. Returns the affected event."""
    poll = get_poll(db, group, poll_id)
    if poll.kind != "date":
        raise AppError(
            "validation_error",
            detail="Only a date poll's winner can be applied to the calendar.",
        )
    results = poll_results(db, poll)
    if results["total_votes"] == 0:
        raise AppError("conflict", detail="Nobody has voted — there is no winner to apply.")
    if len(results["leader_option_ids"]) != 1:
        raise AppError(
            "conflict",
            detail="The vote is tied — the group has not chosen a moment yet.",
        )
    winner = get_option(db, poll, results["leader_option_ids"][0])
    assert winner.starts_at is not None  # date options always carry one
    ends_at = winner.ends_at or winner.starts_at
    if poll.event_id is not None:
        event = event_service.get_event(db, group, poll.event_id)
        if event.status == "cancelled":
            raise AppError("conflict", detail="That event is cancelled — it cannot be moved.")
        event_service.apply_changes(
            db, event=event, actor_label=actor_label,
            changes={"starts_at": winner.starts_at, "ends_at": ends_at},
            reason=f"Date poll decided: {poll.question}",
        )
        db.commit()
        poll.applied_event_id = event.id
        audit.record(
            db, actor=actor_label, action="poll.winner_applied",
            group_id=group.id, entity_type="poll", entity_id=poll.id,
            detail={"event_id": event.id, "moved": True},
        )
        db.commit()
        return event
    event = event_service.create_event(
        db,
        actor_label=actor_label,
        group=group,
        data={
            "title": (title or poll.question)[:240],
            "description": f"Chosen by the group's date poll: {poll.question}",
            "starts_at": winner.starts_at,
            "ends_at": ends_at,
            "timezone": group.home_timezone or "UTC",
            "status": "published",
        },
    )
    poll.applied_event_id = event.id
    audit.record(
        db, actor=actor_label, action="poll.winner_applied",
        group_id=group.id, entity_type="poll", entity_id=poll.id,
        detail={"event_id": event.id, "moved": False},
    )
    db.commit()
    return event
