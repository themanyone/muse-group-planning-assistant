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
Alembic up/down/up clean; **877 tests passing on SQLite and 865
on PostgreSQL, 0 failed on either** (156/350 slices: Phases 0–15
complete) — counts from the Phase 15 gate runs against the final
Phase 15 tree 2026-10-25 (881 collected on both engines = 853 at
the Phase 14 gate + 28 new Phase 15 tests; the engines' passed
delta is exactly their skip delta — parity tests skip on
SQLite, named SQLite-file tests skip on PG; details and the
two-halves method in TEST_REPORT's Phase 15 addendum). ZIP-Beta cut at the Phase 8 gate
(releases/gpa-backend-v0.3-2026-10-24.zip, §8 anatomy); ZIP-RC cut
at the Phase 12 gate (releases/gpa-backend-v0.4-2026-10-25.zip,
§8 anatomy) — Phases 9–12 fold into it; **ZIP-Final cut at the
Phase 14 gate** (releases/gpa-backend-v0.5-2026-10-25.zip, §8
anatomy + RESULTS.md top-level + the drill log inside) — Phases
13–14 fold into it, and its freeze drill ran the demo cold from
a clean unzip (27 s; FREEZE_DRILL.md).
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

### Phase 9 — Members, nonprofit pack, resources (B-105 → B-112): COMPLETE (2026-10-24)
- Tables live in models/nonprofit.py (SubGroup,
  SubGroupMembership, Announcement, Resource, ResourceBooking,
  AttendanceMark, CandidateApproval); Group gained
  review_min_role (default "organiser"),
  review_approvals_required (default 1), template_set
  (default "starter"); Event/Task gained subgroup_id; RoleSlot
  gained starts_at/ends_at. Migration 0012_phase9_nonprofit.
- B-105 sub-groups: THE rule lives in services/subgroups.py —
  visible_subgroup_ids(db, group, *, user_id, role) → None =
  organiser+ unrestricted, else the enrolled set; scoped-out
  items are 404/absent on member-facing reads (events list +
  detail, tasks list/detail/PATCH, calendar agenda/month/
  tonight/conflicts, planning search incl. decisions/questions
  via their event, announcements). Enrolment: organiser manages
  anyone; a member enrols/unenrols THEMSELVES; per-sub-group
  role grant is organiser-only; enrollee must be an ACTIVE
  group member (422); duplicate 409. Deleting a sub-group with
  scoped events/tasks/announcements is refused 409 with counts
  — a delete must never silently widen an audience.
- B-106 announcements: services/announcements.py; the B-043
  law restated for humans — _require_person refuses seats in
  the service ("announcement ≠ publication"). Pinned first,
  then newest; expired posts leave the board unless an
  organiser passes include_expired (the router honours the
  flag for organiser+ only).
- B-107 shifts: services/shifts.py — a shift is headcount
  RoleSlot rows sharing role + window; claim/release rides the
  B-088 slot endpoints (one claim door, no second truth).
  gap_report: published events in a window, only events WITH
  gaps, missing roles in FIRST-OPENED order, one-line summary
  ("Saturday still needs a driver and a door person."; count
  1 → "a {role}", else "{count} {role}s").
- B-108 resources: services/resources.py — items with a keeper
  (keeper words default from membership); duplicate name 409;
  booking a cancelled event 422; duplicate (resource, event)
  booking 409. Double-booking is ALLOWED and flagged:
  calendar conflicts emit resource_overlap flags (this lands
  Phase 4's deferred B-057 resource conflicts), booking
  responses echo their own conflicts (published + strict
  overlap only; back-to-back never flags).
- B-109 attendance: services/attendance.py — organiser-marked,
  published events, active members, idempotent. Reads are
  COUNTS ONLY: event headcount + summary (recent N begun
  published events, counts + average). The refusal is a test:
  per-person attendance routes do not exist (404; the
  ".../attendees" spelling lands on the unmark route's shape
  and answers 405 — a write's shape, not a read). The mark
  receipt is the only response naming a person — it echoes
  the organiser's own act. No person's identity appears in
  any aggregate payload.
- B-110 member import/export: services/member_io.py — CSV in
  as raw body (admin+), email found BY SHAPE (contains "@"),
  not position, so unquoted "Carter, Cy" survives its comma
  and stray trailing columns fall away. Report counts created
  users / memberships / enrolments / already-members; skipped
  rows (in-file dupes) and error rows (bad email) named by
  spreadsheet row number. Imported people arrive INVITED
  (activation stays the invite law's); sub-groups find-or-
  created by name; the spreadsheet never overwrites a name a
  person already has. DRY RUN = the identical work, then
  rollback — no audit row (a dry run happened to nobody).
  Export CSV (real quoting; comma names round-trip) or JSON,
  all memberships, name order, sub-groups joined "; ".
- B-111 settings pack: services/settings_pack.py — one
  validated schema over the Group dials (review rules,
  reminder rules in B-096's vocabulary, digest weekday/time/
  enabled, template_set closed vocabulary {"starter"},
  ai_daily_budget_micros with null = no cap). Present governs
  (incl. null), absent leaves alone; one bad dial changes
  nothing. Audit group.settings_updated with per-field
  old→new; digest changes re-anchor via digests.ensure_
  scheduled in the same transaction. Approval wiring:
  routers/groups.py approve door dropped to member;
  services/groups.py::approve_candidate enforces — seat check
  FIRST (seat_cannot_approve unchanged), then review_min_role
  (forbidden), then, when required > 1, CandidateApproval
  rows per DISTINCT approver (repeat = 409), candidate stays
  pending + audited candidate.approval_recorded until the
  count is met, when the final approver runs the ordinary
  mint path. CandidateResponse carries approvals_recorded /
  approvals_required (additive).
- B-112 gate: tests/test_phase9_gate.py — import roster →
  flyer in (fixture driver) → event minted → scoped to the
  crew (enrolled sees it, unenrolled gets 404) → driver shift
  opened, gap named, claimed, gap closed → attendance marked,
  count read → digest sent naming the event, delivered to the
  crew member's mailbox (capture transport). NO ZIP this
  phase (folds into ZIP-RC at B-138, roadmap's train).
  Contract regenerated: 151 paths / 179 schemas (was 133/150).
- Deviations/boundaries: (1) Migration 0012 on the upgrade
  road adds subgroup_id as a PLAIN column — SQLite cannot add
  an FK in place; fresh DBs get the FK from 0001 metadata and
  services validate the reference on both roads. Downgrade
  drops the scope indexes first and uses batch mode for the
  two subgroup_id columns (SQLite cannot drop an FK-carrying
  column in place). (2) The iCal feed and the weekly digest
  stay GROUP-WIDE in this phase: organiser-published
  broadcasts; the digest is one stored row per group (B-099).
  Interactive member-facing surfaces enforce scoping.
  (3) One Phase 4 assertion superseded (the Phase 8 pattern):
  test_calendar_views' conflict note now asserts
  "resource_overlap" — B-108 made the deferred flag real.
  (4) Approval door change (member + service-enforced pack)
  is behaviour-preserving at defaults; recorded in CHANGELOG.
  (5) Attendance "…/attendees" 405 nuance (above).
  (6) GATE FINDINGS, fixed in cb4ba10: the first gate run
  failed ONE test — the B-110 dry-run test's own assertion
  (it forgot its fixture's accepted member when asserting
  the unchanged roster; the dry run itself was correct).
  The same run exposed a latent foundation defect: main.py's
  request-id log filter sat on the root LOGGER, whose
  filters never run for records propagated from child
  loggers — third-party records (httpx) hit the handler's
  formatter without request_id and raised. The filter now
  rides the HANDLER. Also: a verification run died to the
  /tmp hazard (tmpfs 100%, pytest-of-root 511M) — gate runs
  now use --basetemp on the workspace overlay. Final clean
  run: 704 passed (673 + 31), read from the run's own log.

### Phase 10 — Context pruning and group memory (B-113 → B-122): COMPLETE (2026-10-25)
- Tables live in models/memory.py (Summary, PruneRun); Group
  gained prune_max_passes (default 2), prune_token_budget
  (default 120000), prune_driver (default "hosted").
  Migration 0013_phase10_memory (guarded, in the 0012 style).
- The corpus law: memory reads APPROVED history only —
  sources scoped "group-history" + CURRENT decisions +
  APPROVED summaries of kind discussion/recap — assembled by
  services/memory.approved_corpus, positions renumbered in
  chronological order, chunk ids source:/decision:/summary:.
- B-113 pruning core (ai/pruning.py): Geoffrey's design — a
  full LLM judges relevance chunk by chunk; the module says
  plainly it is NOT embedding RAG (a test asserts the words
  and the absence of embedding/vector imports). Windows ride
  extraction.chunk_windows; tier "wonder"; the token budget
  is checked BEFORE each window call on the rendered
  prompt's estimate (unjudged chunks stay dropped, outcome
  flagged budget_stopped). Golden 500-message corpus: 12/12
  human-marked chunks kept at ≤25% of corpus tokens.
- B-114 multi-pass: prune_multi = prune_once +
  reconsider_dropped per extra pass over the dropped set;
  ceiling MAX_PASSES_CEILING = 3 above the settings caps.
  B-115's gap pass reuses reconsider against the unverified
  draft's text.
- B-115 ask (services/memory.ask_question): prune → draft
  from survivors only → EVERY citation verified by B-040's
  quote rule (extraction.find_quote); zero citations is
  unverifiable by construction. Failure returns the refusal
  led by "I couldn't verify this" with found material
  labelled and its citations carrying null chunk ids —
  never a confident guess (the acceptance test). Run +
  Summary(kind=ask) committed before the answer.
- B-116 summaries propose, the queue disposes:
  summarise_thread stores a draft Summary and lands the
  decisions/tasks the summary NAMES as Candidates
  (created_by "system:summary", flag "from_summary",
  source_id NULL, NO provenance rows — comments are not
  SourceChunks; the quotes live in the draft fields). It
  writes NO Decision/Task rows. Hidden/deleted comments
  never enter a summary (read at the caller's role, then
  filtered regardless).
- B-117 briefings weave the structured present (decisions
  linked to or word-matching the event, latest APPROVED
  recap, open OpenQuestions, unclaimed RoleSlots) with
  history pruned on the event's own terms; the run's kept
  set is the briefing's receipt. Golden: an August landmine
  about the flooding marquee lands in the November briefing.
- B-118 recaps read discussion + the attendance COUNT +
  Flyers with alt_text_status "complete" only (the alt-text
  law both ways). Approval is the only door from draft to
  memory; approving a newsletter creates a Source
  (kind "newsletter", paragraphs as chunks) — the loop
  closes, and the loop test proves a distinctive phrase
  from an approved letter survives into a later prune.
- B-119 LocalDriver (ai/drivers_local.py): OpenAI-compatible
  chat-completions, text only (media calls raise plain-words
  ModelError), one model all tiers, cost_micros 0 (own
  hardware), endpoint token counts when reported. Selected
  per-group by prune_driver="local" in
  services/memory.resolve_client; a missing endpoint config
  is a plain refusal, never a silent fallback. Extraction
  always uses the process client. Swap test: same corpus,
  same survivors, both drivers.
- B-120 transparency: GET /memory/runs (member) lists runs
  with kept/dropped ids, passes, model, cost; DELETE one or
  all (organiser) purges the ledger — summaries are never
  deleted by a purge (their source ranges keep chunk ids
  naming sources/decisions that still exist).
- B-121 evaluation (ai/pruning_evaluation.py +
  pruning_eval_corpus.json): golden questions with
  human-marked relevants, scored through the real engine
  against a recorded judge that is DELIBERATELY imperfect
  (one named miss, recall 0.50; one over-keep, precision
  0.75). Means: recall 0.88, precision 0.94; floors asserted
  in tests; the table lives in TEST_REPORT's Phase 10
  addendum.
- B-122 gate: the three demos in mock mode (honest outcomes
  — the mock keeps nothing, so ask returns the refusal) and
  live mode (LiveDriver over a content-aware stub
  transport; no real Meta endpoint exists in this
  environment — the standing boundary, recorded not faked).
  Contract regenerated: 160 paths / 189 schemas (was
  151 / 179). Bruno collections unchanged (still the B-010
  skeleton, as at the Phase 9 gate). No ZIP.
- Gate findings: (1) two AppError("not_found", status=404)
  calls in the new service passed a kwarg the error chassis
  does not take — the registry owns statuses; fixed and
  covered by the B-120 cross-group purge test. (2) The recap
  service first read its discussion viewer-less, and the
  visibility bright line (visible_to with role None) hides
  EVERY event from a viewer-less read — the recap now reads
  at the organiser role its door guarantees, then filters
  hidden/deleted regardless.

### Phase 11 — Samtal bridge and live sources (B-123 → B-130) ✅

- **B-123** Samtal source connector (`services/samtal.py`,
  `services/livechat.py`, migration 0014): a live source
  (B-032 permission record) syncs a hall's new messages as
  chunks through a cursor, byte-identical to the same words
  pasted. Speakers key on their outside id (`sender_ref`);
  `ExternalAccountLink` resolves a speaker to a member ONLY
  when a person made the link (active members only) —
  unlinked speakers stay named guests (name-collision trap
  test). Sync refusals are recorded on the source's sync
  state in plain words. **The connector is built and tested
  against recorded fixtures and the `FixtureSamtalClient`
  test double only — the live Samtal is never contacted by
  this codebase; no real address, token, or key exists
  anywhere in the repo.**
- **B-124** Calendar return path (`services/samtal_calendar.py`):
  published events stand on the hall's calendar (the hall
  source's id IS its calendar id); a content hash keeps a
  second sync quiet; Samtal-side edits/cancellations return
  as B-077 change candidates (`created_by=
  system:samtal-calendar`, words in a backing source) —
  Samtal never writes events directly. Foreign hall events
  (no `ref`) never import.
- **B-125** Messenger: **access not granted** — the slice is
  its documented fallback: `services/messenger.py` refuses
  sync in plain words naming the paste/export road
  (B-024's mechanics, proven identical), and the refusal is
  recorded on the sync state. Nothing fakes a live
  integration.
- **B-126** Inbound webhooks (`services/webhooks_in.py`):
  endpoint per source; URL token shown once, kept SHA-256
  hashed; signing secret DERIVED by HMAC from the server
  secret (stored nowhere); signature over
  `"<timestamp>.<body>"`, 300 s window; `WebhookReceipt`
  gives exactly-once (replay = 409 webhook_replay);
  refusals audit-logged (group NULL for unknown tokens);
  the DB-file grep finds neither token nor secret.
  REPAIR (2026-10-25, post-gate verification): the replay
  acceptance failed twice in independent full-suite runs —
  the test's helper re-signed with int(time.time()) per
  call, so under load a second boundary between the calls
  made the "replay" a fresh, valid delivery (proven by a
  recorded-signature recreation). The test now pins one
  stamp (a true byte-identical replay); a new mirror test
  pins that a re-signed fresh delivery is NOT a replay.
  The service gained the concurrent half of the guarantee:
  an acceptance commit that loses the receipt's UNIQUE
  race now reads as the same 409 (IntegrityError →
  rollback → replay refusal), never a 500.
- **B-127** Integration framework (`services/integrations.py`,
  `services/sealing.py`): kinds samtal / facebook_events /
  google_calendar / custom; config sealed whole at rest
  with a stdlib HMAC envelope (encrypt-then-MAC, fresh
  nonce, constant-time verify; key from
  `GPA_INTEGRATION_KEY` or the app secret); API reads show
  config key NAMES only; health checks record verdicts in
  plain words — Samtal checks ping the connector (the
  unavailable refusal IS the verdict), the Phase 12 kinds
  report `unconfigured` and say their live check arrives
  with their slice. Acceptance grep: a marker secret appears
  nowhere in the database file.
- **B-128** Outbound webhooks (`services/outbound.py`):
  subscriptions to event.published / event.changed /
  event.cancelled / candidate.pending; secret shown once,
  kept sealed; emission is transactional inside the doors
  where the acts happen (events service, approval mint,
  candidate doors, extraction); the `webhook_deliver` job
  signs with the inbound scheme and, on failure, COMMITS
  the attempt record before raising (the B-102 lesson);
  deliveries inspectable, `dead` at the job's attempt cap.
- **B-129** Agent pack (`services/agent_pack.py`,
  `examples/secretary_seat.py`): the seat's working surface
  is tagged `agent` in the contract — the tagger walks this
  FastAPI version's lazy `_IncludedRouter` entries to the
  route objects the schema builder reads; `SCOPE_GUIDE`
  documents the ladder once (publish never approves — a
  person's act). The example runs in mock mode in the
  suite: reads a hall, drafts its plan line, meets 403
  `seat_cannot_approve` even holding publish.
- **B-130** Gate: `tests/test_phase11_gate.py` — hall →
  sync → extraction → approval → event → back onto the
  hall's calendar (Club night, Sat 24 Oct 2026 19:00 New
  York = 23:00 UTC on the double). Contract regenerated:
  174 paths / 204 schemas (was 160 / 189). No ZIP — folds
  into ZIP-RC at B-138. No defect was found in earlier
  phases' code this phase; the two in-phase finds were in
  the phase's own new code (registration import order for
  the outbound service; a test-data slip in B-124's test).

### Phase 12 — External publishing integrations (B-131 → B-138) ✅
The outward doors. **Standing boundary for the whole phase:
no live third-party action of any kind exists in this
environment** — no Facebook Page/group account, Google
account, or mail provider is connected for this project,
and publishing/sending is Volmarr's word alone. Every
client boundary below ships the recorded-fixture double +
the honest unavailable default; live smoke tests are
recorded NOT RUN in TEST_REPORT, never faked.
- **B-131** Facebook Event creation (`services/facebook_events.py`):
  client boundary (Protocol + unavailable default +
  `FixtureFacebookClient`, page-addressed); POST/GET/DELETE
  `/groups/{id}/events/{eid}/facebook` beside the B-058
  draft route. THE PRESS: first press creates the Page
  copy (link row + payload hash), a press with our side
  unmoved is a stated no-op, a press after our edit
  updates, a press on a cancelled event carries the
  cancellation out; cancelled-without-a-copy stays B-058's
  409. `facebook_state` reports `differs` from the link
  hash. Health: the facebook branch of `integrations._run_check`
  gates on config, then pings through the boundary.
- **B-132** Google Calendar sync (`services/google_calendar.py`):
  same boundary shape (calendar-addressed). `google-connect`
  (admin) records the verdict on the row; `google-sync`
  (organiser + publish) upserts published, propagates
  cancellations as cancellations, never travels drafts,
  hash-quiet on agreement. **One-way OUT, proven by test:**
  a Google-side edit is never read back — the tampered
  copy stands for B-137 to notice.
- **B-133** Real mail provider (`SmtpTransport` in
  `services/mailer.py`, stdlib smtplib, injectable connect
  factory; `GPA_MAIL_*` settings): startup refuses a
  half-configured SMTP deployment in plain words; the
  B-102 provider webhook demands the HMAC signature
  (`GPA_MAIL_WEBHOOK_SECRET`) when one is set — an unsigned
  report records nothing. `DEPLOYMENT.md` (repo root)
  carries provider setup + SPF/DKIM/DMARC notes and the
  pre-submission checklist; the "real digest in a real
  inbox" acceptance is a checklist item there, NOT RUN.
- **B-134** Messenger notifications
  (`services/messenger_transport.py`): B-125's access does
  not exist, so the boundary's real driver is the
  unavailable one and the slice ships the clean
  `driver_unavailable`: a row that asked for Messenger
  records `messenger_status` on the Notification and
  completes via its other channels. Opt-in = the member's
  messenger preference switch AND a B-123 Messenger
  account link; preferences and the quiet-hours hold are
  the SHARED machinery (the messenger word joined
  `CHANNELS` + the preference pattern in the phase's
  foundation, migration 0015).
- **B-135** Embed + public page (`services/embed.py`):
  `groups.embed_origins_json` register via GET/PUT
  `/groups/{id}/embed-origins` (organiser read, admin
  write; an origin is scheme + host, normalised, paths
  refused in plain words); `GET /public/groups/{slug}` is
  the public page payload (B-059 projection);
  `GET /embed/groups/{slug}` refuses a present-but-
  unregistered Origin (403); the CORS middleware in
  `main.py` answers an Origin on public/embed paths ONLY
  when some group registered it (preflight included) —
  an unregistered origin gets nothing.
- **B-136** Health board (`services/health_board.py`):
  GET `/groups/{id}/integrations/health-board` (organiser+,
  registered before `/{integration_id}`) returns every
  integration with its recorded verdict and ONE advice
  sentence — token refusals name the reconnect + fresh
  token step; the board reads records and pings nothing.
- **B-137** Reconciliation (`services/reconcile.py`): the
  `reconcile` job compares each facebook/google link's
  outside copy against the owning service's canonical
  payload projection (envelope fields are not drift) and
  reports `differs`/`missing` to organisers+ as one
  `change.drift` notification. **It never writes
  outward.** The chain is digest-shaped: anchored by
  create-integration (and the manual POST
  `/integrations/reconcile`, admin) for the next 02:30
  group-local, each run queueing its successor. Door
  coherence: a copy DELETED outside is restored by the
  organiser's own press/sync; an EDITED copy stands
  (B-132's law) until a person settles it. Settings are
  threaded into `reconcile_group` — unsealing with the
  wrong settings reads every config as empty (found by
  test; the router passes the app's own settings).
- **B-138** Gate: `tests/test_phase12_gate.py` — one
  event's full outward life (draft → press → sync → move →
  both doors repair → clean reconcile → approved
  re-announce reaching a member's mailbox with the
  Messenger leg `driver_unavailable` → cancellation
  carried out by both doors → registered-origin embed).
  Contract regenerated: 182 paths / 212 schemas (was
  174 / 204). **ZIP-RC cut** (releases/, §8 anatomy):
  Phases 9–12 fold into it, as the train promised.
  Suite at the gate: **797 passed, 0 failed** (769 + 28
  new Phase 12 tests, reconciled; totals from the run's
  own report file).
  Defects found in earlier code: NONE in Phases 0–11. One
  latent red INSIDE the phase: the foundation commit's
  `CHANNELS` growth outran the preference schema pattern
  (a Phase 8 inbox test failed until B-134's schema half
  landed — fixed in B-133's commit, noted there). Two
  in-phase finds, both fixed: reconcile's settings
  threading (above) and the press/sync missing-copy
  restore split (edits stand, deletions restore).

## Phase 13 — Security hardening, privacy, and erasure (B-139 → B-146) ✅

The phase that re-attacks everything before it. `THREAT_MODEL.md`
lives in the repo root now (B-139): assets, actors, Henry's-first-
version as Case #1, one abuse case per route family mapped to
its test, accepted risks named — largest: group content is NOT
field-sealed in this build (B-167, Phase 17). A coherence test
parses the model so it cannot drift from the code.

- **B-140 fuzz**: two marked groups × 8 attacker principals ×
  ~400 seeded cases — zero leaks. FINDING (fixed at root):
  `GET /jobs/{id}` answered ANY signed-in caller; group-named
  jobs are now members-only (404 otherwise). The fuzz world
  fixture is module-scoped (`make_settings` +
  `tmp_path_factory`) — per-test worlds timed out the suite.
- **B-141**: per-group upload quota (`Group.upload_quota_bytes`,
  default 100 MiB, `quota_exceeded` 422, settings dial);
  migration 0016 also carries `source_retention_days`.
  `SourceResponse.storage_key` REMOVED (D-17) — the phase's one
  deliberate contract break; four earlier tests superseded to
  read the key from the row. The .ics door equals the view
  door; drafts refuse for every role (B-053's bright line).
  `thumbnail_key` STAYS in flyer projections for now: a named
  accepted exposure with a binding rule — any future media
  route authorises by flyer/source id through the view door,
  never by raw key.
- **B-142**: prompt render is ONE pass (`ai/promptlib.py`) —
  values are never rescanned; benign output is byte-identical
  so fixture recordings (keyed by rendered hash) survive.
  FINDING (fixed): the digest Jinja env escaped by `.j2`
  suffix, so PLAIN-TEXT digests carried HTML entities —
  escaping now follows the file kind via a named callable
  (jinja's select_autoescape keys on the final extension).
- **B-143**: `services/erasure.py` — ONE deletion plan
  (group_id seeds + FK fixpoint) serves export and erasure.
  Export ZIP: domain JSON + iCal + files; secrets withheld by
  column rule; integration configs export key NAMES only.
  Erase door: owner + live person session (keys refused) +
  exact-name/ERASE double confirmation; commits, then sweeps
  storage objects no other group references, then VACUUM —
  the raw DB file greps clean. Audit keeps only the fact.
  DRILL-FOUND FIX (B-146): the vacuum's output itself rides
  the WAL, and on newer SQLite the raw main file still shows
  the pre-vacuum image afterwards — erase now checkpoints
  AGAIN after VACUUM so the on-disk file is the vacuumed
  image (the drill's restored tree failed the grep 5/5
  until this landed; the main tree's older SQLite had been
  masking it).
- **B-144**: `GET /users/me/data` (services/me_data.py — no
  hashes by construction), sources/permissions listing, and
  retention (`services/retention.py`): `retention_sweep` job
  chain anchored by the settings pack (03:30 group-local);
  expiry hard-removes WITHOUT a tombstone, stamps candidates'
  `source_deleted_at`, spares approved events. Privacy routes
  register BEFORE sources in main.py — the literal
  `sources/permissions` segment must not be swallowed by
  `{source_id}`.
- **B-145**: prod refuses known-default + <32-char secrets
  (config.py denylist); access log redacts the segment after
  feed/invites/email-in/source (`main._redact_path`).
  FINDING (fixed): httpx/httpcore INFO lines log full URLs —
  quieted to WARNING in `_configure_logging`. pip-audit over
  the uv-exported lockfile: **no known vulnerabilities**
  (verbatim output in TEST_REPORT's Phase 13 addendum).
- **B-146**: `scripts/backup.py` (SQLite online backup +
  storage + sha256 manifest + RESTORE.md), `scripts/restore.py`
  (verify-then-place; damaged bundle restores nothing),
  `scripts/restore_drill.py` (plant → backup → copy tree →
  restore → FRESH `uv sync` → full suite → API read-back;
  drill children run with VIRTUAL_ENV/PYTHONPATH scrubbed —
  the first drill run failed on the leak). Contract
  regenerated: 187 paths / 217 schemas (was 182 / 212).
  Suite at the gate: **835 passed, 0 failed** (797 + 38 new
  Phase 13 tests, reconciled; totals from the run's own
  report file). No ZIP this phase — folds into ZIP-Final
  at B-151.

### Phase 14 — Submission freeze and demo proof (B-147 → B-151): ✅ COMPLETE (2026-10-25)
- **B-147 demo data, final** (`services/demo.py`, `seeds/demo_story.json`,
  `demo.sh`): the seed is the story — Riverbend Community Trust
  (nonprofit) + Northside Hiking Club + the B-010 demo group, a cast
  of 7 people (seed loader grew an idempotent `memberships` section),
  the club's 40-message trip argument as a chat_paste source, the
  Trust's 12-section newsletter source, a pinned board announcement,
  and 8 published history events (Oct–Nov 2026). Idempotent by
  natural key (group + title); `scripts/seed.py --demo` loads the
  story; second load creates nothing (pinned by test).
- **B-148 demo script → tests** (`tests/test_demo_flows.py`): Flow C
  (golden newsletter → 12 drafts → re-extraction supersedes, never
  doubles → reviewer date edit + approve → agenda) and Flow D
  (Saturday-night tonight view, pinned first, tomorrow excluded).
  Flows A/B already stand as the Phase 5 / Phase 7 gate tests.
- **B-149 numbers page** (`scripts/make_results.py` → `RESULTS.md`):
  generated from the three harnesses over their shipped corpora;
  a test asserts byte-identical regeneration and that the honest
  imperfections are quoted (place 0.50/0.50, cost recall 0.00,
  vision overall 0.8125, pruning precision 0.94).
- **B-150 submission pack** (`submission/` — README cover,
  MODEL_MAPPING, ARCHITECTURE, RUN, WAIVER_AND_SCOPE): the
  backend's half of the submission. The model page leads with
  its honesty (no real Meta endpoint exists in this
  environment; the live driver is stub-transport tested only;
  every shipped number ran on fixture/mock). The architecture
  page is one Mermaid diagram + a walking tour derived from
  THIS map. The waiver states the scope in quotable words:
  only the jointly built work is submitted; Samtal, Mythic
  Engineering, and Project Aesir are pre-existing and NOT
  submitted — the sole carve-out is the Phase 11 Samtal
  integration surface as it exists in this repo. Licence in
  its ruled words (GPLv2, decided — the roadmap text's
  superseded licence notice was NOT used). The honesty notes
  ride inside: Bruno = B-010 skeleton, B-131 live smoke NOT
  RUN, B-133 real-inbox = DEPLOYMENT.md checklist item.
  `tests/test_submission_pack.py` (8 tests) parses the pack
  B-139-style so its claims cannot drift.
- **B-151 freeze drill + ZIP-Final**: `scripts/make_release_zip.py`
  now ships RESULTS.md top-level (§8 anatomy since B-149) and
  carries each file's Unix mode. **ZIP-Final built**:
  releases/gpa-backend-v0.5-2026-10-25.zip (§8 anatomy; the
  drill log ships inside at `gpa-backend/FREEZE_DRILL.md`).
  The drill ran FOUR times against script-built candidates
  and caught two real defects, both fixed at the root:
  (1) **ZIP permissions** — writestr wrote every entry 0644,
  so `demo.sh`/`run.sh`/`test.sh` landed non-executable after
  unzip; ZIP-Beta + ZIP-RC share the flaw (Final supersedes
  them; workaround there is `sh demo.sh`). `_zip_info` carries
  mode + mtime in both build shapes; `tests/test_release_zip.py`
  (3 tests) pins it. (2) **Cold-tree database** — a clean
  unzip has no `data/`, SQLite refuses a file whose parent is
  missing, and the seed meets the DB before storage/outbox
  creation can make the dir as a side effect (the server was
  always silently rescued by that side effect):
  `Database._ensure_sqlite_parent_dir` (src/gpa/db.py) +
  a foundation regression test. Run 3 failed only in the
  drill script itself (read back through a route that does
  not exist; corrected to `/users/me/data`). **Run 4 green
  end to end**: unzip 0 s, fresh `uv sync` 2 s, seed 20 s
  (7 users / 3 groups / 2 sources / 8 events / 1 announcement),
  boot + /health 4 s, login as Mara, story read back — 27 s
  cold. The drilled candidate (from `d951f81`) and the
  shipped ZIP differ only in the gate papers + the drill log.
  Process find, recorded in TEST_REPORT: the permission
  guard's first commit landed red (a 1970-timestamp test
  defect, masked by a `tail` pipeline's exit code); fixed in
  the next commit and all gate numbers re-run from the
  corrected tree. Suite at the gate: **853 passed, 0 failed**
  (841 + 12 new, reconciled). Contract unchanged: 187 paths /
  217 schemas.

### Phase 15 — Deployment and operations (B-152 → B-156): ✅ COMPLETE (2026-10-25)
- **B-152 PostgreSQL promotion** (`4ca7caa`): D-06 executed.
  `psycopg[binary]` is a runtime dependency (a Postgres deployment
  needs its driver at runtime). `scripts/migrate_to_postgres.py`
  moves a SQLite deployment to Postgres: refuses wrong directions
  and non-empty targets in plain words, builds the target schema
  through Alembic (env.py now honours
  `config.attributes["database_url"]`), copies in FK order, and
  verifies every table's row count AND a SHA-256 content hash.
  All PKs are UUID strings — no sequences to resync (checked, not
  assumed). Erasure's compact is dialect-branched (SQLite keeps
  checkpoint→VACUUM→checkpoint; Postgres runs `VACUUM (FULL)`).
  **Parity is real**: `tests/test_postgres_parity.py` (4 tests)
  runs against a live PostgreSQL 16.2 server (binaries from the
  `pgserver` pip wheel, a throwaway cluster under
  `~/workspace/.verify-tmp/`, user `pgdev`, port 54329), and
  conftest's PG mode (`GPA_TEST_PG_URL`) runs the whole suite
  against one scratch database; SQLite-file tests are named in
  `PG_FILE_TESTS` and skip there with pointers to where their
  guarantee is re-proven. **Defect found by the parity run**:
  group erasure deadlocked on Postgres — the request session's
  storage-sweep read transaction held locks the compact
  connection's `VACUUM (FULL)` needed (pg_stat_activity showed
  the pair). `erase_group` now ends the session's transaction
  before compacting.
- **B-153 native service packaging** (`f24c5a2`): `deploy/` —
  three systemd unit templates (API/worker/backup) + daily
  backup timer + logrotate snippet + `install.sh` (dedicated
  user, uv venv from the lock, env file generated at 0640
  root:service-user and never overwritten, units enabled but
  NOT started). No Docker (D-24). Rehearsed for real into a
  throwaway prefix: env proven 0640, units pass
  `systemd-analyze verify`, installed tree boots and answers
  /health. Pi-class and cloud-VM installs: NOT RUN HERE (no
  such machines reachable) — the exact invocation is in
  TEST_REPORT's Phase 15 addendum.
- **B-154 reverse-proxy + TLS guide** (`7e9b551`):
  `deploy/Caddyfile.example`, `deploy/nginx.conf.example`,
  `deploy/PROXY.md` — HSTS, body limit matched to B-141
  (10 MiB), SSE unbuffered, every secret-bearing path called
  out (feed/invite/email-in tokens, signed webhooks — HMAC
  over the RAW body). **Followed literally in a live test
  deploy** with real Caddy v2.11.7: /health 200 over HTTP/2
  with HSTS, an 11 MiB body refused 413 by Caddy itself, a
  feed request reaching the app while the access log records
  `calendar/feed/REDACTED` and the token appears nowhere.
  nginx: no binary obtainable here (apt has no network) —
  the example ships reviewed, live validation NOT RUN, stated
  in PROXY.md.
- **B-155 observability** (`22c08cd`): `services/health.py`
  deepens /health (database + engine, jobs shape with
  `stuck_running`, configured drivers as facts, a real storage
  probe; ok/degraded/down, down = 503 in the same body;
  `HealthResponse.checks` additive — contract still 187/217).
  Its own tests found and fixed a real gap: the jobs query was
  unguarded, so a schema-less database made /health 500
  instead of reporting down. And the phase gate reconciled
  the deepening with Phase 13's threat model: the deep
  checks are disclosed to signed-in callers only — anonymous
  /health keeps the minimal liveness surface its leak test
  pins (`9d8d7cd`; the gate's one failure, fixed forward). `scripts/requeue_stuck_jobs.py`
  is the operator fix for orphaned running jobs (the chassis
  only claims `queued` rows). `RUNBOOK.md`: the five real
  failures (mail down, AI quota, disk full, token expired,
  job stuck) with exact fixes + the JSON-log queries.
  `tests/test_runbook.py` reproduces all five from
  deliberately broken boxes (a real 1 MB tmpfs filled for
  disk-full; a token born expired — negative minutes, since a
  zero-minute token still validates inside its birth second).
- **B-156 upgrade path** (`fa09b2e`): `scripts/upgrade.py` —
  stage+build → backup (current tree's script, or the staged
  tree's when the install predates backup tooling — v0.4 has
  none; bundle hash-verified against the real B-146 manifest
  shape) → migrate the live DB → smoke the staged server
  against real data → swap (old tree kept as `.previous-*`,
  data/backups/.env carried) → serving venv rebuilt in place.
  Rollback = "restore the backup," printed every run. The
  **drill between the two real ZIPs (v0.4 → v0.5) passed**
  (upgrade 12.8 s; stamp none → `0016_phase13_privacy`; marker
  user/group intact and logging in through the new tree) and
  caught two boot-killers, both fixed at root: uv's default
  editable install is a `.pth` naming the staging path, and a
  venv's console-script shebangs are absolute — so staged
  venvs are built `--no-editable` (install.sh too) and the
  serving venv is born at its final address.
- Phase 15 gate: suite run on BOTH engines from the phase's
  final tree — **SQLite 877 passed / 4 skipped, PostgreSQL
  865 passed / 16 skipped, 0 failed on either, 881
  collected** (= 853 + 28 new, reconciled). The gate caught
  and fixed forward: the /health disclosure collision with
  Phase 13's threat model (`9d8d7cd` + snapshot regen
  `ce9028d`), the PG skip-list's missing pragma test
  (`9263ca4`), PG isolation vs module-scoped worlds
  (`f9ce3a2`), and Alembic's fileConfig silencing the
  app's loggers (`612ba7d`) — all recorded in TEST_REPORT's
  Phase 15 addendum.

### Later phases: NOT STARTED (per roadmap order)
Phase 16 begins at B-157.

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

### Phase 16 — Scale and second integrations (B-157 → B-160): ✅ COMPLETE (2026-10-25)
- **B-157 multi-instance jobs** (`6f7c7d2` + live-leg follow-up
  `e5826e4`): `jobs.claim_next` now scans up to 16 due candidates;
  a rowcount-0 lost race OR a database-level claim collision
  (rolled back) is a clean outcome — never an exception, never a
  double-run. Proven by `tests/test_job_race.py` (3 tests): four worker
  threads on separate sessions drain 24 jobs with an audit-row
  probe showing exactly one run per job, a barrier-synchronised
  claim storm on one job yielding exactly one winner, and a claim
  stepping over a head row another instance holds (a job made
  un-claimable by a running claim's uncommitted UPDATE on SQLite).
  `services/jobqueue.py` is the D-09 interface: `JobQueue`
  protocol, `DatabaseJobQueue` (the default, delegates to the
  chassis), `ArqJobQueue` behind it — arq is the optional `queue`
  extra; submissions translate kind + JSON payload +
  `_defer_until`/`_max_tries`; `run_gpa_job` dispatches into the
  SAME `jobs.HANDLERS` registry, and a handler failure raises so
  arq's retry owns it (the DB worker's semantics, translated, not
  forked). `gpa-arq-worker` is the second console entry
  (`GPA_JOB_QUEUE=arq` + `GPA_REDIS_URL` switch it on).
  **The live leg RAN**: no Redis is packaged here, so redis-stable
  was compiled from source in the verify sandbox (no Docker) and
  a throwaway copy of the tree (`uv sync --extra queue`)
  submitted through the REAL adapter → real Redis → real arq
  worker → the shared registry; the handler's effect landed
  exactly once (`arq:result` recorded). It caught two real
  defects the boundary tests could not: `create_worker` lives in
  `arq.worker` (not arq's top level), and the dispatcher must be
  registered under its wire name (`arq.func(..., name="gpa_job")`)
  or the worker reports the function not found. Both fixed in
  `e5826e4`.
- **B-158 second-wave sources** (`417fb1e`): Discord and Slack
  joined the B-126/B-127 framework as new DRIVERS — no framework
  change. `services/discord.py` / `services/slack.py` are client
  boundaries in the samtal.py shape (Protocol + recorded-fixture
  client + plain-words Unavailable client + process registry);
  the shared normalised message shape is unchanged, and Slack's
  ts-as-id mapping lives at its boundary. `CONNECTOR_KINDS` and
  `INTEGRATION_KINDS` gain the two kinds; `livechat._fetch_new`
  and `integrations._run_check` branch on them (the B-032
  permission record: discord needs `bot_token` in the record's
  config, slack needs `bot_token` too — a token-named key, never
  a pasted secret in code); the health board speaks to both.
  Live legs: NOT RUN (no Discord/Slack accounts may be connected
  here) — the exact later step is one `discord`/`slack`
  integration record with the bot token + connector check.
- **B-159 two-way Google, revisited** (`9cd8904`):
  `services/google_inbound.py` pulls Google-side edits as B-077
  change candidates — the B-124 Samtal pattern, never a direct
  write. The three-way comparison is the heart: outside copy vs
  our current payload vs the link row's last-pushed hash — equal
  to current means nothing happened; equal to the PUSHED payload
  means our side moved and the copy is merely stale (drafting
  would propose reverting our own edit); anything else is a
  genuine outside edit and drafts (author
  `system:google-calendar`, backing manual source, per-field
  provenance, undecided-duplicate fingerprint; a retitle is
  recorded in the source's words only — the B-077 draft shape
  carries starts/ends/place/cancelled). `POST
  …/integrations/{id}/google-pull` (organiser + publish) is the
  door; a copy deleted on Google drafts nothing (drift is
  B-137's story). Contract: **188 paths / 218 schemas**.
  Acceptance proven in `tests/test_google_inbound.py`: the edit
  waits, visibly, and a person's approval moves the event.
- **B-160 public API + developer pack** (`14bfd22`):
  `scripts/generate_sdk.py` generates `sdk/python/gpa_sdk.py`
  from the frozen contract — one method per operation (242),
  standard library only (urllib), `public_page`/`public_events`
  sugar, deterministic bytes (the suite regenerates and
  requires identity). `sdk/typescript/` is the `gpa-types`
  package: `index.d.ts` a byte-identical promotion of
  `frontend_types.ts`, `npm pack` proven by test; **registry
  publish NOT RUN** — publishing is the owner's step
  (`npm publish sdk/typescript`, after `npm login`).
  `DEVELOPER.md` is the pack's front door (versioning, RFC 9457
  codes, ETag/Idempotency, key self-service, scopes, the
  no-approve-scope law, the public surface's absent-not-nulled
  fields). `examples/public_events.py` — 16 lines — is the
  acceptance, run by the suite as a separate process against a
  REAL booted uvicorn server seeded over HTTP. **Defect found
  by the pack's own round-trip test**: API keys travel in the
  `X-API-Key` header, not as Bearer tokens — the SDK's first
  draft and the doc both said Bearer and were refused
  `token_invalid`; generator, SDK, and doc corrected at root,
  the header now pinned by test.
- **Phase 16 gate totals**: collected **908 = 881 (Phase 15)
  + 27 new** (job_race 3, jobqueue 7, second-wave 7,
  google-inbound 5, developer-pack 5). **SQLite: 904
  passed, 4 skipped, 0 failed** (the 4 skips are the parity
  tests standing down without an admin URL, by design).
  **PostgreSQL: 892 passed on the engine, 16 skipped, 0
  failed** — the full PG-mode run gave 888 passed / 20
  skipped, the 20 being the 16 named SQLite-file tests plus
  the 4 parity tests, which need their own
  `GPA_TEST_PG_ADMIN_URL`; run with it against the same
  tree and server they pass 4/4 (892 + 16 = 908). The first
  PG attempt errored wholesale (888 setup errors): the
  environment had reset and taken the cluster with it — the
  run was discarded, `pg-bootstrap.sh` rebuilt the cluster,
  and the gate was re-run from the top. Progress:
  **160/350 slices, Phases 0–16 complete.** Contract: 188
  paths / 218 schemas.

### Phase 17 — Modularity, internal encrypted API, resilience, craft (B-161 → B-180): COMPLETE (gate 2026-10-26)
- **B-161 module boundaries** ✅: `gpa/internal/` opens with
  `modulemap.py` (every services file placed in its §6A.3
  module; composition roots named; audit + crypto recorded as
  the shared common modules whose public functions are their
  surface) and `architecture.py` — an AST import scanner +
  import-linter-style checker. `tests/test_architecture.py`
  enforces: layering (models import nothing above the data
  layer; schemas never import services/routers; nothing but
  composition imports routers; storage never imports upward),
  a total ban on cross-file private imports, every service
  file mapped, and TWO exact pinned inventories (cross-module
  service edges: 115; router → model edges: 14) that may only
  shrink, in the same commit that removes an edge. Proven by
  a deliberately violating fixture (private import, two
  upward leans, an unpinned edge, an unmapped file — each
  named by rule, file, line) and a stale-pin test.
  **Evidence corrections this scan made**: (1) the B-017-era
  note "no router imports any model" is superseded — 14
  router → model edges exist (small projection fetches, e.g.
  B-140's members-only job view); they are pinned, not denied.
  (2) Two schemas imported services; fixed at root by moving
  the pure row helpers `etag_of` → `models/events.py` and
  `task_overdue` → `models/planning.py` (services/events keeps
  `etag_of` as a re-export for its routers). (3) Three private
  helpers were imported across files; promoted to public names
  in their owning modules: `vision.is_genuine_alt`,
  `matching.normalised`, `notifications.parse_hhmm`.
  Enabler committed alongside: the `cryptography` dependency
  (AES-256-GCM + Ed25519 for B-163/B-167; the roadmap names
  both algorithms, and neither exists in the stdlib; wheels
  ship for every Tier-1 target incl. Pi).
- **B-162 internal API core** ✅: `gpa/internal/{envelope,
  registry, client, handlers}.py`. Envelope = pydantic
  `{operation, caller, scope, payload, nonce, timestamp,
  idempotency_key, signature}`; payloads carry ids/values
  only, datetimes as ISO strings. Registry: operation →
  owner + required scope; static `CALLER_GRANTS` mirror the
  pinned edge inventory's meaning. Handlers run in the
  CALLER's session/transaction and preserve each direct
  call's commit discipline (job submission commits;
  emit/notify/candidates do not). Rewired through the
  client: events (4 emits + cancellation fan-out → one
  `notify.request` with an `event_id` audience), announce,
  rsvps (`notify.rsvp_promotion`), invites, reconcile
  (caller="integrations"), groups (stub draft →
  `candidates.create` over a new public
  `services/candidates.create_candidate`; mint emit),
  digests + retention chain links (`jobs.enqueue`),
  extraction (submits → `ai.submit_job`; pending emits),
  vision (submit), voice (transcribe submit,
  caller="sources"), google_inbound + samtal_calendar +
  memory (inline `Candidate(...)` → `candidates.create`).
  NOT rewired, deliberately: same-transaction `jobs.queue()`
  sites — the jobs module's in-process surface, not a
  cross-module call. Defect fixed at root: announce's audit
  line counted the removed `recipients` list (NameError
  under approval alerts) — it now counts the notify result's
  created ids, the people actually told. Architecture
  inventory 115 → 106, exactly the nine rewired edges. Tests:
  test_internal_api 9 passed; affected batches 91 + 85
  passed (change-alerts re-run 15 passed after the fix).
- **B-163 encrypted internal transport** ✅:
  `gpa/internal/crypto.py` (AES-256-GCM seal/open with the
  header as AAD; Ed25519 sign/verify; HMAC profile; comms key
  from `GPA_INTERNAL__KEY_B64` or domain-labelled derivation
  from the app secret), `gpa/internal/ca.py` (internal CA:
  mint CA + server/client leaves, loopback SANs),
  `gpa/internal/transport.py` (length-framed wire form —
  header in clear as authenticated data, payload sealed,
  whole message signed; `WireServer` refuses unsigned/forged
  frames BEFORE dispatch and signs even its refusals;
  `UnixWireServer`, loopback `TcpWireServer` + mTLS contexts,
  `WireClient`). Root addition in client.py:
  `execute_envelope(db, envelope) -> InternalResult` — both
  doors now share the one `_dispatch` (registration, scope,
  handler), so the law cannot differ by door. Acceptance
  evidence (tests/test_internal_transport.py, 6 tests): a
  recording TAP between a real client and server on a real
  Unix socket holds NO readable payload content in either
  direction while the call succeeds; an unsigned hand-made
  frame is refused pre-dispatch; the round trip also runs
  across a REAL subprocess boundary
  (tests/internal_fixture_server.py) and over loopback mTLS
  (a wrong-CA client leaf cannot complete the handshake).
  NOT RUN: Windows named pipes (Linux builder) — the framing
  and wire form behind the pipe are these same classes; the
  pipe leg itself is unproven here and the papers will say
  so.
- **B-164 service identities + replay protection** ✅:
  `gpa/internal/identity.py` (per-module Ed25519 identities,
  key ids = SHA-256 fingerprint prefix; custody = an
  identities directory of `<name>.seed`/`.pub`(+`.pub.prev`
  overlap) or, dev posture, HMAC derivation from the app
  secret — distinct key per module either way; scopes ARE
  the registry's grants, never duplicated) and
  `gpa/internal/replay.py` (5-minute window + seen-nonce
  store, `internal_replayed`; in-memory per door process —
  the restart-within-window residual is documented in the
  module, not hidden). The door now binds envelope caller to
  the authenticated signing identity, and `Channel.verify`
  returns the matched key id under rotation overlap.
  Acceptance (tests/test_internal_identity.py): impersonation
  refused at three layers (forged signer name → bad
  signature; honest signer + lying caller → binding refusal;
  own name + wrong scopes → scope denial), captured-message
  replay + stale timestamp refused, rotation overlap loses
  nothing (old key lands mid-rotation, refused after, new
  key works throughout). B-163's fixtures were aligned to
  sign as their true caller ("events") — the binding's first
  catch was our own test data.
- **B-165 internal API audit** ✅: `gpa/internal/auditing.py`
  — the client's audit hook (the slot B-162 reserved) writes
  one B-019 line per dispatch: actor `module:<caller>`,
  action `internal.<operation>`, detail = exactly {caller,
  key_id, operation, scope, module (owner tag), result}
  — never the payload; entity_id = the envelope nonce;
  group_id when the payload carries one (an id, not
  content). Installed by all three composition roots
  (main.create_app, worker.main, arq_worker.main) inside the
  caller's transaction, so story and act commit together.
  Acceptance (tests/test_internal_audit.py, 4 passed): the
  story reconstructs from audit alone in order, refusals
  leave their line with the refusal's code, and a seeded
  distinctive title appears in NO audit line in the stream.
- **B-166 in-process profile, honestly** ✅: the internal
  client's `wire` mode — a client with a WireClient attached
  seals+sends instead of dispatching locally, so the split
  profile is a composition choice and no module's call site
  changes; the server door's `execute_envelope` carries the
  split transaction law (per-call session: commit on
  success, roll back on refusal). Job payloads at rest are
  envelope-encrypted: `services/jobs.py` seals every payload
  (`enc:v1:` + AES-256-GCM under the internal key, AAD bound
  to the use) at `queue()`, at the arq adapter's Redis
  boundary, and on payload stamps; `payload_of` is the one
  reading law and tolerates legacy plaintext rows by prefix.
  DEFECTS FIXED AT ROOT: (1) two key sources — queue() sealed
  under env settings while process_one opened under the
  app's explicit settings, making sealed payloads read as
  tampering wherever Settings are constructed rather than
  env-read; the chassis now takes composition-injected
  settings (the mailer/outbound pattern: create_app, worker,
  arq_startup all inject), one key source per process. (2)
  reconcile's new except clause referenced AppError without
  importing it. Tests moved to the reading law where they had
  read raw columns (digest/reconcile/rsvps/jobqueue).
  Acceptance (tests/test_internal_profiles.py, 11 passed):
  the same probes pass in BOTH profiles unchanged; a copied
  database yields no readable job payload; legacy rows read.
- **B-167 field-level encryption** ✅: `gpa/crypto/fields.py`
  — master key (env `GPA_MASTER_KEY_B64`, never in the DB;
  dev derivation fallback) wraps per-group random data keys
  (`group_keys` table, migration 0017); fields seal as
  `enc:v1:` + AES-256-GCM with the seal bound to group +
  field name; a keyed HMAC blind index rides for lookup
  fields. Adopted by the rawest content: source chunk text +
  sender names (sealed in sources/transcribe/vision/
  tombstone-restore writers, opened in the list_chunks
  funnel via set_committed_value so no flush can write
  plaintext back), provenance quotes (add_provenance seals;
  candidates/events/merging/tombstone-export read through
  open_quote; merging dedups on OPENED quotes — seals are
  nonce-random and never match themselves), and
  event-source-link quotes (opened+resealed at mint, never
  copied sealed-to-sealed; the events router opens for
  projection). The group export opens all three at assembly
  (the archive is the owner's readable data). Opening never
  creates key material: a sealed value naming a keyless group
  is tampering. DEFECTS FOUND BY THE REGRESSION BATCH, fixed
  at root (B-166 completeness): vision's payload stamp,
  retention's payload reader, the jobs router's projection,
  and erasure's job attribution (a SQL LIKE over payload_json
  matches nothing under sealing — jobs of an erased group
  would have survived; attribution now opens payloads).
  Deliberate new architecture pins, with reasons: candidates→
  sources, merging→sources, transcribe→sources (quote/chunk
  sealing law lives with the data's owner; writes ride the
  caller's transaction), stream→jobs (payload reading law),
  erasure→jobs (payload attribution) — inventory 106 → 111.
  Acceptance (tests/test_field_encryption.py, 6 passed): raw
  DB+WAL grep finds no seeded secret/name/text while the API
  serves them whole; wrapped keys distinct per group, data
  keys nowhere in the file; cross-group/cross-field opens
  refused. NOT yet sealed (service is field-generic; named
  next adopters): candidate draft fields and discussion text.
- **B-168 encrypted file storage** ✅:
  `gpa/storage/encrypted.py` — a B-007 driver wrapping the
  local driver: every object sealed with a fresh per-file
  key (AES-256-GCM), the file key wrapped under the master
  key and carried in a `GPAE1` header inside the object;
  reads open transparently; legacy unsealed objects read as
  themselves. LocalStorage gained `put_at` (key-addressed
  atomic write) so the sealed blob rests under the
  PLAINTEXT's content address — keys, dedup, and the
  erasure shared-object law all survive. Composition:
  `GPA_ENCRYPT_STORAGE` via build_storage in all roots +
  the storage registry default. DEVIATION FROM THE ROADMAP'S
  LETTER, recorded: file keys wrap under the MASTER key, not
  the group key — the store is content-addressed and shared
  across groups by design (one object, several owner groups;
  B-143 keeps it while any owner lives), and a group-wrapped
  key would make a shared object unreadable to its other
  owners. Acceptance (tests/test_encrypted_storage.py, 5
  passed): raw-disk grep finds neither a planted text file's
  words nor a picture's bytes; the API-path upload seals on
  disk and serves whole; wrong master opens nothing.
- **FULL-SUITE CHECKPOINT (after B-168)**:
  `~/workspace/.verify-tmp/p17-full-gate1.log` — 960
  collected: **955 passed / 4 skipped / 1 failed in 39m18s**.
  The one failure was this map's author's own B-167 test
  asserting the migration head is literally 0017 — stale
  the moment 0018/0019 landed; the test now asserts against
  the script directory's live head. Phase 17's first eight
  slices are full-suite green.
- **B-169 key management + owner recovery** ✅:
  `gpa/services/keymgmt.py` + `KEY_MANAGEMENT.md` (the
  hierarchy in one page). Master rotation = re-wrap only
  (one transaction, lands whole or not at all; job
  `crypto.rotate_master`, old key from
  GPA_MASTER_KEY_OLD_B64, operator-set, never resting).
  Recovery: RecoveryEscrow rows (migration 0018) hold the
  master sealed under per-code scrypt keys; codes are
  shown once and rested NOWHERE; the row-finding check is
  keyed by the APP SECRET precisely so it can be computed
  on the day the master is lost; minting deletes the old
  set (mint = revocation); a code spends once. Acceptance
  (tests/test_key_management.py, 4 passed): mint → lose →
  restore round trip (sealed field opens again under the
  recovered master); rotation re-wraps (version bump,
  sealed data untouched; wrong old key stops whole); the
  REAL B-146 bundle script's output greps clean for the
  master, a data key, and all eight codes, raw and base64.
  Migration 0019 (audit chain schema) landed in this commit
  with its migration test; the chain itself wires at B-170.
- **B-170 tamper-evident audit chain** ✅: AuditLog gains
  prev_hash/row_hash; AuditChainHead (id=1) holds the tip,
  extended inside each writer's transaction by
  audit.record's new flush-and-hash step (flush, never a
  commit — change and audit entry still land or roll back
  together). Hash = HMAC-SHA256 keyed by a chain key
  derived from the app secret (set_chain_secret in all
  composition roots). `gpa/services/audit_chain.py`
  verifies by LINK WALK: follows prev-hash links from
  GENESIS (or a uniquely re-anchored start), recomputes
  every row, requires the walk to consume exactly the
  hashed rows, and checks the head — edits, deletes,
  forged inserts, forks, and tail cuts each fail AT the
  wound with the row named. Endpoint:
  GET /groups/{id}/audit/verify (admin of any group; the
  chain is instance-wide). Acceptance
  (tests/test_audit_chain.py, 5 passed): ordered links
  verify; edited row fails naming the row; deleted row
  breaks; forged insert detected; endpoint answers the
  same verdict. Audit-heavy regression batch: 62 passed.
- **B-171 durability manager** ✅: (a) commit-before-answer
  made STRUCTURAL — `gpa/durability.py` WriteTracker
  (flush listeners mark uncommitted writes; commit/rollback
  clear) rides request.state from get_db; the create_app
  middleware answers a success response carrying uncommitted
  writes with 500 `uncommitted_writes` after rolling them
  back. Error responses pass untouched. (b) Draft autosaves
  (migration 0020): PUT/GET
  /groups/{id}/autosaves/{candidate|event}/{id} with
  base_version optimistic concurrency — a stale save lands
  as a kept conflict copy, answered 409 draft_conflict with
  both versions. (c) Resumable chunked uploads:
  POST/GET/PUT parts/POST complete under
  /groups/{id}/uploads — parts in any order, each size-
  verified against its position (offset law), resting as
  files outside the content store; completion assembles,
  verifies the whole-file SHA-256 (mismatch → 422, session
  stays open), and hands the bytes to B-025's ingest_file.
  Acceptance (tests/test_durability.py, 4 passed): the
  forgetting-handler probe (500 + writes gone; committed
  control persists; a refuser's own error stands), the
  conflict rehearsal, resume-after-kill across two app
  instances, hash-mismatch refusal. Also in this commit:
  the modulemap placements B-169/B-170 owed (audit_chain →
  audit, keymgmt → crypto, uploads → sources, autosaves →
  platform) + the keymgmt→jobs handler-registration pin —
  architecture suite green.
- **B-172 startup recovery & self-healing** ✅:
  `gpa/services/recovery.py` assess_and_repair — verdicts
  serve / repaired / refuse(with the manual repair named).
  Repairs: WAL checkpoint (frames brought home are
  reported), stuck jobs (running past the 900 s lease →
  requeued with attempt counted or failed when spent, each
  audited as system:recovery), orphan upload parts (open
  sessions' parts are the resume contract — never swept;
  completed/ghost sessions' parts are), migration state
  (an alembic_version naming an unknown revision refuses;
  an unstamped create_all database is noted, not refused).
  The worker runs it before its first job and exits 2 on
  refuse. Acceptance (tests/test_startup_recovery.py, 5
  passed): the DOUBLE-VERDICT GUARD (healthy ground scores
  serve twice, zero actions), stuck-job requeue exactly
  once then serve, spent-job failure, orphan sweep bounds,
  unknown-revision refusal naming the repair.
- **STOPPED after B-172** (this builder's run): B-173 →
  B-180 remain for the timer crew, in order. Phase papers
  NOT written (B-180 not reached). Full-suite gate for
  B-169→B-172 stands on their affected batches (all green,
  named in the entries above); the next full gate + PG
  parity belong with the B-180 drill.
- **B-173 error-correction services** ✅ (timer crew,
  2026-10-25): `gpa/services/resilience.py` — one circuit
  breaker + one bulkhead per external dependency (meta_ai /
  mail / facebook / google; threshold 5 consecutive failures,
  60 s cooldown, one half-open probe, 4 in flight per
  process; state is per-process memory, reset at every
  composition — a fresh process re-probes the world).
  Enforcement is the job chassis: `jobs.process_one` takes
  the dependency's permit (bulkhead first, so a full
  compartment never spends the probe slot) for the mapped
  kinds (notify/digest_run → mail; extract/vision/transcribe
  → meta_ai); no permit = DEFER — job back to queued with the
  cooldown as its run time, attempts untouched, plain-words
  reason on the row. Real outcomes settle the breaker.
  `resilience.call` is the same door for inline callers.
  `gpa/services/repair.py` — the `data_repair` job: recomputes
  derived truths from the rows that own them (done-job
  progress, chunk positions 0..n-1, flyer alt_text_status via
  vision.is_genuine_alt), removes provenance pointing at
  chunks/candidates that no longer exist (event source links
  deliberately untouched — B-023 tombstone law), re-renders
  empty-bodied digests through compose_digest for their own
  week in place; ONE audit row (repair.completed) lists every
  fix; a second run finds nothing. `DEGRADATION.md` (repo
  root) is the per-dependency matrix: queued, never silently
  lost, never a stack trace. Acceptance
  (tests/test_error_correction.py, 10 passed): parametrized
  kill-each-dependency (breaker opens, refuses without
  calling, other breakers stay closed), half-open probe
  decides both ways, bulkhead refuses only its own
  dependency, mail outage defers notify jobs attempt-free
  while data_repair runs to done mid-outage, Meta outage
  defers extraction while mail sails, planted-damage repair
  (orphans planted through a raw FK-off engine — the lax
  road that creates them) fixes forward + audits + is
  idempotent. Architecture pins 111 → 116 with reasons
  recorded (jobs→resilience the chassis' own gate;
  repair→digests/jobs/vision recompute-never-reimplement;
  jobqueue→repair registration). **Defect found in earlier
  code, fixed at root**: `services/voice.ingest_voice` never
  committed — it rode on the job submission's commit, and
  since B-165 the internal call's audit row lands after that
  commit, so B-171's guard answered voice uploads 500
  `uncommitted_writes` (tests/test_stream.py ×3, red on
  pristine HEAD too — the B-169→B-172 batches never ran it).
  The service now commits its own transaction; stream +
  voice + transcription + durability suites green (15
  passed). Regression batch for the chassis change (jobs,
  job race, jobqueue, notifications, digest, extraction
  jobs, inbox, rsvps, internal profiles): 49 passed + the
  same 3 stream failures, all pre-existing, all fixed by
  the voice commit above. Progress: **173/350**.
- **B-174 advanced account security** ✅ (timer crew,
  2026-10-25): `gpa/services/account_security.py` (identity
  module) + migration 0021. **TOTP MFA** hand-rolled RFC 6238
  on stdlib (HMAC-SHA1, ±1-step window; RFC test vectors
  pinned) — setup mints a secret stored SEALED via
  services/sealing (shown once, in the setup answer), confirm
  proves the app can read it before `mfa_enabled` flips, and
  enrolment's confirming step is deliberately NOT spent (the
  person may re-sign-in inside the same 30 s). Password
  sign-in for an MFA account mints NO session: it earns a
  5-minute single-use `mfa_challenge` one-time token, answered
  at POST /auth/login/mfa; a mistyped code does not burn the
  challenge, a correct one spends its TOTP step — the same
  code replayed against a fresh challenge is refused
  (`mfa_last_step`). Owners/admins see `offer_required` on
  GET /users/me/mfa (the roadmap's mandatory-OFFER; taking it
  up stays their act). Disabling MFA (current code required)
  revokes every session. **Password policy**: ≥12 chars + a
  LOCAL breached/common list (the roadmap's offline option —
  no password fragment ever leaves the machine to be checked)
  + not-the-email-name, enforced at register / reset /
  change (`password_too_weak` 422); the seed road keeps its
  direct-hash discipline and is untouched. **Progressive
  lockout** on the User row: 5 consecutive failed password
  sign-ins lock for 1 min, doubling per further failure,
  capped 1 h (`account_locked` 423, plain words naming the
  wait); success clears the count. **Session list**:
  RefreshToken gained the birth device words (user-agent/IP,
  carried forward across rotation — one family is one
  device); access tokens now carry a `fam` claim (a label,
  never a credential) so GET /users/me/sessions can mark the
  current device; DELETE /users/me/sessions/{family_id}
  signs out exactly that device (another's family = 404).
  **Login anomaly**: issue_session records the (IP,
  user-agent) pair in `user_devices` in the session's own
  commit; a NEW pair for a person with known devices writes
  an outbox notice + `user.login_new_device` audit row.
  Acceptance (tests/test_account_security.py, 10 passed):
  RFC vectors, policy (short + breached), full MFA flow +
  code single-use + challenge single-use, setup-not-yet-
  protection, owner offer, disable-revokes, session list +
  per-device revoke, lockout + lift + counter reset,
  new-device notice exactly once — and the slice's named
  guarantee: **a stolen refresh token still dies by B-013's
  rotation rule with MFA on** (replay revokes the family,
  newest token included). **Boundaries recorded plainly**:
  magic-link sign-in remains a full sign-in with MFA on (the
  link proves mailbox control, itself a factor; the challenge
  guards the password road); WebAuthn/passkeys are the
  roadmap's post-window stretch and are NOT built. Contract
  regenerated: **201 paths / 229 schemas** (188/218 at B-160 —
  the delta is B-174's 7 routes plus the Phase 17 routes
  B-169→B-173 had not frozen); SDK regenerated with it (256
  operations). Full suite (basetemp on the overlay, totals
  from the run's own log): **994 passed, 4 skipped, 1 failed**
  — the one failure was the stale generated SDK against the
  new contract; regenerated, its batch passes (developer
  pack + OpenAPI snapshot, 6 passed). No other test was
  touched by the fix. Progress: **174/350**.
- **B-175 crash-correction test harness** ✅ (timer crew,
  2026-10-25): fault injection as a first-class suite.
  `gpa/services/chaos.py` (platform module) states the crash
  invariant once, as code — no committed entry lost, no
  half-written entity visible, no interrupted job silently
  gone — and judges any database against it below the API
  (integrity + foreign-key PRAGMAs, ready-source-without-
  chunks tear probe, running-job census). The drivers:
  `tests/chaos_worker.py`, a subprocess that reaches a named
  kill point in one roadmap flow and then SIGKILLs itself
  (marker file proves the point was reached by the road
  intended, exit is -SIGKILL, never an early error) — ingest
  after commit (whole source + 3 chunks survive), ingest
  mid-transaction (nothing visible), extraction and digest
  jobs claimed-then-killed (B-172 recovery requeues with a
  plain-words record; the resumed job then runs to done
  under a probe handler, restored afterwards), approval
  mid-transaction (candidate stays pending, NO event
  minted — the flip and the mint are one transaction or
  neither), upload mid-session (session stays open, landed
  part byte-intact, the missing part resumes and the
  assembled whole matches the session's SHA-256). The
  in-process legs inject disk-full at the storage boundary
  (ENOSPC inside the ingest transaction → rollback, no
  partial source) and a read-only database (creator-based
  `mode=ro` open — the pysqlite driver ignores `?mode=ro`
  in the URL string, found by the first run; writes refuse,
  committed rows read whole). **The 100-run pass**:
  `run_inprocess_chaos(seed=175)` draws seeded-random faults
  over the five kinds (commit 18, rollback 17, job 17,
  diskfull 19, readonly 29), one fresh deployment per run
  (schema template copied per run — create_all ×100 made
  the first pass take ~9 minutes; the copy brings the whole
  file to ~80 s), recovery first, invariant judged after
  every fault: **100/100 hold**. `scripts/chaos_report.py`
  re-runs legs + loop as an operator road and writes
  `CHAOS_REPORT.md` (6/6 legs, 100/100 runs — quoted in
  TEST_REPORT's Phase 17 addendum, the ZIP's test report).
  Power-loss profile, stated honestly: a SIGKILL inside an
  open transaction is the same event as a power cut for
  committed rows under WAL + synchronous=FULL (B-003), so
  the mid-transaction legs ARE the fsync-window legs; no
  VM/container rig is used (no-Docker law). Tests:
  test_chaos 7 + architecture/recovery/durability batch —
  19 passed in the targeted run. Progress: **175/350**.
- **B-176 comment + documentation gate** ✅ (timer crew,
  2026-10-25): `scripts/comment_gate.py` — a stdlib-only AST
  checker, importable (`check_tree`) and runnable as a CLI;
  it runs inside `test.sh` through `tests/test_comment_gate.py`
  (test.sh IS the pytest suite). HARD rules, one breach fails:
  every Python file under src/gpa + migrations/versions +
  scripts opens with a purpose header (module docstring or
  leading comment); every migration carries a why-docstring;
  every prompt template carries its name+version front-matter
  (B-038's discipline); every `.env.example` setting block is
  comment-introduced; no docstring may merely restate its
  name (§6A.1 counts that AGAINST a module). RATCHET floors
  per module — function/class docstring coverage + documentation
  density (docstring + comment lines as a share of all lines):
  measured on this tree at the gate's landing, rounded down to
  the nearest five points, and only ever rising (services
  75/95/20, routers 85/60/15, schemas 55/70/20, models
  95/95/40, migrations 10/0/15, scripts 40/0/15, the rest in
  the checker's FLOORS table); a new module with no floor
  fails until it is measured and floored. Every individual
  missing docstring (512 at landing — mostly small private
  helpers, `__init__`s, and protocol overrides whose contract
  docstring lives on the protocol) is REPORTED by file and
  line in the gap list: §6A.1's own division of labour —
  "the gate enforces presence and floor; human review
  enforces quality" — with 100% per-function coverage as the
  standard the floors climb toward, recorded here as the
  slice's honest deviation from the roadmap's every-function
  letter rather than papered over. Acceptance
  (tests/test_comment_gate.py, 6 passed): the real tree
  passes its own gate; a deliberately stripped fixture fails
  with its class/method/function gaps named by file and line
  (Widget:3, spin:4, build_widget:8 in the fixture); each
  hard rule also fails alone (bare migration, headerless
  prompt, unexplained setting block, restating docstring).
  Regression batch with architecture + threat model +
  submission pack: 23 passed. Progress: **176/350**.
- **B-177 cross-platform verification matrix** ✅ (timer crew,
  2026-10-25): `scripts/platform_audit.py` — the §6A.2 law as
  tooling (pathlib-only path building, explicit `encoding=` on
  every text read/write, no `sys.platform`/`os.name` branching
  in domain code; URL joins pinned with reasons — main.py's
  log-redaction join, the webcal/https builders). Its first
  runs earned their keep: three `read_text()` calls in
  `internal/identity.py` gained explicit UTF-8, and four
  apparent `open()` findings proved to be Pillow/urllib doors
  (the audit now scopes to the bare builtin). Fixtures
  (`tests/test_platform_matrix.py`, 9 tests): audit green on
  the tree + fails an unportable fixture by file/line; BOM +
  CRLF + non-ASCII round-trips (pinning the universal-newline
  law text reads apply on every OS); DST fixtures from
  zoneinfo (the lockfile already carries `tzdata` for
  Windows); loopback TCP everywhere + the AF_UNIX door probed
  where offered; filesystem case-sensitivity PROBED, with the
  defences asserted (lowercase-hex content keys, storage
  depth < 80 chars against MAX_PATH). CI matrix
  (`.github/workflows/test.yml`) runs the identical suite on
  ubuntu/windows/macos. **Named waivers** in
  `PLATFORM_MATRIX.md`: the Windows and macOS columns are
  CI-defined but NOT RUN here (no such machines reachable;
  the manual real-Windows run stays NOT RUN), Windows named
  pipes stay unproven (B-163's record; mTLS door carries
  Windows), MAX_PATH + case-insensitivity defended by
  construction. Progress: **177/350**.
- **B-178 production-quality pipeline** ✅ (timer crew,
  2026-10-25): ruff, mypy, pytest-cov, pre-commit added as
  locked dev dependencies. **Enforced today**: ruff E9+F
  (syntax + pyflakes) on `src/gpa` at ZERO — 33 findings fixed
  at root, incl. a real undefined-name annotation
  (`InternalResult` in `internal/client.py`, now a top-level
  import) and dead imports across 20 files; the autofix's one
  casualty, the load-bearing `services.events.etag_of`
  re-export (routers call it), was restored with its noqa —
  the regression batch (events + internal suites, 51 passed)
  proves the tree still runs. **The ratchet** (B-176's
  pattern, in `scripts/quality_gate.py` + CI + pre-commit):
  `mypy --strict` over services/ai/internal/crypto stands at
  **722** (type-arg 391, no-untyped-def 171 dominate); the
  ceiling may only fall. **Named deviations** in
  `QUALITY_PIPELINE.md`: wider ruff families (1,189 findings,
  945 of them FastAPI's B008 Depends idiom) and `ruff format`
  (137 files) are NOT enforced — a mass reformat would bury
  the D-26 house style; coverage floors are measured at the
  B-180 full gate, none stated before measured, with the
  security/durability suites excluded from averages by
  policy. In-suite: `tests/test_quality_gate.py` (3 passed;
  the mypy half runs via the gate's --mypy mode, green at the
  ceiling). Progress: **178/350**.
- **B-179 signed releases + SBOM** ✅ (timer crew,
  2026-10-25): `scripts/release_signing.py` — SBOM
  (`sbom.spdx.json`, SPDX 2.3) generated from the committed
  `uv.lock` by the real TOML parser (every pinned package,
  exact version, per-artifact SHA-256 uv recorded; `uv sync`
  verifies those same hashes at install — the lockfile law,
  stated in the module). Train ZIPs (and the flat shape) now
  carry the SBOM inside, the release manifest names the
  signing key's fingerprint, and a detached Ed25519 signature
  (``<zip>.sig``) + public key (``<zip>.pub``) land beside the
  ZIP next to the existing ``.sha256`` sidecar. Key custody:
  the private key comes only from
  `GPA_RELEASE_PRIVATE_KEY_B64` / `GPA_RELEASE_KEY_FILE`
  outside the repo — an in-repo key path REFUSES the build,
  and `keygen` refuses to write inside the repo; with no key
  configured the build signs with an EPHEMERAL key and the
  manifest stamps `ephemeral: true` in plain sight (never a
  silent unsigned or pretend-signed release).
  `verify_release` checks, in order: sha256 sidecar, Ed25519
  signature over the ZIP's exact bytes, SHA256SUMS against
  every entry (no unlisted entries), SBOM package set ==
  the shipped uv.lock's set, manifest key_id == the key that
  verified. Acceptance (tests/test_signed_releases.py, 5
  passed): a real train ZIP built with a throwaway key held
  outside the repo verifies clean; **one flipped byte →
  verification refuses** (signature + sha256 problems named);
  in-repo key refused at both doors. Batch with release-zip,
  comment-gate and quality-gate suites: 17 passed. Progress:
  **179/350**.

- **B-180 Phase 17 gate — the addendum proof** ✅ (gate
  closed 2026-10-26, Yrsa): the drill
  (`tests/test_phase17_gate.py`, log in `PHASE17_DRILL.md`,
  shipped in the gate ZIP) walks one unbroken story —
  resumable-upload flyer in, worker dies mid-vision-job,
  startup recovery requeues, approval over the internal
  encrypted API, raw-disk greps clean, audit chain verifies.
  The drill found two root defects in earlier phases' code,
  both fixed in the gate commit `b0d7ec2` (chunked-upload
  completion refused image bytes; `Flyer.transcript` rested
  in plaintext — now sealed). The gate then found three
  more, all repaired at root before the numbers below were
  taken: four SQLite-bound Phase 17 tests moved to
  `PG_FILE_TESTS` with three parity twins added
  (`0ed3dcd`); a **production** audit-chain head race on
  PostgreSQL — B-170's read-prev/hash/advance was
  serialised on SQLite only by the write lock — fixed with
  FOR UPDATE + nested-transaction head creation
  (`66f88fb`, concurrent writers 5/5, was ~1/2); and the
  PG per-test reset now drops `alembic_version`, ending
  B-172's future-stamp poisoning of later tests on a
  reused suite database (`50b15bd`). **Full suite, final
  tree `50b15bd`, collected 1,033 on both engines: SQLite
  1,026 passed / 7 skipped / 0 failed** (two declared
  back-to-back halves, 668 + 358 — two single runs died to
  environment resets mid-suite, zero failures to the cut);
  **PostgreSQL 1,006 passed / 27 skipped / 0 failed** in
  PG mode plus the parity file **7/7** against the live
  engine — **1,013 passed on-engine**; both engines
  reconcile (1026 + 7 = 1006 + 27 = 1033). PG environment
  rebuilt as a fresh initdb cluster from the preserved
  pgserver 16.2 binaries after the workspace-preserved
  data copy tore (missing `pg_filenode.map`). Named
  waivers, in the open in `PHASE17_DRILL.md`: second OS
  NOT RUN (Linux-only builder; B-177 matrix defines it);
  coverage floors unmeasured at this gate, left unstated
  rather than invented. Gate ZIP: `gpa-backend-v0.6-
  2026-10-26.zip` (signed, SBOM inside, drill log inside).
  Contract: **201 paths / 229 schemas**. Progress:
  **180/350, Phases 0–17 complete.**

## Standing laws in force here

- No Docker, anywhere, ever (native installs + uv only).
- All code is the team's own original work (D-46); third-party
  software enters only as installed, locked dependencies.
- Licence: **GPLv2** — "Licence: GNU General Public License
  version 2 (GPLv2)." Ruled by Volmarr on 2026-10-24 ("GPLv2 is
  the project liscense"), superseding the earlier undecided
  posture; local statements aligned by Yrsa at the Phase 9
  checkpoint (README line, pyproject licence field).
- Commit-before-answer (services commit, then routers respond);
  entered data is sacred (WAL + FULL).
- Heavy plain-words comments (D-26): header on every file,
  docstrings everywhere.
- Local git commits only. No remote, no push, ever, unless Volmarr
  says otherwise in so many words.
