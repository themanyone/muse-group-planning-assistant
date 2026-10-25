# Runbook — the five failures this product actually has

One page, five failures, the exact fix for each. Every entry
is reproduced from a deliberately broken box in
`tests/test_runbook.py` — if a fix stops working, a test
says so. Start every diagnosis at **`GET /health`**: the
status word is public (a load balancer needs it), and the
`checks` block — which names the failing layer before you
read a single log line — is disclosed to any signed-in
session, so bring your operator login. (Anonymous callers
get the minimal liveness answer only; the Phase 13 threat
model owns that surface.)

| # | Failure | /health says |
|---|---------|--------------|
| 1 | Mail down | `mail.transport` is fine (a fact, not a probe) — the symptom lives in the failures list, below |
| 2 | AI quota spent | nothing — the candidates queue says `paused: budget` |
| 3 | Disk full | `checks.storage.status: "error"`, status `degraded` |
| 4 | Token expired | nothing — users report 401s |
| 5 | Job stuck | `checks.jobs.stuck_running ≥ 1`, status `degraded` |

## 1. Mail down

**Symptom.** Invites and digests stop arriving. In a group,
open **Notifications → failures** (or
`GET /api/v1/groups/{id}/notification-failures`): rows sit
at `failed` with a `last_error` like *mail server
unreachable*. Nothing is lost — the user flows never depended
on the mail server being up.

**Fix.** Correct the mail settings in the env file
(`GPA_MAIL_TRANSPORT=smtp`, `GPA_MAIL_SMTP_HOST/PORT/
USERNAME/PASSWORD`) and restart the worker
(`systemctl restart gpa-worker`). Then, per group, an
organiser resends each failed row from the failures list
(`POST /api/v1/groups/{id}/notifications/{nid}/resend`).
Rows that hit the attempt ceiling are `dead`: fix the cause
first, then resend — resending into a still-dead server just
spends the row's remaining attempts.

## 2. AI quota spent

**Symptom.** New pastes and flyers stop becoming candidates.
`GET /api/v1/groups/{id}/candidates/status` answers
`paused: true, pause_reason: "budget"` with today's spend
against the group's cap. Nothing failed; the gate held.
Extraction runs defer to the next UTC day on their own.

**Fix.** Raise the group's daily cap
(`PATCH /api/v1/groups/{id}` with `ai_daily_budget_micros`,
organiser or admin) if the spend is legitimate, or wait
for the UTC-midnight reset — deferred runs proceed by
themselves. If spend looks wrong, the status answer's
per-day figures and the audit trail (`extraction.*`) say
which sources cost what before you raise anything.

## 3. Disk full

**Symptom.** Uploads and backups fail; `/health` turns
`degraded` with `checks.storage.status: "error"` — the
health probe writes for a living, so it notices first.

**Fix.** Free space on the volume holding `GPA_STORAGE_DIR`
and the backups directory: delete rotated-out backup
bundles past their retention, prune the host's journal
(`journalctl --vacuum-size=200M`), and check for a runaway
log. Writes recover on their own — no restart needed; watch
`/health` return to `ok`. Then find out what filled it
before it fills again: `du -sh` the data directory and the
backups directory; bundles are dated, growth is visible.

## 4. Token expired

**Symptom.** A user (or the frontend) reports sudden 401s,
or an integration's health-board row says its connection
needs attention.

**Fix, session half.** Access tokens live 15 minutes
(`GPA_ACCESS_TOKEN_MINUTES`); a 401 with a refresh cookie
present is the normal refresh path, not an incident. A 401
for *everyone at once* means clock skew or a rotated
`GPA_SECRET_KEY` — check the host clock first (`timedatectl`),
then re-login. There is no token to "un-expire"; a fresh
login is the fix.
**Fix, integration half.** Follow the health board's
plain-words next step for the integration
(`GET /api/v1/groups/{id}/integrations/health-board`):
reconnect with a fresh grant. The sealed credential is
replaced, never repaired — by design, sealing is one-way.

## 5. Job stuck

**Symptom.** Work silently never happens — a digest unsent,
an extraction unrun — while everything looks up.
`/health` shows `checks.jobs.stuck_running ≥ 1`: a job
claimed by a worker that died mid-run. The chassis only
ever claims `queued` rows, so the orphan waits forever
until a person returns it.

**Fix.** List, look, then requeue:

```bash
uv run python scripts/requeue_stuck_jobs.py            # list only
uv run python scripts/requeue_stuck_jobs.py --apply    # return them to queued
```

The default threshold is 15 minutes untouched — the same
number `/health` reports. Read the list before applying: a
job that is merely *slow* (long vision work) also appears,
and requeueing a job whose worker is alive runs it twice.
After `--apply`, the next worker pass drains the queue.

## Reading the logs

Production logs are JSON lines (one object per line) with
`time`, `level`, `logger`, `request_id`, and `message` —
every request carries one `request_id`, echoed in its
response's problem body when it fails, so a user's report
and the log line meet.

```bash
# The last hour's errors, systemd installs:
journalctl -u gpa -u gpa-worker --since "1 hour ago" -p warning

# Everything one request did, by its id:
journalctl -u gpa -o cat | jq -r 'select(.request_id == "THE-ID") | "\(.time) \(.level) \(.message)"'

# The five failures announce themselves by logger:
journalctl -u gpa-worker -o cat | jq -r 'select(.logger | startswith("gpa.jobs")) | .message'
```

File-log postures (the logrotate snippet in `deploy/`)
produce the same JSON lines in the file; the `jq` queries
above work on the file unchanged.
