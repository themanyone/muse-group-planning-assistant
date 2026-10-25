# Project Memory Map — Group Planning Assistant Backend

*The living map of this project. Updated in the same session as any
architectural change (roadmap §3). Ground truth for design: the
team roadmap `GROUP_PLANNING_ASSISTANT_PYTHON_BACKEND_ROADMAP_2026-10-24.md`
in the goal files; the docs set (18 documents) travels with it.*

## What this is

The Python backend for the Group Planning Assistant (Meta Global AI
Developer Hackathon team entry; build window Oct 26 → Nov 4, 2026).
Volmarr + Yrsa build the backend; Geoffrey + Henry build the
frontend against `OPENAPI.json`. Releases travel as ZIPs on the
roadmap's train (ZIP-0 → Alpha → Beta → RC → Final).

## How to run / how to test

```sh
uv sync --extra dev     # install the exact locked environment
./run.sh                # server on http://127.0.0.1:8000  (--demo seeds first)
./test.sh               # full pytest suite + one-page report
uv run alembic upgrade head   # migration path (GPA_DATABASE_URL)
```

Verified 2026-10-24: fresh `uv sync` → `./run.sh` → `/health` ok;
Alembic up/down/up clean; **673 tests, all passing** (104/340 slices:
Phases 0–8 complete) — the count taken from one clean run against
the final Phase 8 tree (642 before Phase 8 + 31 new = 673,
collected count reconciled). ZIP-Beta cut at the Phase 8 gate
(releases/gpa-backend-v0.3-2026-10-24.zip, §8 anatomy).
Demo verified offline on the mock driver (seed + health + demo
login, no API key, no network).

## Slice status

### Phase 0 — Foundation (B-001 → B-010): COMPLETE
- B-001 repo+tooling (uv lockfile committed, run.sh/run.ps1 parity,
  protective .gitignore) · B-002 create_app + pydantic-settings
  (GPA_ prefix, prod refuses dev secret in plain words) + JSON/text
  structured logging + request-id middleware · B-003 SQLite WAL +
  synchronous=FULL + foreign_keys=ON + busy_timeout on every
  connection; Alembic wired; migration 0001_initial · B-004
  problem+json chassis + ERROR_CODES registry in errors.py · B-005
  pytest chassis + test.sh one-page report · B-006 export_openapi.py
  + generate_ts_types.py + snapshot contract test · B-007 storage
  protocol + local sha256 content-addressed driver (atomic writes,
  strict key pattern = traversal-proof) · B-008 job chassis
  (enqueue/claim-by-update/backoff/dead-letter, GET /jobs/{id},
  standalone `gpa-worker`) · B-009 idempotent seed loader (natural
  keys) · B-010 health+meta, demo seed (1 group + 1 user),
  make_release_zip.py → **ZIP-0 built** (releases/, sha256 sidecar),
  Bruno skeleton (bruno.json + health.bru + local env).

### Phase 1 — Identity, groups, permissions (B-011 → B-022): COMPLETE
- B-011 users + Argon2id (see Decisions) · B-012 magic links
  (hashed, 30-min, single-use, dev mail = file outbox in
  services/mail.py) · B-013 sessions: hand-rolled HS256 JWT access
  (15 min) + rotating refresh families, reuse-of-old revokes the
  family · B-014 API keys (hashed, prefix shown, scopes
  read/draft/publish/admin, last-used) · B-015 groups + slug
  collisions + private-group 404 rule · B-016 memberships: invite
  (creates claimable passwordless account), accept/decline, role
  changes (owner-only for owner/admin moves), last-owner rule ·
  B-017 deps.py is the only door (require_group_role /
  require_scope); verified: **no router imports any model** ·
  B-018 agent seats: seat keys, seat audit labels, candidate stub —
  seat can draft, can never approve (403 seat_cannot_approve,
  enforced in the service) · B-019 append-only audit, same-transaction
  writer, GET /groups/{id}/audit (admin+) · B-020 password reset +
  password change + email change (token to the NEW address); all
  revoke existing sessions · B-021 per-IP token-bucket on auth
  routes, 429 + Retry-After · B-022 role-matrix suite green
  (tests/test_permissions.py: 5 roles × 8 actions, parametrized).

### Phase 2 — Sources and ingest (B-023 → B-034): ✅ COMPLETE (2026-10-24)
- B-023 sources core: `Source`/`SourceChunk`/`Provenance` models
  (models/sources.py), services/sources.py, routers/sources.py —
  create/list/get/delete under `/groups/{id}/sources`, permission
  record (granted_by = caller label, granted_scope verbatim) on
  every source, chunks ordered by position, source ids never leak
  across groups. Deletion law: chunks + their provenance are
  removed; candidates are NEVER deleted — a candidate naming the
  source keeps its `source_id` and gains `source_deleted_at`
  (tombstone columns on Candidate, plain values, no cascade FK).
- B-024 paste ingest: services/paste.py — speaker-line parsers for
  WhatsApp (bracket + dash), Discord headers, Slack headers,
  Messenger blocks (bare name + trailing timestamp line), generic
  `Name:` lines; unparsed/wrapped lines join the previous message
  and leading text becomes a senderless chunk — nothing is dropped.
  Timestamps are kept raw (`timestamp_text`) and parsed to
  `sent_at` only for unambiguous numeric formats; relative words
  are Phase 3's date resolver's job. `POST /groups/{id}/sources/paste`.
- B-025 file ingest: services/files.py — raw-body upload at
  `POST /groups/{id}/sources/upload?filename=` (no multipart
  dependency); size limit `GPA_MAX_UPLOAD_BYTES` (default 10 MiB);
  virus hook (`virus_scan`: EICAR signature + executable magics);
  content sniffing decides the kind (PDF/HTML/ICS/text) — a
  disguised executable is refused whatever its extension says;
  stdlib extraction (HTML via html.parser with script/style
  skipped, PDF text layer via FlateDecode + Tj/TJ strings, ICS one
  chunk per VEVENT, text by paragraphs). Bytes go to B-007 storage,
  a `FileAsset` row and the source land in one transaction.
- B-026 newsletter ingest: services/newsletter.py — HTML email →
  one chunk per section (h1/h2/h3/hr), links inline as
  `anchor text (URL)`, images → alt text or the marked placeholder
  `[image: no description]`, script/style/head never chunked.
  `POST /groups/{id}/sources/newsletter` (raw body, like upload).
  Golden fixture tests/golden/newsletter.html: twelve events stay
  twelve ordered sections behind the greeting preamble chunk.
- B-027 images: services/images.py — magic-byte sniff (PNG/JPEG/
  GIF/WebP), Pillow EXIF transpose, ≤2400px normalise, 320px JPEG
  thumbnail, `Flyer` row (models/sources.py) whose `alt_text` starts
  as the literal "awaiting description" with `alt_text_status`
  exposed — an undescribed image is a visible defect. Source gained
  `stage`/`progress` (B-033's read model). Migration 0002.
- B-028 board photos: same route with `board=true` — ONE source,
  kind `board_photo`, status/stage `awaiting_segmentation`, exactly
  one placeholder chunk (`region_json {"placeholder": true}`); the
  `Flyer.parent_flyer_id`/`region_json` columns await Phase 5.
- B-029 link ingest: services/linkfetch.py is the ONE guarded fetch
  road — http/https only, every resolved IP must be `is_global`
  (metadata IPs + metadata hostnames blocked), redirects followed
  manually (≤5) with each hop re-validated, 2 MiB cap. Transport +
  resolver are injectable seams; tests use a fake internet and
  never touch the real one. services/links.py archives the page
  (bytes + FileAsset + chunks, final URL on `Source.url`,
  migration 0003). Refusals: 422 `fetch_blocked`, 502
  `fetch_failed`; a failed fetch archives nothing.
- B-030 email-in: services/emailin.py — per-group `InboundAddress`
  (token hashed via `hash_secret`, shown once, prefix kept);
  admin+ `POST /groups/{id}/inbound-address`; webhook-shaped
  receiver `POST /api/v1/email-in/{token}` making `email` sources
  with `granted_by = "system:email-in"` and the sender on every
  chunk. Forged token → 401 `token_invalid` + `email_in.rejected`
  audit row (group NULL) + log line. Provider wiring is Phase 12.
- B-031 voice: services/voice.py — audio magic-sniff (WAV/MP3/
  Ogg/FLAC/M4A), stored asset, `voice` source in stage
  `awaiting_transcription` with one plain-words note chunk, and a
  `transcribe` job enqueued via B-008 (id in the response; status
  via `GET /api/v1/jobs/{id}`). The handler (services/transcribe.py)
  is an honest stub — verifies the note exists, completes, and
  leaves the status awaiting; the real driver is Phase 5.
- B-032 live-chat registration: services/live.py guards the only
  door — granting principal must be a PERSON (seats refused 403),
  scope non-empty (else 422), connector kind + external id
  required. `chat_live` source, status `registered`, binding on
  `Source.connector_kind`/`external_id` (migration 0004). The
  receiver that fills it is Phase 12.
- B-033 status + stream: sources report `stage` + `progress`
  (since B-027); `Job.progress` added (migration 0005 — the
  chassis sets 100 on completion; exposed in JobResponse);
  `GET /groups/{id}/stream` (member+) serves SSE from
  services/stream.py — one `snapshot` frame, then `job.progress`
  frames — POLLED FROM THE JOB TABLE, the durable record, never
  an in-memory bus. Honest boundary: synchronous ingests
  (paste/file/newsletter/image/link) land final stage/progress in
  the create response and never pass through a job; the stream
  carries job-driven work (transcription today).
- B-034 phase gate: tests/test_ingest_corpus.py runs the whole
  corpus (chat paste + golden newsletter + 3 flyers incl. a
  sideways EXIF photo + board photo + voice note) through the API
  in one group — 7 sources, kinds/counts/states/permissions
  asserted. OPENAPI.json + frontend_types.ts regenerated
  (42 paths, 40 schemas); FRONTEND_HANDOFF.md carries the
  upload-screen notes. Phase 2 folds into ZIP-Alpha (Wed Oct 28)
  per the roadmap train — no ZIP cut at this gate.

### Phase 3 — The AI adapter and the extraction engine (B-035 → B-048): ✅ COMPLETE (2026-10-24)

- **B-035 adapter protocol** (`ai/client.py`): `MetaModelClient`
  (complete_text / complete_json / read_image / transcribe,
  keyword-only args), tiers spark/wonder, `ModelResult` (text,
  data, model_id, tokens, integer `cost_micros`). Process-wide
  registry; `build_client(settings)` factory — driver swap is
  config only. No vendor SDK imports anywhere in domain code
  (the live driver speaks httpx directly).
- **B-036 mock + fixture drivers**: mock synthesises the smallest
  schema-valid instance; fixture replays recordings keyed by
  sha256(prompt + canonical schema + media hash), missing =
  loud `FixtureMissError`, never an invented answer.
- **B-037 live driver**: POST `{GPA_AI_API_BASE}/chat/completions`,
  env-only secrets, retries with jitter on 429/5xx + transport
  errors only, a succeeded call never duplicated (parse happens
  outside the retry loop), per-call usage/cost into `ai_usage`
  (migration `0006_phase3_ai` also grew `Candidate`
  (draft_json/confidence/flags/prompt/job/merge/resulting-entity)
  and `groups.ai_daily_budget_micros`). **Stubbed-transport tests
  only — never run against a real endpoint.**
- **B-038 prompt library** (`ai/prompts/`): 9 templates + output
  JSON Schema beside each; `manifest.json` pins version + SHA-256
  per template — editing a prompt without a version bump fails
  load + tests; every candidate records its prompt name/version.
- **B-039 date resolver** (`ai/dates.py`, D-22): deterministic;
  "this weekday" passed → BOTH readings flagged; slash dates
  both readings; ordinals skip short months; unknown = matched
  False, never a guess. 268-case golden suite incl. the two
  demo lines (Wed 2026-10-21 → Sat 10-24; 2026-10-28 → Sat 10-31).
- **B-040/B-041/B-042 extraction pipeline** (`ai/extraction.py`):
  one pipeline, four kinds; chunk windows (≤40 chunks, ≤12k
  chars); per-field quote verification (unmatched quote →
  `supported: false`, no provenance, confidence excluded); dates
  resolved from the provenance chunk's sent-at in the group tz;
  one repair retry on malformed output, then ModelError; the
  12-section newsletter yields 12 candidates; coverage check
  flags unflagged shared chunks on all claimants.
- **B-043 review queue API** (`routers/candidates.py`,
  `services/candidates.py`): list (kind/status/min_confidence),
  detail with full provenance, PATCH edit (status `edited`,
  flags recomputed by the API), reject, merge (loser kept whole,
  `merged_into_id` set). Approve extended to `edited` drafts in
  services/groups.py. Security suite: no direct event-create
  route exists (404/405), member approve 403, draft-scoped key
  approve 403 insufficient_scope, seat approval still refused
  in-service (B-018). **Boundary: approval records the decision;
  `resulting_entity_*` stays NULL — Phase 4 (B-049) mints.**
- **B-044 quality flags** (`services/quality.py`): closed
  vocabulary computed server-side — low_confidence /
  missing_required / unsupported / ambiguous_date /
  unresolved_date / conflicting (cross-source, flagged on BOTH
  sides) / shared_chunk_unflagged. Clients never send flags.
- **B-045 extraction jobs** (`services/extraction.py`): `extract`
  job per source (202 + job id, organiser+); machine author
  `system:extraction`, requester on job/audit; re-runs supersede
  the same source+kind's undecided drafts only — decided rows
  are never touched; sources awaiting transcription/segmentation
  are skipped with an audit row, not failed.
- **B-046 evaluation harness** (`ai/evaluation.py` +
  `eval_corpus.json` + `eval_baseline.json`): per-field
  precision/recall over the golden corpus (dates scored on the
  RESOLVED date); corpus deliberately imperfect (place 0.50,
  cost recall 0.00) so the gate can see red; >2-point drop vs
  baseline fails the gate test; `python -m gpa.ai.evaluation`
  prints the table.
- **B-047 budget** (`services/usage.py`): per-group daily cap
  (`ai_daily_budget_micros`, set via group PATCH — settable and
  changeable, not clearable to null under the route's
  exclude_none convention), usage ledger, gate checked before
  each kind's step (per model call for single-window sources);
  at cap the run defers to next UTC midnight as a new job and
  `GET /candidates/status` reports `paused` / `"budget"` /
  spend / cap in plain words. The gate re-reads the cap fresh
  (session-expire) — a long-lived worker never paces by stale
  settings.
- **B-048 gate**: `tests/test_phase3_gate.py` — golden chat →
  candidates (quotes/confidences/flags) → approval; golden
  newsletter → exactly 12 candidates. OPENAPI.json +
  frontend_types.ts regenerated (**47 paths, 47 schemas**);
  FRONTEND_HANDOFF.md carries the review-queue notes. Phase 3
  folds into ZIP-Alpha (Wed Oct 28) — no ZIP cut at this gate.

### Phase 4 — Events, calendar, and publishing out (B-049 → B-062): ✅ COMPLETE (2026-10-24)

- **B-049 events core** (`models/events.py`, migration
  `0007_phase4_events`, `services/events.py`): Event +
  EventSourceLink + EventChange + Recurrence + CalendarFeed +
  Thread + Comment + Rsvp tables. Approval of an event candidate
  now MINTS a published event in the approval's transaction
  (services/groups.py calls `mint_from_candidate`) and fills
  `resulting_entity_*` — Phase 3's NULL boundary is lifted.
  A draft with no resolved start is refused in plain words and
  the candidate stays pending. Direct organiser entry records a
  **manual source** (new `manual` SOURCE_KIND) and links it.
  Bright line: drafts are 404 below organiser on every surface.
  Mint defaults (recorded decisions): a draft with a resolved
  date but no time becomes an all-day event; a missing end is
  +1 day (all-day) or +2 hours (timed) — a stated end always wins.
- **B-050 editing + history**: PATCH requires `If-Match`
  (ETag `"v<n>"`, `Event.version`); stale = 409 `stale_version`
  (new error code) naming the current version. Every changed
  field writes an EventChange; material fields (starts/ends/
  timezone/all_day/place) set `needs_reannounce`. Change feed
  complete + ordered. A capacity PATCH re-runs the RSVP waitlist.
- **B-051 cancel + clone**: cancel carries a reason, sets
  cancelled_at + needs_reannounce; cancelled refuses edits.
  Clone copies structure + the RRULE (exceptions reset — they
  name the old series' dates), never RSVPs/discussion/history;
  born a draft.
- **B-052 recurrence** (`services/recurrence.py`, hand-rolled,
  no deps): RFC 5545 subset (FREQ/INTERVAL/COUNT/UNTIL/BYDAY
  with ordinals/BYMONTHDAY/BYMONTH), validated at write time.
  **Wall-clock law**: expansion walks LOCAL candidates and
  converts each through the zone — the weekly-over-DST golden
  (19:00 EDT → 19:00 EST, UTC instant moves) passes at service
  AND API level. Exceptions are data on the Recurrence row
  (original_start / moved_to / cancelled). The API returns
  occurrences, never rules.
- **B-053 iCal** (`services/ical.py`, hand-rolled): serialiser
  with RFC escaping/folding, VTIMEZONE enumerated from the
  zone's REAL transitions (daily scan + bisection), per-event
  .ics (drafts 404), per-group feed — token shown once, stored
  hashed (B-014 discipline), filters on the feed row
  (statuses/place_contains/sub_group), revocation instant.
  Cancelled emitted `STATUS:CANCELLED`, never vanished; moved
  occurrences ride as RECURRENCE-ID VEVENTs, cancelled as
  EXDATE. Round-trip proof: parse the feed back, expand with
  our own engine, occurrences equal the API's (golden test).
- **B-054 links**: Google add-to-calendar URL built server-side
  (golden string test, exact parameter match) + webcal form of
  a feed URL; the organiser event detail carries `links`
  (google_calendar, ics_download).
- **B-055 agenda/month**: occurrence-expanded, `q` over
  title/place/host, type/place/sub_group filters, cursor
  pagination (cursor = last occurrence's (start, event,
  original start)); month grid is whole Monday-first weeks
  with in_month marks — the frontend does zero date maths.
- **B-056 tonight**: evening = 15:00 → 03:00 local in the GROUP
  tz; pinned (lat/lng) events first, then start time; flyer-born
  events carry flyer {thumbnail, alt_text, alt_text_status} —
  the visible-defect law rides along.
- **B-057 conflicts**: host_overlap / place_overlap /
  travel_impossible (haversine at an assumed 40 km/h) computed
  over expanded occurrences; STRICT overlap, so back-to-back
  in the same room never flags (guarded by an exact-set test).
  **Boundary: resource double-booking is NOT computed — Phase 9
  owns the resource model; the conflicts response carries a
  `resource_note` saying so.**
- **B-058 Facebook drafts**: Meta-shaped payload (name,
  start/end with true offsets, description, place, timezone) +
  copy-ready text + flyer cover suggestion. **Drafts only —
  nothing is posted; the real create is Phase 12 with a human
  pressing the button.** Draft events 404, cancelled 409.
- **B-059 projections**: `project_event` builds public/member/
  organiser levels server-side — member-only fields (access
  notes, host, cost, bring) are ABSENT from public, not
  blanked; public route `/api/v1/public/groups/{slug}/events`
  exists only for `public-listed` groups. Leak suite greps the
  public response for member-only strings.
- **B-060 discussion**: threads/comments under events
  (polymorphic parent — Phase 7 generalises). Post/edit-own/
  delete-own (tombstone); organiser hide/unhide, audited
  (`comment.hidden` test asserts the audit row). Members never
  see hidden/deleted; organisers see both, flagged. The group
  SSE stream now also emits `comment.created`, polled from the
  Comment table (no in-memory bus).
- **B-061 RSVPs**: upsert per (event, principal); capacity
  seats parties in arrival order; the waitlist is STRICT
  next-in-line — a party that doesn't fit blocks the line (no
  queue-jumping by smaller parties; guarded by test). A freed
  seat (answer change, withdrawal, or a capacity PATCH)
  promotes in order and enqueues a `notify` job per promotion
  — EXCEPT a promotion caused by your own answer change (your
  response already told you). `services/rsvps.py` registers the
  `notify` handler (audit-writing stub; real delivery is
  Phase 11's outbox). The chassis runs promotion jobs to
  `done` in tests.
- **B-062 gate**: `tests/test_phase4_gate.py` — flyer in →
  chat extracted → candidate approved → published event
  (flyer linked) → .ics parsed back to the same times →
  discussion + RSVP live → agenda + tonight show it.
  OPENAPI.json + frontend_types.ts regenerated (**72 paths,
  72 schemas**); FRONTEND_HANDOFF.md carries the calendar +
  event screen notes. Phase 4 folds into ZIP-Beta (Sat Oct 31)
  per the roadmap train — no ZIP cut at this gate.
- **Superseded Phase 3 assertions, updated with the phase**:
  `test_candidates.py` now asserts approval MINTS (the NULL
  boundary was Phase 4's to lift); the role matrix's approve
  fixture approves a task candidate (same permission door —
  a title-only event draft is now honestly unmintable); and
  `test_no_direct_event_creation_from_a_candidate` now asserts
  the property at full strength against the EXISTING create
  route — a candidate draft POSTed at it is refused (422; the
  draft shape is not an event), no event exists afterwards,
  and the candidate is untouched — where Phase 3 could only
  assert the route's absence (404/405).

### Phase 5 — Flyers, vision, and boards (B-063 → B-074): ✅ COMPLETE (2026-10-24)
- **B-063 vision reading** (`ai/vision.py` + `services/vision.py`,
  migration `0008_phase5_vision`): POST
  `/groups/{id}/sources/{source_id}/read` (organiser+ / publish
  scope) enqueues a `vision` job → the adapter reads the picture
  with the versioned `flyer_reading` prompt (v2: transcript, alt
  text, links) → the SAME event draft shape as text extraction,
  with **region provenance**: `Provenance.region_json` carries
  each field's bounding box; a field whose box is invalid is
  `supported: False`, never hidden — the claimed box rides the
  draft, labelled by the verdict. Dates resolve through the
  Phase 3 resolver anchored on the photo's `created_at`.
  Approval of a flyer-born candidate now sets `Event.flyer_id`
  in the mint itself — **Phase 4's by-hand stand-in is lifted**.
  A reading with **no field values at all** mints no candidate:
  nothing readable is a result, not a draft (the mock driver's
  schema-shaped shrug lands exactly there).
- **B-064 board segmentation** (`ai/segmentation.py`): a
  SAM-shaped wire client (POST `{base}/segment`, stub-transport
  tested) behind a `BoardSegmenter` protocol; the decided
  fallback asks the vision driver for poster boxes
  (`board_segmentation` prompt v1) and the SAME region pipeline
  runs. Children are cut once with Pillow into child Flyer
  sources (permission record inherited from the board), then
  read one by one. If SAM refuses at runtime the run falls back
  to vision and **the audit names the driver that actually cut**
  (`vision (fallback: SAM refused)`). Golden board yields its 3
  posters as separate flyers in both driver modes.
- **B-065 transcript + alt text**: every read flyer stores its
  transcript (which becomes its citable chunk) and a drafted
  description; `alt_text_status` flips to `complete` only for a
  **genuine** description (≥20 chars, not the placeholder, not
  the filename) and a human-completed alt is **never**
  overwritten by a machine draft. `PATCH /groups/{id}/flyers/{id}`
  lets a member+ write the description (non-descriptions refused
  422). Standing scan test: no flyer API response ships the
  placeholder unflagged — the pairing (placeholder ⇔
  `awaiting_description`) is asserted across every surface.
- **B-066 links**: candidate `draft["links"]` = the model's
  decoded codes (QR/printed, with regions) **merged with**
  printed URLs extracted deterministically from the flyer's own
  transcript (scheme-ful and `www.` addresses; bare domains are
  the model's half). **No QR decoder was hand-rolled and none
  is pretended**: decoding is the vision model's structured
  output — the boundary is recorded here plainly.
- **B-067 generated image**: POST
  `/groups/{id}/events/{id}/generate-image` (organiser+) via the
  adapter's `generate_image`; stored as a Flyer with
  `generated=True` — a **column**, carried by `FlyerView` in
  every projection (detail, list, agenda, tonight, public, wall)
  forever. The picture's provenance is a manual source naming
  the requesting organiser; its alt text completes only from the
  organiser's own genuine description. An event with a picture
  refuses a second (409). The **live driver raises ModelError
  in plain words** — no image-generation endpoint is wired;
  fixture/mock carry the tests.
- **B-068 transcription**: the B-031 stub handler is real —
  audio through the adapter's `transcribe`, usage recorded,
  speaker-labelled chunks by the paste discipline (sender
  prefix split, continuations joined, nothing dropped), the
  placeholder chunk replaced, source `ready`/`done`, audit
  `transcription.completed`. Golden voice note → chunks →
  downstream extraction with quote verification. A failed
  transcription leaves the note honestly `awaiting_transcription`.
- **B-069 job pipeline**: vision work is jobs (B-033 machinery)
  with real progress; a board's progress is the share of its
  posters read. **Partial success is a state**: a failed child
  is `read_failed` on the wall, the board still completes, and
  `vision.partial {read, failed, total}` is audited; only
  all-failed fails the job.
- **B-070 flyer wall**: GET `/groups/{id}/flyers?period=YYYY-MM`
  — the period's posters (by their event's month, else arrival
  month), dated first; unapproved flyers show `pending_review`,
  failed reads show `read_failed`, never invisible.
- **B-071 duplicates**: hand-rolled **average-hash** perceptual
  hash (8×8, `services/images.py`, computed at ingest) — same
  poster twice (≤6 bits apart) + same drafted title or date →
  `possible_duplicate` flag and a `duplicate_of` pointer on the
  new draft: a merge SUGGESTION in the queue, never a silent
  second event, never an automatic merge.
- **B-072 print month**: GET `/groups/{id}/print/month?month=`
  — rows carry every event fact (title, group-local date/time,
  place, address, host, cost, bring, description) and the
  rendered `text` is asserted fact-by-fact in tests.
- **B-073 vision evaluation** (`ai/vision_evaluation.py` +
  corpus/baseline JSON): the golden flyers (clean / handwriting
  / rotated / glare) scored per type through the real pipeline —
  clean 1.00, the other three 0.75 each by deliberate corpus
  imperfection (a missing cost, a missing place, a digit-misread
  date), overall 0.8125; the 0.02 gate has a degradation test
  with teeth. `python -m gpa.ai.vision_evaluation` prints the
  table.
- **B-074 gate**: `tests/test_phase5_gate.py` — Flow A
  (photo → read → queue → approve → published event with the
  flyer attached by the real mint → agenda) on a fresh DB,
  offline, with the **fixture** driver; and again with the
  **mock** driver, whose honest end is: job done, no invented
  candidate, flyer flagged on the wall. OPENAPI.json +
  frontend_types.ts regenerated (**77 paths, 80 schemas**);
  FRONTEND_HANDOFF.md carries the flyer notes. Phase 5 folds
  into ZIP-Beta (Sat Oct 31) — no ZIP cut at this gate.
- **Superseded assertions, updated with the phase**:
  `test_voice_live.py`'s stub-era assertion (source still
  awaiting after the job) now asserts the real handler's words
  land, with the supersession noted in the test;
  `test_prompts.py`'s manifest expectations grew
  (`board_segmentation` v1, `flyer_reading` v2).
- **Driver reality (the phase's standing boundary)**: no real
  Muse Image / SAM / transcription endpoint or credential
  exists in this environment. Everything above runs through the
  Phase 3 adapter with fixture/mock drivers; the SAM client is
  stub-transport tested only; the live driver's `generate_image`
  refuses in plain words. The B-074 live-driver run is recorded
  as **awaiting a real Meta endpoint** — never faked.

### Phase 6 — De-duplication, changes, truth-keeping (B-075 → B-082): ✅ COMPLETE (2026-10-24)
- **B-075 cross-source matching** (`services/matching.py`):
  hand-rolled scorer (title .35 / time .35 / place .20 / host
  .10, weighted mean over present components; ½·Jaccard +
  ½·containment on article-stripped tokens; exact date 1.0,
  ambiguous-reading credit 0.85, ±1 day 0.4). Threshold 0.6.
  **A bare title resemblance is halved and can never propose**
  — a match needs time/place/host evidence too. Candidate
  detail carries `matches` (best first, per-component scores,
  `likely`); cancelled and tombstoned events never match.
- **B-076 merge service** (`services/merging.py`): merging a
  candidate into an event is field-by-field — empty fills,
  the fuller form of the same fact refines, agreement is
  recorded by links, genuine disagreement is settled by
  SUPPORT: distinct sources per VALUE (sources recorded
  backing a different value never credit the current one —
  contradiction rows arbitrate the count). Unsupported claims
  never move anything; ambiguous/unresolved starts are
  skipped, never guessed. All candidate provenance becomes
  `EventSourceLink` rows, so the merged event answers "where
  did this come from?" per field (`GET …/events/{id}/sources`).
  Candidate → `merged`. The edit engine was extracted from
  B-050's PATCH path as `events.apply_changes` (and
  `events.apply_cancellation`) so merges, changes and edits
  share one EventChange-writing door.
- **B-077 change candidates**: new prompt `extraction_changes`
  v1; kind `event_change` is REQUESTABLE but deliberately NOT
  in the default four-kind run (first announcements must not
  draft "changes" out of themselves). The pipeline links each
  change to its target at draft time — `matching.match_by_name`
  treats an exact name as a REFERENCE (score 1.0), else the
  weighted score at threshold; unmatched →
  `change_target_unknown` flag. Approval applies the change
  inside the approval transaction
  (`merging.apply_change_candidate`): partial moves keep the
  unmoved part, a start move carries the end by the same
  delta, cancellation flows through `apply_cancellation` with
  the source's quote as the reason. Refusals are plain and
  total (no target / gone / cancelled / unbacked claim /
  unresolved time / changes-nothing) and leave the candidate
  pending; rejection leaves the event standing.
- **B-078 re-announce drafts** (`services/announce.py` +
  `AnnouncementDraft`): built from the material EventChange
  rows since the last draft, each line humanised old → new in
  the event's timezone ("Start: Friday 06 November 2026,
  06:30 PM → 07:30 PM") — never a bare "updated".
  Nothing-to-announce → plain 422. Drafting does NOT clear
  `needs_reannounce` — sending is Phase 10.
- **B-079 contradictions** (`EventContradiction`): a merge
  tie keeps the event's value and raises a visible row
  (values with per-value source lists, the current marked);
  organiser event detail carries open ones. Resolve picks one
  of the said values (else 422) and applies it through the
  change history. A value that later arrives better-sourced
  settles its own open contradiction automatically.
- **B-080 tombstones** (`services/tombstones.py`, `Tombstone`,
  `events.deleted_at`; migration `0009_phase6_truth`,
  inspector-guarded like 0007/0008): events soft-delete —
  gone from every read (events service, calendar views, iCal
  feeds, flyer wall, matching) — and restore whole inside 30
  days; post-window restore is refused in plain words even
  before the purge; `purge_expired` hard-deletes expired
  events children-first (`events.hard_delete_event`) and
  drops expired stones. SOURCES keep the B-023 law at full
  strength (chunks + provenance still go at once) but
  `delete_source` first snapshots source+chunks+provenance+
  flyers into the tombstone; restore rebuilds with the
  original ids and lifts candidates' `source_deleted_at`.
- **B-081 quality report** (`quality.group_quality_report`,
  `GET …/quality-report`, member+): missing place / missing
  host among PUBLISHED non-deleted events (drafts are allowed
  to be unfinished), low-confidence fields in the open queue,
  open contradictions — and `needs_attention`, the badge count.
- **B-082 gate**: `tests/test_phase6_gate.py` — the whole
  story as one scripted test (triple announcement → one
  event → move → re-announce draft naming both times →
  cancel). OPENAPI.json + frontend_types.ts regenerated
  (**86 paths, 94 schemas**); FRONTEND_HANDOFF.md carries the
  Phase 6 notes (merge view, change candidates,
  contradictions, tombstones, quality badge). Phase 6 folds
  into ZIP-Beta (Sat Oct 31) — no ZIP cut at this gate.
- **35 new tests** (matching 7, merging 7, migration 1, change
  candidates 6, announce 6, tombstones 5, quality 2, gate 1).

### Phase 7 — Planning: tasks, decisions, polls, checklists (B-083 → B-094): ✅ COMPLETE (2026-10-25)
- **Migration 0010_phase7_planning** (guarded creates in the
  0009 style; up/down/up tested against 0009): tables `tasks`,
  `decisions`, `open_questions`, `polls`, `poll_options`,
  `poll_votes` (unique per poll+voter), `checklist_items`,
  `role_slots`, `cost_lines`, `availability_slots`,
  `availability_marks` (unique per slot+voter), `templates`.
  Domain code follows the house shape: `models/planning.py`,
  `schemas/planning.py`, services `planning` / `polls` /
  `checklists` / `budget` / `availability` / `templates`,
  routers of the same names; contract now **121 paths, 134
  schemas**.
- **Approval mints planning (the Phase 4 pattern, extended)**:
  `groups.approve_candidate` now also calls the planning mints
  in the approval's own transaction — a task / decision /
  question candidate approval creates its entity and fills
  `resulting_entity_*`; a mint that refuses (empty draft, a
  draft `owner_id` naming a non-member) rolls the whole
  approval back and the candidate stays pending.
- **B-083 tasks**: direct create + per-event/per-group lists
  (filters: status, unclaimed, owner); `unclaimed` and
  `overdue` are computed server-side on every response, never
  stored. THE OWNER LAW: the extraction draft's owner WORDS
  land only in `suggested_owner_label`; `owner_id` is set
  solely by a person's act — direct create naming a member, a
  PATCH assigning/unassigning, or a reviewer putting a real
  member's `owner_id` into the edited draft before approving.
  The AI never assigns a silent victim (pinned by test). A
  draft due-date that resolved to a day only is due at END of
  that local day (recorded decision, in the service). Status
  flow: forward moves, cancel from open/in_progress, reopen
  from done; done stamps `completed_at`.
- **B-084 threads generalised**: `services/discussion.py` is
  parent-generic (`PARENT_TYPES = event|task|decision|poll`);
  ONE Thread/Comment system — the B-060 event paths read the
  same rows as the new `…/comments/{parent_type}/{parent_id}`
  routes. `CommentResponse` always carries `parent_type` +
  `parent_id`; `event_id` is populated for event parents, null
  otherwise (SSE `comment.created` frames likewise). Ordering
  law in the router: comment-act routes register BEFORE the
  generic parent routes, or `POST …/comments/{id}/hide` would
  be read as a post to a parent named "hide".
- **B-085 decisions**: mint or direct create; supersede chains
  both ways in one transaction (superseding a non-current
  decision is a 409); `?current=true` is the settled-truth
  view; `…/decisions/{id}/chain` walks root → current.
- **B-086 questions + gaps**: mint (asked of the group —
  naming who must answer is a person's PATCH) or direct
  create with `asked_of_id`; drop is a settled state, never a
  delete; resolve may mint a decision in the SAME call
  (`resolution_decision_id` links it). `…/events/{eid}/gaps`
  aggregates missing fields (place, host) + open questions +
  unclaimed roles + unclaimed tasks into one flat `gaps` list
  plus sections (golden half-planned event pinned).
- **B-087 polls**: choice/date kinds (date options REQUIRE an
  instant; the label defaults to the formatted moment);
  vote/change-vote with the (poll, voter) unique constraint —
  `cast_vote` COMMITS inside the service and the router reads
  the tally only afterwards (commit-before-answer, proven by
  a separate-session test); concurrent same-member votes hold
  one current vote via the constraint + bounded retry
  (transaction test, 8 threads). Close freezes; `closes_at`
  ends voting by the clock. `apply-winner` (organiser+,
  publish scope): attached event MOVES through
  `events.apply_changes` (history + needs_reannounce ride
  along); no attached event mints one via direct entry; a tie
  or zero votes refuses in plain words — the backend never
  picks a winner.
- **B-088 checklists + roles**: bring/prepare lines with
  quantities; claims are one-person promises (claiming
  another's line/slot is a 409; "held" = a member id OR a
  seat's label claim); role slots are N one-person rows per
  role name. `GET …/supplies?start=&end=` (default: this week,
  Monday → Monday group-local) folds lines by
  case/whitespace-normalised label across non-cancelled,
  non-tombstoned events — totals + per-event breakdown
  (merged-supplies golden pinned).
- **B-089 budget-lite**: cost lines + per-event summary;
  integer cents END TO END — the request schema takes an int
  (a float body is a 422 before any service runs), the column
  is Integer, balances sum in SQL and sort by paid desc.
  Payer defaults to the caller; a named member is validated;
  words-only payers (guest cash) keep `member_id: null`.
  Stops at group truth by design: no ledger, no payments.
- **B-090 availability**: organiser-proposed slots; members
  mark can/can't (upsert under the unique constraint);
  ranking is deterministic (can desc, can't asc, starts asc)
  — golden group-of-five pinned. Privacy boundary is the
  shape: only marks against proposed slots exist; no
  private-calendar data is read, stored, or shown.
- **B-091 templates**: payload grammar `{event, tasks (due as
  day-offsets), checklist, roles}`; `template_from_event`
  snapshots a plan (claims become open slots); instantiate
  builds through the same doors as direct entry — the event
  via `events.create_event` (which commits it, as direct
  entry does), then tasks/checklist/roles in a commit of
  their own (two commits, documented honestly in the service;
  instantiated tasks/roles are always unclaimed). The 7
  global starters (meeting, club night, trip, fundraiser,
  class, match, celebration) seed idempotently by name via
  `seed_starter_templates`, called from `load_seed_data`
  (its return gains a `templates` key; created/already_present
  shapes unchanged).
- **B-092 search**: `GET …/search?q=` over events (published/
  cancelled, tombstones excluded), tasks, decisions,
  questions — deterministic weighted scoring (primary text 3
  with startswith bonus, secondary fields less; ties by type,
  title, id), every hit carrying `candidate_id` / `source_id`
  / `event_id` links (golden ranking pinned).
- **B-093 digest data**: `GET …/planning-digest` — this
  week's published events, unclaimed open tasks, open
  questions, undecided polls, computed once in
  `planning.planning_digest` (golden snapshot at a fixed
  moment pinned; week = Monday 00:00 group-local → next
  Monday).
- **B-094 gate**: `tests/test_phase7_gate.py` — Flow B from
  the proposal: the 40-message trip argument pasted (40
  chunks = exactly one extraction window), fixture-extracted
  into 1 event + 3 tasks + 2 decisions + 2 questions, two
  reviewer edits (place corrected; the kayaks' volunteered
  owner confirmed by naming the member in the draft), all
  approved and minted, and the conversation continuing in the
  minted event's thread. OPENAPI.json + frontend_types.ts
  regenerated (121 paths, 134 schemas); FRONTEND_HANDOFF.md
  carries the Phase 7 planning-screens notes. Phase 7 folds
  into ZIP-Beta (Sat Oct 31 — the ZIP is cut at the Phase 8
  gate, B-104); no ZIP cut here.
- **54 new tests** (tasks 9, decisions 4, questions 5, polls
  11, checklists 3, budget 3, availability 4, threads 5,
  templates 4, search/digest 4, migration 1, gate 1).

### Phase 8 — Reminders, notifications, digests (B-095 → B-104): COMPLETE (2026-10-24)
- B-095 notification core: models/notifications.py (Notification,
  NotificationPreference, MemberNotificationSettings, EventInvite,
  Digest, MailEvent), services/notifications.py (create = row +
  notify job in the CALLER's transaction; policy at creation —
  preferences choose channels, both-off = audited suppression, no
  row), services/mailer.py transport boundary (FileTransport =
  services/mail.py file outbox; Capture/Failing for tests;
  GPA_MAIL_TRANSPORT; provider driver is Phase 12 B-133 —
  BOUNDARY: no real email exists in this environment).
  jobs.queue() added (no-commit enqueue) so outbox writes are
  transactional. RSVP promotions (B-061) moved from the audit
  stub + bare job to real notifications (kind rsvp.promoted,
  payload keeps to/kind/rsvp_id + gains notification_id);
  rsvps' stub handler REMOVED, notifications' registered instead.
- B-096 reminders: services/reminders.py — rules (group
  reminder_rules_json; defaults 7d/1d/morning-of at 09:00/09:00/
  08:00 event-local) materialised per RSVP'd person + host at
  publish / edit / approval-mint / approval-change / RSVP set /
  RSVP remove, same transaction; reschedule supersedes
  outstanding rows and DELETES their queued jobs (roadmap says
  "cancelled"; deletion is stronger — superseded rows keep the
  record). Bodies: template from facts (formatted start, place,
  bring lines, EventChange words). Migration 0011.
- B-097 change alerts: announce.approve_announcement + POST
  .../reannounce-drafts/{id}/approve — alerts exactly the
  RSVP'd (event_recipients: coming+maybe; guests at their
  invited address; +guests ride the member), clears
  needs_reannounce, stamps sent_at/sent_by; second approve 409.
- B-098 invites: services/invites.py — token sha256 at rest,
  shown once (create response link + the invite notification);
  public GET (B-059 public projection) + POST rsvp via
  rsvps.set_rsvp as guest:<email>; tamper = 404, nothing made.
  GPA_PUBLIC_BASE_URL builds links ("" = path-only).
- B-099 digest: services/digests.py + Jinja2 templates in
  src/gpa/templates/ (jinja2 3.1.6 was ALREADY a declared,
  locked dependency — used as-is). Compose = B-093
  planning_digest + next week's events; Digest row stores both
  renderings; one digest notification per active member.
  Scheduling = digest_run job chain: anchored by digest
  settings save (update_group → ensure_scheduled) or a manual
  run; each run queues its successor. assert_alt_text runs on
  every render (templates carry no images).
- B-100 preferences: switches (default on; absence = on) +
  quiet hours per member per group, member's timezone; policy
  ONCE at creation; only reminder-category held; urgent never
  held; cancellation alerts are urgent because cancel_event is
  the organiser's act.
- B-101 inbox: GET /users/me/inbox (+ unread_only), mark-read
  (other's = 404); SSE notification.created frames in
  services/stream.py (recipient's stream only, no body).
- B-102 deliverability: attempts on the row; dead at 5 (=
  job max_attempts); failures list (organiser+) + resend;
  /webhooks/mail/{provider} stubbed receiver (bounce marks
  the notification failed; signature checks arrive with the
  Phase 12 provider). deliver() COMMITS the failure record
  before raising — the job chassis rolls back on handler
  failure, and the record must survive it.
- B-103 AI wording: group ai_reminder_wording opt-in;
  reminder_wording prompt v1 (spark tier); ships ONLY if the
  formatted start, place, and every bring item survive
  verbatim; template_body + wording_source stored either way;
  any ModelError = template ships.
- B-104 gate: tests/test_phase8_gate.py — invite → guest
  answer → reminder delivered → move → approved alert to
  member + guest → digest mention, capture transport as the
  mailbox, run_at + send_after moves as the fake clock.
- **ZIP-BETA CUT**: gpa-backend-v0.3-2026-10-24.zip (releases/;
  §8 anatomy — repo under gpa-backend/, OPENAPI.json,
  FRONTEND_HANDOFF.md, CHANGELOG.md, TEST_REPORT.md,
  SHA256SUMS.txt, release-manifest.json). ZIP-Alpha was never
  cut separately: its gate passed 2026-10-24 and it folded
  into Beta (manifest states this). OPENAPI.json +
  frontend_types.ts regenerated (133 paths, 150 schemas).
  make_release_zip.py grew the §8 train shape (flat ZIP-0
  shape kept). Three test_rsvps assertions updated as
  superseded (Phase 4 pattern): they counted ALL notify jobs;
  the outbox now carries reminders on the same queue, so they
  select by payload kind — intent unchanged.
- Deviations/boundaries: (1) mail = driver boundary, no real
  sending (above). (2) B-096 old jobs DELETED not cancelled.
  (3) Digest chain anchoring: settings-save/manual-run anchors;
  a group that never touches settings gets its chain at first
  manual run. (4) In-app channel exists NOW as the inbox
  (roadmap text said Phase 12; B-101 in this phase defines it —
  the phase's own slices win). (5) GroupResponse grew comms
  fields (additive).

### Later phases: NOT STARTED (per roadmap order)
Phase 9 begins at B-105.

## Decisions & deviations (from the roadmap defaults)

- **Password hashing: argon2-cffi** (the roadmap allowed pwdlib or
  argon2-cffi; chose the direct reference binding — fewer layers,
  Argon2id via PasswordHasher defaults). Recorded here per B-011.
- **JWT: implemented in-house** on stdlib hmac/hashlib (security.py)
  instead of adding PyJWT — the format is three base64url parts and
  an HMAC; owning it keeps the dependency list exactly the
  roadmap's and the behaviour fully testable (D-46 spirit).
- **Email validation: in-house** small validator
  (services/identity.normalize_email) instead of pydantic EmailStr,
  which would have required the extra email-validator package.
- **Initial Alembic migration** builds from model metadata
  (create_all/drop_all) rather than op-by-op DDL — at revision 0001
  the models are the reviewed schema; later migrations are
  hand-written as usual. Noted in the migration file itself.
- **Seats' group context**: an invited (not yet active) person can
  reach a private group with role=None so accept/decline works;
  everyone else gets the 404. Implemented in deps.get_group_context.
- Test suite takes ~1 minute, almost all of it Argon2 hashing in
  registrations — acceptable; revisit only if it slows the train.
- **Phase 4 added NO new dependencies either** — the RRULE
  expander and the iCal serialiser/parser are hand-rolled
  in-house (D-46), as the task required; zoneinfo (stdlib)
  carries every timezone truth.
- **D-15's remaining half**: ETag/If-Match is built (B-050);
  the `Idempotency-Key` store for POST creates is NOT yet
  built anywhere in the codebase — no Phase 4 slice names it,
  and claiming it would be a lie in this map. It belongs with
  the write-safety work of the multi-user phases.
- **Migrations 0007 is a guarded create-tables revision** in
  the 0006 style: 0001 builds from live metadata, so fresh
  databases already hold the Phase 4 tables when 0007 runs.
- **No new dependencies in Phase 2** — the roadmap's B-025 escape
  hatch (add pypdf) was NOT needed: B-025's stdlib PDF text-layer
  reader (FlateDecode + Tj/TJ) covers text-layer PDFs, and
  scanned-image PDFs are refused in plain words until Phase 5
  OCR. Pillow (already locked for B-027) is the only imaging lib.
- **Uploads are raw request bodies** (+ `filename` query param),
  uniformly — file, newsletter, image, voice. No multipart
  anywhere; the frontend handoff says so in bold.
- **Migrations 0002–0005 are inspector-guarded** add/drop: 0001
  builds from live model metadata, so a fresh database already
  has every column when the later revisions run; the guards keep
  up/down/up clean on both fresh and pre-Phase-2 databases
  (tests/test_migrations_phase2.py).
- **Concurrency note (2026-10-24)**: B-023→B-025 were built by
  the 10-minute forge cron and B-026→B-034 by a subagent in one
  sitting; one overlapping edit (candidate tombstone columns) was
  absorbed cleanly by the cron's slice. The `.forge-lock` file
  was accidentally committed once — it is removed again; treat a
  lock in the tree as stale if its timestamp is old.

## Standing laws in force here

- No Docker, anywhere, ever (native installs + uv only).
- All code is the team's own original work (D-46); third-party
  software enters only as installed, locked dependencies.
- Licence: **undecided** — "Licence: to be decided by the team — all
  rights reserved." No file may presume one (no licence field in
  pyproject, no classifiers). Business/licence decisions rest with
  Geoffrey & Henry.
- Commit-before-answer (services commit, then routers respond);
  entered data is sacred (WAL + FULL).
- Heavy plain-words comments (D-26): header on every file,
  docstrings everywhere.
- Local git commits only. No remote, no push, ever, unless Volmarr
  says otherwise in so many words.
