# Changelog — Group Planning Assistant Backend

Per-slice list for the release train (roadmap §8). Slice titles
are the roadmap's own words; phases are grouped in build order.

## Breaking changes

- **None to the HTTP contract through B-112.** Every change has
  been additive (D-12/D-15): new endpoints, new optional fields.
- Internal (not HTTP): the `notify` job payload gained
  `notification_id` in Phase 8 — the outbox handler delivers the
  named Notification row. Jobs written by older code without one
  still run (they are recorded in the audit, in the old words).
- Behaviour: waitlist promotions (B-061) are now real
  notifications (inbox + email per the member's preferences),
  not audit-only records. RSVP responses are unchanged.
- Additive response fields: `GroupResponse` carries the Phase 8
  comms settings (`reminder_rules`, `digest_weekday`,
  `digest_time`, `digest_enabled`, `ai_reminder_wording`);
  `AnnouncementDraftResponse` carries `sent_at` / `sent_by`.
- Phase 9 behaviour: the candidate-approve door is membership
  now — who may approve, and how many distinct approvers a
  candidate needs, is the group's settings pack (B-111),
  enforced in the service; defaults (organiser+, one approver)
  reproduce the old behaviour exactly. `CandidateResponse`
  carries `approvals_recorded` / `approvals_required`
  (additive). Events and tasks gained `subgroup_id` scoping
  (B-105): a scoped item is absent (404) to members outside
  its sub-group on member-facing reads.
- **Phase 13 contract change (the phase's one deliberate
  break):** `SourceResponse` no longer carries `storage_key`
  (B-141, D-17). A stored file's content address is an
  internal locator: no route serves bytes by key, and the
  key was never the frontend's to hold. `file_asset_id`
  stays — an opaque id naming the asset record. Everything
  else in Phase 13 is additive: five new routes (group
  export, group erase, retention run, source permissions,
  /users/me/data) and two new settings dials
  (`upload_quota_bytes`, `source_retention_days`).

## Slices

### Phase 0 — Foundation (pre-build, Sun Oct 25) — B-001 → B-010

- **B-001** — Repo + tooling
- **B-002** — App factory + config
- **B-003** — Database chassis
- **B-004** — Error chassis
- **B-005** — Test chassis
- **B-006** — OpenAPI pipeline
- **B-007** — Storage interface
- **B-008** — Job chassis
- **B-009** — Seed chassis
- **B-010** — Demo skeleton + first ZIP

### Phase 1 — Identity, groups, and permissions (Mon Oct 26) — B-011 → B-022

- **B-011** — Users
- **B-012** — Magic links
- **B-013** — Sessions
- **B-014** — API keys
- **B-015** — Groups
- **B-016** — Memberships + roles
- **B-017** — Permission dependency
- **B-018** — Agent seats
- **B-019** — Audit log v1
- **B-020** — Account recovery + email change
- **B-021** — Rate limits v1
- **B-022** — Phase gate

### Phase 2 — Sources and ingest (Mon–Tue Oct 26–27) — B-023 → B-034

- **B-023** — Sources core
- **B-024** — Paste ingest
- **B-025** — File ingest
- **B-026** — Newsletter ingest
- **B-027** — Image ingest
- **B-028** — Board-photo ingest
- **B-029** — Link ingest
- **B-030** — Email-in ingest
- **B-031** — Voice-note ingest
- **B-032** — Live-chat source registration
- **B-033** — Ingest status + progress
- **B-034** — Phase gate

### Phase 3 — The AI adapter and the extraction engine (Tue–Wed Oct 27–28) — B-035 → B-048

- **B-035** — `MetaModelClient` protocol
- **B-036** — Fixture + mock drivers
- **B-037** — Live driver
- **B-038** — Prompt library
- **B-039** — The date resolver (D-22)
- **B-040** — Event extraction
- **B-041** — Multi-event splitting
- **B-042** — Task / decision / question extraction
- **B-043** — Candidates + review queue API
- **B-044** — Confidence + quality flags
- **B-045** — Extraction jobs + re-runs
- **B-046** — Evaluation harness v1
- **B-047** — Cost guardrails
- **B-048** — Phase gate

### Phase 4 — Events, calendar, and publishing out (Wed–Thu Oct 28–29) — B-049 → B-062

- **B-049** — Events core
- **B-050** — Event editing + change history
- **B-051** — Cancellation + clone
- **B-052** — Recurrence
- **B-053** — iCal engine
- **B-054** — Google "add to calendar" + webcal links
- **B-055** — Agenda + calendar queries
- **B-056** — Saturday-night view
- **B-057** — Conflict + impossibility flags
- **B-058** — Facebook Event drafts
- **B-059** — Public + member calendar views
- **B-060** — Event discussion (threads v1)
- **B-061** — RSVPs
- **B-062** — Phase gate

### Phase 5 — Flyers, vision, and boards (Thu–Fri Oct 29–30) — B-063 → B-074

- **B-063** — Vision reading pipeline
- **B-064** — Board segmentation
- **B-065** — Flyer transcript + alt text
- **B-066** — QR + URL extraction
- **B-067** — Generated event image (optional)
- **B-068** — Voice transcription driver
- **B-069** — Image job pipeline
- **B-070** — Flyer wall API
- **B-071** — Duplicate-flyer detection
- **B-072** — Print view data
- **B-073** — Vision evaluation set
- **B-074** — Phase gate

### Phase 6 — De-duplication, changes, and truth-keeping (Fri Oct 30) — B-075 → B-082

- **B-075** — Cross-source matching
- **B-076** — Merge service
- **B-077** — Change detection
- **B-078** — Re-announce service
- **B-079** — Contradiction flags
- **B-080** — Tombstones + undelete
- **B-081** — Data-quality report
- **B-082** — Phase gate

### Phase 7 — Planning: tasks, decisions, polls, checklists (Fri–Sat Oct 30–31) — B-083 → B-094

- **B-083** — Tasks
- **B-084** — Task comments + history
- **B-085** — Decisions log
- **B-086** — Open questions
- **B-087** — Polls
- **B-088** — Checklists + roles per event
- **B-089** — Budget-lite
- **B-090** — Availability finder
- **B-091** — Templates
- **B-092** — Search across planning
- **B-093** — Planning digest data
- **B-094** — Phase gate

### Phase 8 — Reminders, notifications, and digests (Sat–Sun Oct 31–Nov 1) — B-095 → B-104

- **B-095** — Notification core
- **B-096** — Reminder engine
- **B-097** — Change alerts
- **B-098** — Invites
- **B-099** — Weekly digest
- **B-100** — Notification preferences
- **B-101** — In-app inbox
- **B-102** — Deliverability records
- **B-103** — Reminder wording via AI (optional tier)
- **B-104** — Phase gate

### Phase 9 — Members, nonprofit pack, and resources (Sun Nov 1) — B-105 → B-112

- **B-105** — Sub-groups + chapters
- **B-106** — Announcements board
- **B-107** — Volunteer shifts
- **B-108** — Resources
- **B-109** — Attendance (aggregates only — no per-person reads exist)
- **B-110** — Member import/export
- **B-111** — Group settings pack
- **B-112** — Phase gate (no ZIP — folds into ZIP-RC at B-138)

### Phase 11 — Samtal bridge and live sources — B-123 → B-130

- **B-123** — Samtal source connector: a live source syncs a
  hall's new messages as transcript chunks, keyed on each
  speaker's outside id; speakers resolve to members ONLY
  through explicit account links (active members only,
  duplicates refused) — unlinked speakers stay named guests,
  proven against a name-collision trap. Built and tested
  against recorded fixtures and a test double; the live
  Samtal is never touched by this codebase's suite
- **B-124** — Samtal calendar return path: published events
  stand on the hall's own calendar; Samtal-side edits and
  cancellations come back as B-077 change candidates in the
  ordinary queue — the round-trip test walks out, edit,
  candidate, approval, and back, against the double
- **B-125** — Messenger source: access has not been granted,
  so the slice ships as the documented fallback — the
  paste/export ingest path (B-024), with the connector
  refusing sync in plain words naming that road. The
  fallback IS the acceptance test; nothing fakes a live
  Messenger integration
- **B-126** — Generic webhook source: per-source endpoint,
  URL token shown once and kept hashed, signing secret
  derived (never stored), HMAC over "<timestamp>.<body>",
  300-second replay window and exactly-once acceptance —
  a replay is a 409; refusals are audit-logged and archive
  nothing
- **B-127** — Integration framework: kinds, sealed-at-rest
  config (an HMAC-envelope in the house idiom; API reads
  show key names only), and recorded health checks. The
  acceptance grep over the raw database file finds no
  plaintext secret
- **B-128** — Outbound webhooks: the group's acts (event
  published/changed/cancelled, candidate pending) are
  announced to subscribed endpoints, signed with the
  inbound scheme, delivered by job with the failure record
  committed before the raise; deliveries inspectable, dead
  at the retry cap with the reason kept
- **B-129** — Agent API pack: the seat's working surface is
  tagged `agent` in the contract; the scope ladder is
  documented once; `examples/secretary_seat.py` runs in
  mock mode in the suite — reads a hall, drafts its plan
  line, and meets the approval refusal that defines a seat
- **B-130** — Phase gate: hall → sync → extraction →
  approval → event → back onto the hall's calendar, one
  scripted test; contract regenerated (174 paths / 204
  schemas); no ZIP — folds into ZIP-RC at B-138

### Phase 10 — Context pruning and group memory (Sun Nov 1) — B-113 → B-122

- **B-113** — Pruning core: a full LLM judges chunk relevance
  (Geoffrey's design — NOT embedding RAG, and the code says
  so); every run writes a PruneRun (kept, dropped, model,
  cost). Golden 500-message corpus: 100% recall at ≤25% of
  original tokens
- **B-114** — Multi-pass pruning with gap-driven reconsider;
  passes/budget/driver are group settings with hard caps
- **B-115** — Ask-the-group-memory: prune → answer with
  citations verified by the quote rule; failure returns
  "I couldn't verify this", never a confident guess
- **B-116** — Discussion summaries propose CANDIDATE
  decisions/tasks only — the queue disposes
- **B-117** — Event briefings: pruned history + past
  decisions, open questions, role gaps
- **B-118** — Recaps + newsletter drafting; an approved
  newsletter returns as a source — the memory loop closes
- **B-119** — Local-inference driver (OpenAI-compatible,
  text-only); per-group pruning may run local while
  extraction stays hosted
- **B-120** — Memory transparency: inspect and purge prune
  runs; summaries are never deleted by a purge
- **B-121** — Pruning evaluation: golden set scored through
  the engine (mean recall 0.88, precision 0.94, one
  deliberate judge miss included)
- **B-122** — Phase gate: the three memory demos (ask,
  briefing, recap) in mock AND live-wire modes; contract
  regenerated (160 paths / 189 schemas); no ZIP — folds
  into ZIP-RC at B-138

### Phase 12 — External publishing integrations — B-131 → B-138

- **B-131** — Facebook Event creation: the B-058 draft gains
  its living half. POST /events/{id}/facebook is THE PRESS —
  create / update / carry-a-cancellation of the Page copy,
  organiser + publish scope, and the only road out (edits
  here never travel on their own; `differs` says when the
  copy is behind). Proven against the recorded fixture
  client; the roadmap's live smoke test is NOT RUN — no
  Facebook Page is connected in this environment, and
  publishing is Volmarr's word alone (see TEST_REPORT)
- **B-132** — Google Calendar sync (one-way OUT): connect
  (admin) + sync (organiser, publish scope); published
  events upsert to the group's own calendar, cancellations
  propagate as cancellations, drafts never travel; a
  Google-side edit is NEVER read back — proven by test, not
  just stated. OAuth + sync proven against recorded
  fixtures/stub transport; no Google account is connected
- **B-133** — Real mail provider: an SMTP driver (stdlib)
  behind B-095's transport boundary, config in plain words,
  startup refusing a half-configured deployment; the B-102
  bounce webhook gains its HMAC signature check;
  DEPLOYMENT.md carries provider setup + SPF/DKIM/DMARC
  notes. The roadmap's "real digest in a real inbox"
  acceptance is a pre-submission checklist item there —
  NOT RUN (no provider is configured here)
- **B-134** — Messenger notifications: ships the clean
  `driver_unavailable` — B-125's access was never granted,
  so a notification that asked for Messenger records that
  state on its row while email/in-app carry the load. The
  channel is opt-in by link + preference switch, and the
  opt-in/quiet-hours rules are SHARED with the other
  channels — proven by running the same scenarios across
  channels
- **B-135** — Embed + public page data: a group registers
  the origins allowed to embed its page (an origin is a
  scheme + host, never a path); GET /public/groups/{slug}
  serves the public page payload (B-059 projection); GET
  /embed/groups/{slug} refuses a present-but-unregistered
  Origin outright; CORS answers are made for registered
  origins only — an unregistered origin gets nothing
- **B-136** — Integration health board: every integration
  with its recorded verdict and ONE next step in plain
  words — a refused token's advice names the reconnect +
  fresh-token step concretely. The board reads records; it
  pings nothing itself
- **B-137** — Reconciliation job: nightly (02:30
  group-local) + on admin demand, outside Facebook/Google
  copies are compared against ours and drift (differs /
  missing) is reported to the organisers — never auto-fixed
  (an edited outside copy stands for a person to settle,
  B-132's law). A copy DELETED outside is restored by the
  organiser's own press/sync, which is the press's
  business; overwriting an edit is not
- **B-138** — Phase gate: one event's full outward life in
  a single scripted test; contract regenerated (182 paths
  / 212 schemas); **ZIP-RC cut** — Phases 9–12 fold into it

### Phase 13 — Security hardening, privacy, and erasure — B-139 → B-146

- **B-139** — Threat-model pass: `THREAT_MODEL.md` lives in
  the repo — assets, actors, Henry's-first-version as
  Case #1, one abuse case per route family each mapped to
  the test that carries it, accepted risks named (largest:
  group content is not field-sealed until Phase 17). A
  coherence test keeps the document true: named tests must
  exist and every router module must be covered
- **B-140** — Authorisation fuzz: two marked groups, 8
  attacker principals, ~400 seeded cases across every route
  family — zero successes, zero leaked marker bytes, zero
  state change. FINDING, fixed at root: `GET /jobs/{id}`
  answered any signed-in caller; a job naming a group (via
  payload, source, notification, or webhook) is now that
  group's members-only, 404 to everyone else
- **B-141** — Upload hardening: disguised executables and
  EICAR refused by content; image payloads stripped by the
  re-encode; a per-group storage quota (`quota_exceeded`,
  at the settings dial); storage keys leave the API (see
  Breaking changes); the .ics door equals the view door —
  drafts refuse for every role, the B-053 bright line
- **B-142** — Injection + output safety: prompt rendering
  is ONE pass (source text can no longer smuggle template
  placeholders; benign output byte-identical, so fixture
  recordings hold); a poisoned source yields at most a
  pending candidate — no publish, no mail. FINDING, fixed:
  plain-text digests carried HTML entities because escaping
  followed the `.j2` suffix — it now follows the file kind
- **B-143** — Group export + erasure: one schema-derived
  deletion plan serves both acts. Export is a real ZIP
  (domain JSON + iCal + the files themselves; secrets
  withheld by column rule; integration configs export key
  NAMES only). Erase is owner + live person session +
  double confirmation; storage objects die only when no
  other group shares them; the database is vacuumed so the
  raw file greps clean; the audit keeps only the fact.
  (Post-vacuum checkpoint added at the phase gate: the
  B-146 drill proved the vacuum's output can linger in the
  WAL leaving the raw file stale on newer SQLite builds.)
- **B-144** — Privacy surfaces: `GET /users/me/data` (the
  whole footprint, plain sections, no hashes by
  construction); per-source permission records in plain
  words; retention — an opt-in window hard-expires raw
  sources nightly with NO tombstone (candidates keep their
  stamp, approved events stand)
- **B-145** — Secrets + config audit: prod startup refuses
  known-default and short secrets; the access log redacts
  token-carrying path segments. FINDING, fixed: httpx's
  INFO request lines log full URLs — quieted to WARNING.
  pip-audit ran for real over the exported lockfile: no
  known vulnerabilities (TEST_REPORT has the output)
- **B-146** — Backup + restore drill: `scripts/backup.py`
  (SQLite online backup + storage + sha256 manifest),
  `scripts/restore.py` (verify-then-place), and
  `scripts/restore_drill.py`, which plants marker data,
  backs up, restores into a clean tree, builds with a
  FRESH `uv sync`, runs the whole suite there, and reads
  the marker back through the API. Phase gate; no ZIP —
  folds into ZIP-Final at B-151
