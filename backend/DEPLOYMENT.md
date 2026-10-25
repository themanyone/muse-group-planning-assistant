# Deployment — Group Planning Assistant Backend

Plain words first: this is how a deployment is wired to the
world outside — its mail provider (B-133), its public origin,
and the checks to run before calling it live. Settings are
environment variables with the `GPA_` prefix; `.env.example`
documents every one. Secrets live in the environment only —
never in the repo, never in a release ZIP (D-25).

## Mail provider (B-133)

The notification outbox sends through ONE driver boundary
(`services/mailer.py`). Dev runs the `file` driver (messages
land as JSON files in `GPA_OUTBOX_DIR` — the file IS the
mailbox in dev). A real deployment sets:

```sh
GPA_MAIL_TRANSPORT=smtp
GPA_MAIL_SMTP_HOST=smtp.provider.example
GPA_MAIL_SMTP_PORT=587
GPA_MAIL_SMTP_USERNAME=<the provider account>
GPA_MAIL_SMTP_PASSWORD=  # the provider password — a SECRET, env only
GPA_MAIL_SMTP_USE_TLS=true
GPA_MAIL_FROM_ADDRESS=planning@your-domain.example
```

Any SMTP-speaking provider works. Startup refuses an `smtp`
configuration missing its host or From address, in plain
words — a deployment that cannot send says so at boot.

### Deliverability is a feature: SPF, DKIM, DMARC

Mail from a domain nobody vouched for lands in spam, and a
reminder in spam is a reminder that never happened. For the
domain in `GPA_MAIL_FROM_ADDRESS`, publish all three DNS
records BEFORE the first real send:

- **SPF** — a TXT record naming the provider as a sender
  for the domain, e.g.
  `v=spf1 include:_spf.provider.example ~all`
  (use the provider's own include; exactly one SPF record
  per domain).
- **DKIM** — enable signing in the provider's console; it
  issues a selector (e.g. `gpa1`) and a public key to
  publish at `gpa1._domainkey.your-domain.example`. The
  provider signs every message with the matching private
  key; receivers verify against the published key.
- **DMARC** — a TXT record at `_dmarc.your-domain.example`,
  starting in monitor mode,
  `v=DMARC1; p=none; rua=mailto:dmarc@your-domain.example`,
  and tightening to `p=quarantine` then `p=reject` once the
  reports show legitimate mail passing SPF/DKIM alignment.

### The provider's word coming back: bounces

Providers report bounces and complaints to the webhook
`POST /api/v1/webhooks/mail/{provider}`. Set
`GPA_MAIL_WEBHOOK_SECRET` and configure the provider to sign
each event's raw body as `x-gpa-mail-signature` =
HMAC-SHA256 hex under that secret; unsigned or mis-signed
events are refused and record nothing. A bounce marks its
notification failed, so the address shows on the admin
failure list instead of being retried forever (B-102).

Inbound mail (B-030's per-group addresses) arrives through
the provider's inbound webhook to
`POST /api/v1/email-in/{token}` — the token is the group's
own credential, shown once at creation.

### Pre-submission checklist (mail)

Recorded honestly, per the roadmap's B-133 acceptance —
these are run against a REAL deployment, never assumed:

- [ ] Provider account connected; SMTP credentials in env.
- [ ] SPF, DKIM, DMARC published and verified for the From
      domain (check with the provider's domain tool).
- [ ] **A real digest sent by a real run lands in a real
      inbox** (not spam) — the B-133 acceptance, checked by
      a person, recorded in the release's test report.
- [ ] Bounce webhook configured with the shared secret; a
      test bounce appears on the admin failure list.

## Public origin

`GPA_PUBLIC_BASE_URL` is the origin invite links are built
on (B-098). Empty means path-only links — right for local
dev only.

## Integrations

Facebook Events (B-131), Google Calendar (B-132), and
Samtal (B-123) connect per group under **Integrations**;
their credentials are sealed at rest (B-127's envelope, key
from `GPA_INTEGRATION_KEY` or the app secret) and never
read back through the API. Connecting a real Page or Google
account is an operator act done with the platform's own
consent flow; the tokens it issues are what the integration
config holds.

## Backup & restore (B-146)

Three scripts, all in `scripts/`:

```sh
uv run python scripts/backup.py [--out DIR]     # take a bundle
uv run python scripts/restore.py <bundle> <dir> # verify + place
uv run python scripts/restore_drill.py          # prove it all, timed
```

A **bundle** is a directory: `data.db` (copied with SQLite's
online backup API — safe against the live WAL database),
`storage/` (every stored file), `manifest.json` (sha256 +
size of everything, written at backup time), and
`RESTORE.md`. Default output: `backups/gpa-backup-<UTC
timestamp>` beside the deployment.

**Restore** verifies every manifest hash BEFORE placing
anything — a damaged bundle restores nothing and says which
file failed. It writes `<dir>/data.db` and `<dir>/storage/`;
run against them with `GPA_DATABASE_URL=sqlite:///<dir>/data.db`
and `GPA_STORAGE_DIR=<dir>/storage`.

**The drill** is the standing proof: it plants marker data
in a throwaway instance, backs it up, copies the source
tree to a clean directory (never the `.venv` — a copied
environment is a broken one), restores, builds with a fresh
`uv sync`, runs the FULL test suite in the copied tree, and
reads the marker back through the real API. Run it after
any change to backup/restore and before calling a release
done; the latest timings are recorded in TEST_REPORT.md's
Phase 13 addendum. Drill lessons baked into the script:
child processes run with `VIRTUAL_ENV`/`PYTHONPATH` scrubbed
and the main tree's venv bin off `PATH` (a leaked environment
makes the restored tree's suite run the WRONG interpreter —
the drill's first two runs died on exactly this); the build
uses `uv sync --extra dev`, the project's documented install
(a bare sync is runtime-only and the suite finds no runner);
and the contract snapshot must be current before the drill's
suite step can be green.


## PostgreSQL promotion (B-152)

SQLite is the default and the demo engine. A deployment
that outgrows one file promotes to PostgreSQL without a
code change — the same models, the same migrations:

1. Install PostgreSQL (the platform's package; this
   project's rule is native installs, never Docker) and
   create an empty database and a role that owns it.
2. Build the schema there through Alembic:
   `GPA_DATABASE_URL=postgresql+psycopg://… uv run alembic upgrade head`
   (or let the mover do it — next step does).
3. Move the data with verification:
   `uv run python scripts/migrate_to_postgres.py --from sqlite:////path/to/gpa.db --to postgresql+psycopg://…`
   The mover refuses a non-SQLite source, a non-PostgreSQL
   target, or a non-empty target; copies every table in
   foreign-key order; and verifies per-table row counts AND
   a SHA-256 content hash, printing the table-by-table
   report. A mismatch exits non-zero and says which table.
4. Point `GPA_DATABASE_URL` at Postgres and restart. The
   SQLite file stays where it was — it is the way back.

Parity is not a claim: `tests/test_postgres_parity.py`
runs the migration chain, the mover, the core flow and an
erasure against a live server, and the whole suite runs in
Postgres mode with `GPA_TEST_PG_URL` set (the gate record
is in TEST_REPORT.md's Phase 15 addendum). One honest
boundary: the backup bundle (below) is a SQLite-file
artifact; on Postgres, back up with the platform's own
dump discipline (`pg_dump`) on the same daily cadence.

## Native install (B-153)

A clean machine installs from the script alone (no
Docker, per D-24):

```bash
sudo deploy/install.sh /path/to/unpacked-release
```

It creates a dedicated `gpa` service user, copies the tree
to `/opt/gpa`, builds the venv from the lockfile with uv,
writes `/etc/gpa/gpa.env` with a generated secret at
**0640 root:gpa** (an existing env file is left untouched —
rotation is deliberate), renders the systemd units from
`deploy/*.in` (API, worker, daily backup timer), and
**enables without starting**: the operator starts it on
purpose after reading the env file. Knobs for rehearsal
and non-standard layouts are documented at the top of the
script (`GPA_PREFIX`, `GPA_CONFIG_DIR`, `GPA_SYSTEMD_DIR`,
`GPA_SERVICE_USER`, `GPA_SKIP_USER`, `GPA_SKIP_SYSTEMD`).
Units log to the journal; `deploy/gpa.logrotate` covers
the file-log override posture.

## Reverse proxy & TLS (B-154)

The API binds 127.0.0.1; the world reaches it through the
proxy. `deploy/PROXY.md` is the guide — Caddy and nginx
examples, HSTS, body limits matched to the app's own
upload cap, unbuffered SSE, and every secret-bearing path
(feed/invite/email-in tokens, signed webhooks) called out
with its rule. Follow it literally; it was written from a
test deploy that did.

## Observability (B-155)

`GET /health` is the operator's first look: its `checks`
block reports the database (and which engine), the job
queue's shape (including `stuck_running` — jobs orphaned
by a dead worker), the configured AI/mail drivers, and a
real storage write/read/delete probe. `ok` / `degraded` /
`down`; `down` answers 503 in the same body. Disclosure:
the status word is public, but the deep `checks` go only
to a signed-in caller — anonymous callers receive the
minimal liveness answer (the Phase 13 threat model owns
that surface; engine names and error details are
operational data, not a public fingerprint). Production
logs are JSON lines carrying `request_id`; **RUNBOOK.md**
(one page: mail down, AI quota, disk full, token expired,
job stuck — symptom, health signature, exact fix, plus the
log queries) is the second look. The stuck-job fix is
`scripts/requeue_stuck_jobs.py` (list first; `--apply`
returns orphans to the queue).

## Upgrades (B-156)

```bash
uv run python scripts/upgrade.py --zip releases/gpa-backend-vX.Y-DATE.zip \
    [--root /opt/gpa] [--env-file /etc/gpa/gpa.env]
```

In the roadmap's order: the new tree is staged and built
beside the installation; a backup bundle is taken and
hash-verified (nothing mutates before it exists); the new
tree's migrations run against the live database; the
staged server smokes against the real data and must answer
/health ok; then the swap — the old tree kept whole at
`<root>.previous-<stamp>`, data/backups/.env carried over,
and the serving venv rebuilt in place (a venv is
path-bound; the staged one was scaffolding). Restart the
services when the script says to. **Rollback is "restore
the backup," plainly:** swap the two directories back and
place the bundle with `scripts/restore.py` (the exact
commands print at the end of every upgrade run).
`scripts/upgrade_drill.py` is the standing proof between
two real ZIPs; its latest record is in TEST_REPORT.md.
