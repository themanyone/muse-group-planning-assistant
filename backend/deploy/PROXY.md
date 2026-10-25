# Reverse proxy + TLS — the public face (roadmap B-154)

Plain words first: the backend binds **127.0.0.1:8000** and
speaks plain HTTP to its own machine only. The reverse proxy
is what the world talks to: it owns the public name, the TLS
certificate, and HTTPS. Two worked examples ship beside this
guide — `Caddyfile.example` (automatic certificates; the
one-command path) and `nginx.conf.example` (manual
certificates). Both enforce the same rules; pick either.

## Set up (either proxy)

1. Install the backend first (`deploy/install.sh`,
   DEPLOYMENT.md) and confirm `curl 127.0.0.1:8000/health`
   answers `{"status": "ok", ...}` locally.
2. Point the public name's DNS at this machine.
3. Install the proxy config, replacing
   `planning.example.org` with the real name.
4. So the app sees the visitor's real address (the auth
   rate limiter counts per IP — B-021), tell uvicorn to
   trust the proxy's forwarding headers from localhost
   only: add `--proxy-headers --forwarded-allow-ips=127.0.0.1`
   to the `ExecStart` in `gpa.service`, then
   `systemctl daemon-reload && systemctl restart gpa`.
5. Reload the proxy. From the world:
   `curl https://planning.example.org/health` answers, the
   certificate is valid, and plain `http://` redirects to
   `https://`.

## Rules both examples enforce

- **HTTPS only, with HSTS.** `Strict-Transport-Security:
  max-age=31536000; includeSubDomains` on every response.
- **Body limit matched to the app.** Uploads are capped by
  `GPA_MAX_UPLOAD_BYTES` (default 10 MiB = 10485760 bytes).
  The proxy refuses a bigger body first (Caddy
  `request_body max_size 10485760`; nginx
  `client_max_body_size 10m`). If you raise the app's
  limit, raise the proxy's to the same number — a mismatch
  means uploads the app would accept die at the door.
- **The event stream is never buffered.** `/api/v1/groups/
  {id}/stream` is server-sent events; both examples give it
  an unbuffered path, or every screen feels a minute behind.

## Paths that deserve a second look

Some URLs in this product **carry credentials in the path
itself** — that is their design (a calendar app cannot send
headers; a mail provider's webhook has no login). They are
listed here so the proxy treats them knowingly:

| Path | What rides in it | Proxy care |
| --- | --- | --- |
| `GET /api/v1/calendar/feed/{token}.ics` | The feed token IS the credential | Never in an access log: Caddy example filters it to `REDACTED`; nginx example turns logging off for the path |
| `GET /api/v1/invites/{token}` | A guest's invite token | Same log care; keep out of any analytics export |
| `POST /api/v1/email-in/{token}` | A group's inbound-mail token | Same log care |
| `POST /api/v1/webhooks/mail/{provider}` | Provider's bounce events, HMAC-signed over the RAW body | Do not transform the request body on webhook paths — the signature is over the bytes exactly as sent |
| `POST /api/v1/webhooks/source/...` | Inbound source deliveries, HMAC-signed + timestamped (B-126) | Same raw-body rule; the app refuses replays itself |
| `GET /api/v1/groups/{id}/stream` | A member's live event stream (their session authorises it) | Unbuffered (above); no caching |

The app redacts these segments in its OWN access log
(B-145). The proxy's log is the second copy — the examples
keep it equally clean, and any log shipping/analytics the
operator adds later must inherit the same redaction.

## What the proxy must NOT do

- **No caching of `/api/v1/` responses.** Answers are
  per-person and per-group; a shared cache would leak one
  member's planning to another. Neither example caches.
- **No body rewriting on webhooks** (see the table).
- **No second authentication layer in front** (basic-auth
  popups): calendar clients and webhook senders cannot
  answer them, and the app's own auth is the real door.

## Test deploy — how this guide was proven

The Caddy half of this guide was followed literally in a
test deploy on the build machine (recorded in
TEST_REPORT.md's Phase 15 addendum): the real Caddy binary
(v2.11.7), the example config adapted only for a local test
name with Caddy's internal CA, a real backend behind it —
health answered over HTTPS, the HSTS header present, an
11 MiB body refused by the proxy before the app saw it, and
the access log showing `calendar/feed/REDACTED`. The nginx
half is shipped as a reviewed example: no nginx binary
exists on the build machine and its package mirrors were
unreachable, so `nginx -t` and a live nginx deploy are
recorded as **NOT RUN** there — the config is syntax-shaped
to the same rules and is validated by the operator's own
`nginx -t` at install time.
