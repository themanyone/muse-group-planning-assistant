# Graceful degradation — what happens when a dependency is down

*Roadmap B-173. The machinery is `src/gpa/services/resilience.py`
(circuit breakers + bulkheads) enforced by the job chassis
(`src/gpa/services/jobs.py`); the automatic data-repair crew is
`src/gpa/services/repair.py`. This page is the contract a person
can hold the system to: for each external dependency, what the
user sees while it is down — never a stack trace, never silent
loss.*

## The rules that hold for every dependency

1. **Work queues; it does not die.** A background job whose
   dependency cannot be called is *deferred*: back to queued
   with a later run time, its attempt count untouched, and a
   plain-words note in its error field naming the dependency
   and promising the work runs when the service recovers.
   Attempts are spent only on real attempts.
2. **Five consecutive failures is an outage.** The dependency's
   circuit breaker opens and further calls are refused at once
   — the dead provider is not hammered. After a 60-second
   cooldown one probe is allowed; success closes the breaker,
   failure opens it for another cooldown.
3. **One outage cannot starve the rest.** Each dependency has a
   bulkhead (at most 4 calls in flight per process). Work that
   leans on nothing external — most of the product — is never
   gated by any breaker.
4. **A restart starts hopeful.** Breaker state is per-process
   memory; a fresh process re-probes the world instead of
   inheriting a corpse's verdict.
5. **Repairs fix forward and say what they did.** The
   `data_repair` job recomputes derived values from the rows
   that own the truth (job progress, chunk positions, flyer
   alt-text status), removes provenance rows pointing at
   things that no longer exist, and re-renders digests whose
   stored renderings are empty — and writes one audit row
   (`repair.completed`) listing every fix.

## Per dependency

| Dependency | What leans on it | While it is down, the user sees |
|---|---|---|
| **Meta model API** (`meta_ai`) | `extract`, `vision`, `transcribe` jobs | Sources sit at their honest stage (`awaiting…` / in queue); extraction and reading jobs defer and run on recovery. Nothing is extracted from a guess; no candidate is invented. Manual entry, review of existing drafts, calendar, and planning all work normally. |
| **Mail provider** (`mail`) | `notify` jobs (reminders, alerts, invites), `digest_run` | Notifications rest in the outbox as rows — visible in the inbox and the organiser failure list — and send when the provider returns. Nothing is marked sent that was not sent. The product itself (events, RSVPs, discussion) is unaffected. |
| **Facebook** (`facebook`) | Event presses (B-131), reconciliation reads (B-137) | Our events stand complete on our side; the outward copy is stale, and reconciliation reports `differs`/`missing` to organisers rather than pretending agreement. A press during an outage fails in plain words and can simply be pressed again — the press is idempotent by payload hash. |
| **Google Calendar** (`google`) | Calendar sync (B-132), inbound-edit pulls (B-159) | Same posture as Facebook: our calendar is the truth, the outside copy waits, drift is reported, never silently "healed" — a Google-side edit still arrives only as a change candidate a person approves. |

## What is NOT degraded, ever

* **Entered data.** WAL + synchronous=FULL and the
  commit-before-answer law (B-171) do not depend on any outside
  service. A dependency outage cannot lose, corrupt, or hide a
  committed entry.
* **The audit.** Every deferral reason lands on the job row;
  every repair lands in the audit stream. Degraded is a
  *visible* state, not a quiet one.
* **Security boundaries.** A down dependency never widens a
  permission, skips a signature, or opens a sealed field.
