"""Error-correction: circuit breakers + bulkheads (roadmap B-173).

Every external dependency this backend leans on can fail, and
each one fails differently: the Meta model API rate-limits and
times out, the mail provider refuses connections, Facebook and
Google revoke tokens and go dark. The failure this module
exists to prevent is not the dependency failing — it is OUR
side making it worse: a worker that keeps hammering a dead
provider, burns every job's attempts against a wall, and
dead-letters good work that only needed the provider to come
back. Worse still is the cascade: one integration's outage
filling the whole queue's attention while unrelated work
starves behind it.

Two classic defences, kept deliberately small and honest:

* **Circuit breakers, one per dependency.** After a run of
  consecutive failures the breaker OPENS: further calls are
  refused at once, in plain words, without touching the
  dependency at all. After a cooldown the breaker goes
  HALF-OPEN and lets exactly one probe through; the probe's
  outcome decides — success closes the breaker, failure opens
  it for another cooldown. The state lives in this process's
  memory: a restart starts closed, which is the right posture
  (a fresh process re-probes the world rather than inheriting
  a corpse's verdict), and the job chassis resets the registry
  whenever an app is composed, so one tenant of a process can
  never inherit another's outage.
* **Bulkheads, one per dependency.** A bounded number of
  in-flight calls per dependency per process. When a
  dependency's compartment is full, new work for THAT
  dependency waits — work for every other dependency sails
  on. One integration's failure cannot starve the worker
  because no integration may hold more than its share of it.

The job chassis (``services/jobs.py``) is the enforcement
point for background work: each job kind names its dependency
(``JOB_KIND_DEPENDENCIES``), and ``process_one`` asks this
module for a permit before running a dependency-bound job.
No permit — breaker open or bulkhead full — means the job is
DEFERRED: back to queued with a later run time, its attempts
untouched, its error field saying in plain words which
dependency is down and that the work is waiting, not lost.
That is the graceful-degradation contract the roadmap asks
for, spelled out per dependency in ``DEGRADATION.md`` at the
repo root: queued, never silently dropped, never a stack
trace in a user's face.

Direct callers (a service about to call a provider inline)
use :func:`call`, which wraps a callable in the same permit +
settle discipline. The breakers record outcomes only from
real attempts: a refused permit is not a failure, and a
bulkhead refusal never touches the breaker.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

# The external dependencies with breakers, by the names the
# degradation matrix (DEGRADATION.md) and the health board use.
DEPENDENCIES: tuple[str, ...] = ("meta_ai", "mail", "facebook", "google")

# Human names for plain-words messages: a person reading a
# deferred job's error should meet a name, not a slug.
DEPENDENCY_LABELS: dict[str, str] = {
    "meta_ai": "the Meta model API",
    "mail": "the mail provider",
    "facebook": "Facebook",
    "google": "Google Calendar",
}

# Which dependency each background job kind leans on. A kind
# absent here leans on nothing outside this process (its work
# is database-and-storage work) and is never gated — that
# asymmetry IS the bulkhead promise: unmapped work always
# sails, whatever is down. ``reconcile`` is absent on purpose:
# it reads two providers' copies through their own boundary
# clients, and its failures are per-integration records, not
# a single dependency's outage.
JOB_KIND_DEPENDENCIES: dict[str, str] = {
    "notify": "mail",
    "digest_run": "mail",
    "extract": "meta_ai",
    "vision": "meta_ai",
    "transcribe": "meta_ai",
}

# Defaults, chosen for a small team's deployment: five
# consecutive failures is an outage, not a wobble; a minute's
# cooldown before the probe; four concurrent calls per
# dependency per process is plenty beside a single-threaded
# worker loop and still a real wall for threaded callers.
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_MAX_IN_FLIGHT = 4


class DependencyUnavailable(Exception):
    """A dependency may not be called right now.

    Raised by :func:`call` when the breaker is open or the
    bulkhead is full. The message is plain words on purpose —
    it is allowed to reach a job's error field and an
    operator's log exactly as written."""


class CircuitBreaker:
    """One dependency's breaker: closed → open → half-open.

    ``permit`` is the asking half, ``settle`` the answering
    half; a caller that takes a permit MUST settle it exactly
    once. The clock is injectable (a monotonic-seconds
    callable) so the kill-each-dependency suite can walk the
    cooldown without sleeping.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state = "closed"  # closed | open | half_open
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def cooldown_remaining(self) -> float:
        """Seconds until a probe will be permitted (0 = now)."""
        with self._lock:
            if self._state != "open":
                return 0.0
            return max(0.0, self.cooldown_seconds - (self._clock() - self._opened_at))

    def permit(self) -> bool:
        """May a call go out now? Taking a half-open permit
        spends the one probe slot until it settles."""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if self._clock() - self._opened_at < self.cooldown_seconds:
                    return False
                # Cooldown served: exactly one probe may pass.
                self._state = "half_open"
                self._probe_in_flight = True
                return True
            # half_open: the probe slot is the whole door.
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def settle(self, success: bool) -> None:
        """Record one permitted call's outcome."""
        with self._lock:
            self._probe_in_flight = False
            if success:
                self._consecutive_failures = 0
                self._state = "closed"
                return
            self._consecutive_failures += 1
            if (
                self._state == "half_open"
                or self._consecutive_failures >= self.failure_threshold
            ):
                self._state = "open"
                self._opened_at = self._clock()

    def snapshot(self) -> dict:
        """The breaker's state as plain data (health/tests)."""
        with self._lock:
            return {
                "dependency": self.name,
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self.failure_threshold,
                "cooldown_seconds": self.cooldown_seconds,
            }


class Bulkhead:
    """One dependency's compartment: a bounded in-flight count.

    Non-blocking by design — a caller that finds the
    compartment full DEFERS its work (the job chassis puts
    the job back to sleep) rather than queueing threads
    behind a dying dependency.
    """

    def __init__(self, name: str, *, max_in_flight: int = DEFAULT_MAX_IN_FLIGHT) -> None:
        self.name = name
        self.max_in_flight = max_in_flight
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def acquire(self) -> bool:
        """Take a slot if one is free; never wait."""
        with self._lock:
            if self._in_flight >= self.max_in_flight:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        """Return a slot. Releasing below zero is a caller bug
        and is clamped rather than allowed to mint capacity."""
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "dependency": self.name,
                "in_flight": self._in_flight,
                "max_in_flight": self.max_in_flight,
            }


# The process-wide registry. One breaker + one bulkhead per
# named dependency, built lazily so tests may configure first.
_lock = threading.Lock()
_breakers: dict[str, CircuitBreaker] = {}
_bulkheads: dict[str, Bulkhead] = {}
_overrides: dict[str, dict] = {}


def configure(
    name: str,
    *,
    failure_threshold: int | None = None,
    cooldown_seconds: float | None = None,
    max_in_flight: int | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Set a dependency's tuning BEFORE its next build.

    Drops any live breaker/bulkhead for the name, so the next
    ``breaker_for``/``bulkhead_for`` builds with the new
    numbers. Operators tune through deployment config;
    the test suite tunes to walk cooldowns without sleeping.
    """
    with _lock:
        overrides = _overrides.setdefault(name, {})
        if failure_threshold is not None:
            overrides["failure_threshold"] = failure_threshold
        if cooldown_seconds is not None:
            overrides["cooldown_seconds"] = cooldown_seconds
        if max_in_flight is not None:
            overrides["max_in_flight"] = max_in_flight
        if clock is not None:
            overrides["clock"] = clock
        _breakers.pop(name, None)
        _bulkheads.pop(name, None)


def breaker_for(name: str) -> CircuitBreaker:
    """The process's breaker for one dependency."""
    with _lock:
        breaker = _breakers.get(name)
        if breaker is None:
            overrides = _overrides.get(name, {})
            breaker = CircuitBreaker(
                name,
                failure_threshold=overrides.get(
                    "failure_threshold", DEFAULT_FAILURE_THRESHOLD
                ),
                cooldown_seconds=overrides.get(
                    "cooldown_seconds", DEFAULT_COOLDOWN_SECONDS
                ),
                clock=overrides.get("clock", time.monotonic),
            )
            _breakers[name] = breaker
        return breaker


def bulkhead_for(name: str) -> Bulkhead:
    """The process's bulkhead for one dependency."""
    with _lock:
        bulkhead = _bulkheads.get(name)
        if bulkhead is None:
            overrides = _overrides.get(name, {})
            bulkhead = Bulkhead(
                name,
                max_in_flight=overrides.get("max_in_flight", DEFAULT_MAX_IN_FLIGHT),
            )
            _bulkheads[name] = bulkhead
        return bulkhead


def reset_all() -> None:
    """Forget every breaker, bulkhead, and tuning override.

    Called when an app is composed (``create_app``) and by
    the worker at startup: a fresh composition starts with
    closed breakers and empty compartments, so no tenant of
    a shared process inherits another tenant's outage.
    """
    with _lock:
        _breakers.clear()
        _bulkheads.clear()
        _overrides.clear()


def dependency_of_kind(kind: str) -> str | None:
    """The dependency a job kind leans on, or None."""
    return JOB_KIND_DEPENDENCIES.get(kind)


def try_begin(name: str) -> bool:
    """The job chassis' door: bulkhead first, then breaker.

    Order matters — a bulkhead refusal must not spend the
    half-open probe slot, and a breaker refusal must not hold
    a bulkhead slot hostage. On True the caller owns one
    bulkhead slot and one breaker permit and MUST call
    :func:`end` exactly once; on False it owns nothing.
    """
    bulkhead = bulkhead_for(name)
    if not bulkhead.acquire():
        return False
    if not breaker_for(name).permit():
        bulkhead.release()
        return False
    return True


def end(name: str, *, success: bool) -> None:
    """Settle a :func:`try_begin`: record the outcome on the
    breaker and return the bulkhead slot."""
    breaker_for(name).settle(success)
    bulkhead_for(name).release()


def refusal_reason(name: str) -> str:
    """Why ``name`` cannot be called right now, in plain words
    fit for a job's error field: which dependency, what state,
    and the promise that the work waits rather than dies."""
    breaker = breaker_for(name)
    bulkhead = bulkhead_for(name)
    label = DEPENDENCY_LABELS.get(name, name)
    if breaker.state in ("open", "half_open"):
        return (
            f"Deferred: {label} is not answering (circuit open after "
            f"{breaker.consecutive_failures} failed attempt(s)); this work "
            "stays queued and runs when the service recovers."
        )
    if bulkhead.in_flight >= bulkhead.max_in_flight:
        return (
            f"Deferred: too much work is already in flight with {label}; "
            "this work stays queued until a slot frees."
        )
    return f"Deferred: {label} is unavailable right now; this work stays queued."


T = TypeVar("T")


def call(name: str, fn: Callable[..., T], *args, **kwargs) -> T:
    """Run ``fn`` under ``name``'s breaker + bulkhead.

    For inline callers (a service about to touch a provider).
    Refusal raises :class:`DependencyUnavailable` BEFORE ``fn``
    runs — the dependency is never touched while its breaker
    is open. A real exception from ``fn`` is recorded on the
    breaker and re-raised untouched; a success closes the
    breaker's failure run.
    """
    if not try_begin(name):
        raise DependencyUnavailable(refusal_reason(name))
    try:
        result = fn(*args, **kwargs)
    except Exception:
        end(name, success=False)
        raise
    end(name, success=True)
    return result


def status() -> dict:
    """Every dependency's breaker + bulkhead, as plain data —
    the snapshot the health surface and the tests read."""
    return {
        name: {
            "breaker": breaker_for(name).snapshot(),
            "bulkhead": bulkhead_for(name).snapshot(),
        }
        for name in DEPENDENCIES
    }


__all__ = [
    "DEPENDENCIES",
    "DEPENDENCY_LABELS",
    "JOB_KIND_DEPENDENCIES",
    "Bulkhead",
    "CircuitBreaker",
    "DependencyUnavailable",
    "breaker_for",
    "bulkhead_for",
    "call",
    "configure",
    "dependency_of_kind",
    "end",
    "refusal_reason",
    "reset_all",
    "status",
    "try_begin",
]
