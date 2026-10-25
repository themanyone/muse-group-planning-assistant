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
