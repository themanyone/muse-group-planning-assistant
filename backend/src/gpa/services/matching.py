"""Cross-source matching (roadmap B-075, Doc 07 §6).

When a new candidate arrives, the queue asks: is this an event
the group already knows? The answer is a *score*, computed here
from the evidence both sides carry — title words, time, place,
host — and shown in the candidate's detail view as
``matches``. Above ``MATCH_THRESHOLD`` a match is flagged
``likely``: the queue shows "likely the same event" beside a
merge action. Below it, the candidate is treated as a new
event. The machine proposes; a person merges (B-076) — nothing
here ever merges, renames, or edits anything on its own.

The scoring is deliberately small and hand-rolled (D-46): token
sets for words, a normalised containment check for places
("hall" inside "Village Hall" is the same place, said shorter),
and the Phase 3 date resolver's readings for times — an
ambiguous date earns credit when the event's date is one of
its readings, because the Doc 07 triple story turns on exactly
that (the chat's "Friday week" and the newsletter's firm date
are the same Friday).

A bare title resemblance is never enough: a match needs title
evidence PLUS time or place evidence. Two different "Club
night"s in different weeks must not propose a merge — so a
title-only score is halved and can never reach the threshold.
"""

import re
from datetime import date, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from gpa.models.events import Event
from gpa.models.groups import Candidate, Group
from gpa.services import candidates as candidate_service

# At or above this score, the queue calls a match "likely the
# same event" and offers the merge action.
MATCH_THRESHOLD = 0.6

# How much each piece of evidence weighs when it is present.
WEIGHTS: dict[str, float] = {
    "title": 0.35,
    "time": 0.35,
    "place": 0.20,
    "host": 0.10,
}

_WORDS = re.compile(r"[a-z0-9]+")

# Articles carry no identity: "the hall" and "hall" are the
# same place said two ways, and dropping them is what lets the
# containment check see it.
_ARTICLES = {"the", "a", "an"}


def _tokens(text: str) -> set[str]:
    """The word-set of a string, lowercased, punctuation-free,
    articles dropped."""
    return {
        word for word in _WORDS.findall((text or "").lower())
        if word not in _ARTICLES
    }


def _normalised(text: str) -> str:
    """One string, lowercased, single-spaced, articles dropped
    — the containment form."""
    return " ".join(
        word for word in _WORDS.findall((text or "").lower())
        if word not in _ARTICLES
    )


def title_similarity(a: str, b: str) -> float | None:
    """Word overlap between two titles: half Jaccard, half
    containment (a short title wholly inside a long one is a
    strong match, not a weak one). None when either title is
    empty — no words, no evidence."""
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return None
    shared = tokens_a & tokens_b
    if not shared:
        return 0.0
    jaccard = len(shared) / len(tokens_a | tokens_b)
    containment = len(shared) / min(len(tokens_a), len(tokens_b))
    return 0.5 * jaccard + 0.5 * containment


def place_similarity(a: str, b: str) -> float | None:
    """Place agreement: identical names are 1.0, a name contained
    in the other ("hall" in "Village Hall") is 0.9 — the same
    place said shorter — otherwise word overlap. None when
    either side is empty: an unnamed place is not a disagreement,
    it is an absence."""
    norm_a, norm_b = _normalised(a), _normalised(b)
    if not norm_a or not norm_b:
        return None
    if norm_a == norm_b:
        return 1.0
    if norm_a in norm_b or norm_b in norm_a:
        return 0.9
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    shared = tokens_a & tokens_b
    if not shared:
        return 0.0
    return len(shared) / len(tokens_a | tokens_b)


def host_similarity(a: str, b: str) -> float | None:
    """Hosts either match (same normalised name) or they do not;
    an unnamed host is absence, not disagreement."""
    norm_a, norm_b = _normalised(a), _normalised(b)
    if not norm_a or not norm_b:
        return None
    return 1.0 if norm_a == norm_b else 0.0


def _draft_field_value(draft: dict, name: str):
    """A draft field's value, from the fields map (the pipeline's
    home for it) or the top-level mirror."""
    fields = draft.get("fields", {})
    entry = fields.get(name, {}) if isinstance(fields, dict) else {}
    if isinstance(entry, dict) and entry.get("value") is not None:
        return entry["value"]
    return draft.get(name)


def _draft_date_readings(draft: dict, name: str = "starts") -> list[str]:
    """The candidate's resolved date(s) for a date field: the
    single resolved date when the resolver pinned one down, else
    every reading the resolver returned (an ambiguous date still
    carries its readings — they are evidence, not a failure)."""
    fields = draft.get("fields", {})
    entry = fields.get(name, {}) if isinstance(fields, dict) else {}
    if not isinstance(entry, dict):
        return []
    resolved = entry.get("resolved_date")
    if resolved:
        return [str(resolved)]
    readings = entry.get("date_readings") or []
    return [str(reading) for reading in readings]


def time_score(draft: dict, event: Event, tz_name: str) -> float | None:
    """Time agreement between a draft and an event.

    The event's local date (its UTC instant in its own
    timezone) is compared with the draft's readings: an exact
    reading is 1.0, the event's date among ambiguous readings
    is 0.85 (the Doc 07 rule — a flagged reading still counts),
    a date one day either side is 0.4 (a near miss, not a
    match), anything else is 0.0. None when the draft carries
    no date at all — a dateless draft says nothing about time.
    """
    readings = _draft_date_readings(draft)
    if not readings:
        return None
    tz = ZoneInfo(tz_name or "UTC")
    event_date = (
        event.starts_at.replace(tzinfo=timezone.utc).astimezone(tz).date()
    )
    reading_dates: list[date] = []
    for reading in readings:
        try:
            reading_dates.append(date.fromisoformat(reading))
        except ValueError:
            continue
    if not reading_dates:
        return None
    if event_date in reading_dates:
        return 1.0 if len(reading_dates) == 1 else 0.85
    if any(abs((reading - event_date).days) <= 1 for reading in reading_dates):
        return 0.4
    return 0.0


def score_candidate_event(
    draft: dict, event: Event, tz_name: str, *, title_value: str | None = None
) -> dict:
    """Score one draft against one event: the component scores
    plus the weighted total, and whether the total reaches the
    "likely" threshold.

    ``title_value`` overrides the draft's title field — the
    change-candidate kind (B-077) names its target event in an
    ``event`` field, not a ``title`` field, and reuses this
    scorer verbatim.
    """
    title = title_value if title_value is not None else _draft_field_value(draft, "title")
    components: dict[str, float | None] = {
        "title": title_similarity(str(title or ""), event.title),
        "time": time_score(draft, event, tz_name),
        "place": place_similarity(
            str(_draft_field_value(draft, "place") or ""), event.place_name
        ),
        "host": host_similarity(
            str(_draft_field_value(draft, "host") or ""), event.host_label
        ),
    }
    present = {name: value for name, value in components.items() if value is not None}
    if not present:
        score = 0.0
    elif "title" not in present:
        score = 0.0
    elif not ({"time", "place", "host"} & set(present)):
        # A bare title resemblance is a hint, never a proposal:
        # halved, it can never reach the threshold on its own.
        score = present["title"] * 0.5
    else:
        total_weight = sum(WEIGHTS[name] for name in present)
        score = sum(WEIGHTS[name] * value for name, value in present.items()) / total_weight
    return {
        "event_id": event.id,
        "event_title": event.title,
        "event_starts_at": event.starts_at.isoformat() + "Z",
        "event_place_name": event.place_name,
        "event_host_label": event.host_label,
        "event_status": event.status,
        "score": round(score, 4),
        "likely": score >= MATCH_THRESHOLD,
        "components": {name: value for name, value in components.items()},
    }


def _matchable_events(db: Session, group: Group) -> list[Event]:
    """The events a candidate can match: the group's live ones.
    Drafts count (a direct-entry draft is a real intention) and
    published ones count; cancelled events do not — a new
    announcement is not the same event as a cancelled one, and
    matching against it would only resurrect it by accident.
    Deleted events are excluded by the tombstone filter
    (B-080, added with the deleted_at column)."""
    return (
        db.query(Event)
        .filter(
            Event.group_id == group.id,
            Event.status.in_(("draft", "published")),
            Event.deleted_at.is_(None),
        )
        .order_by(Event.starts_at, Event.id)
        .all()
    )


def match_candidate(db: Session, group: Group, candidate: Candidate) -> list[dict]:
    """Score one candidate against the group's events, best
    first. Only event-kind candidates in an open state carry
    matches — a decided candidate's story is already written,
    and other kinds are not events at all."""
    if candidate.kind != "event":
        return []
    if candidate.status not in candidate_service.OPEN_STATUSES:
        return []
    draft = candidate_service.draft_of(candidate)
    tz_name = group.home_timezone or "UTC"
    scored = [
        score_candidate_event(draft, event, tz_name)
        for event in _matchable_events(db, group)
    ]
    scored.sort(key=lambda row: (-row["score"], row["event_id"]))
    return scored


def match_by_name(
    db: Session, group: Group, name: str, draft: dict
) -> tuple[Event | None, float]:
    """The event a change names (B-077's linking).

    A change's primary key is the NAME itself: when the words
    name an event exactly (modulo case and articles), that is
    the target — a bare-title resemblance is weak evidence for
    a *new announcement*, but "the Harvest Supper is cancelled"
    is not a resemblance, it is a reference. When several
    events share the exact name, the soonest is the target (a
    change is about the next happening of that name). Without
    an exact name, the weighted score decides at the usual
    threshold. No match at all returns (None, best score) —
    an unknown target is a flagged fact, never a silent guess.
    """
    events = _matchable_events(db, group)
    wanted = _normalised(name)
    if wanted:
        exact = [event for event in events if _normalised(event.title) == wanted]
        if exact:
            return exact[0], 1.0
    tz_name = group.home_timezone or "UTC"
    best: Event | None = None
    best_score = 0.0
    for event in events:
        result = score_candidate_event(draft, event, tz_name, title_value=name)
        if result["score"] > best_score:
            best, best_score = event, result["score"]
    if best is not None and best_score >= MATCH_THRESHOLD:
        return best, best_score
    return None, best_score
