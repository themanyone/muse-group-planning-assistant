# Frontend Handoff — ZIP-Beta (v0.3) · Foundation + Phases 1–8

*Regenerated at each release. Read this first.*

## What you have

A running backend with the full identity + groups + permissions
layer working and tested (515 tests green at Phase 4). You can now build:

- **Sign-in screens** — register, password login, magic-link login,
  refresh, logout, password reset, email change.
- **Group home** — create/read/update groups, visibility rules.
- **Member management** — invites (accept/decline), roles, removal,
  the agent-seat list, and the group audit view (admin+).

**New in Phase 2: sources — all ingest kinds are in.**
`POST/GET /groups/{id}/sources`, `GET/DELETE /groups/{id}/sources/{source_id}`
(delete is organiser+; deleting a source that feeds a candidate
tombstones the link — the candidate is never silently orphaned).
Every source carries its permission record (`granted_by`,
`granted_scope`), a `stage` + `progress`, and ordered chunks with
`sender`, `sent_at` (only when unambiguous), the raw
`timestamp_text`, and `region_json` where a chunk maps to a place
in a picture. The ingest doors, all under `/groups/{id}/sources/`:

- `paste` (JSON `{text}`) — chat exports (WhatsApp, Discord, Slack,
  Messenger, `Name:` lines) parsed into speaker chunks; unparsed
  lines are preserved verbatim, never dropped.
- `upload?filename=<name>` — **raw file bytes as the request body
  (not multipart)**; text/HTML/PDF-text-layer/Markdown/.ics;
  10 MiB cap; executables refused by content sniffing, not by
  extension. Scanned-image PDFs (no text layer) are refused in
  plain words — OCR is Phase 5.
- `newsletter?filename=<name>` — raw HTML email body; one chunk
  per section, links kept whole as `anchor text (URL)`, images
  become their alt text or the marked placeholder
  `[image: no description]`.
- `image?filename=<name>[&board=true]` — flyer/photo upload (raw
  body). The response's `flyer` object carries the normalised
  picture (EXIF orientation applied, ≤2400px, thumbnail stored)
  and — build your UI around this — `alt_text` +
  `alt_text_status`: an image starts as the literal text
  "awaiting description" with status `awaiting_description`.
  **An undescribed image is a visible defect**: show that state
  plainly (it clears when the Phase 5 vision pass writes a real
  description). With `board=true` the upload is a whiteboard
  photo: ONE source, status `awaiting_segmentation`, one
  placeholder chunk — region crops arrive in Phase 5; do not
  build region UI yet.
- `link` (JSON `{url}`) — the server fetches and ARCHIVES the
  page's text as chunks and records the final URL in `url`
  (redirects followed). Private/LAN/cloud-metadata addresses are
  refused with 422 `fetch_blocked`; an unreadable page is 502
  `fetch_failed` and archives nothing. Show the archived state,
  not a live embed — the archive is the point.
- `voice?filename=<name>` — raw audio (WAV/MP3/Ogg/FLAC/M4A,
  sniffed by content). The response adds `transcription_job_id` +
  `transcription_status`; the source sits in stage
  `awaiting_transcription` until the Phase 5 driver lands. Poll
  `GET /api/v1/jobs/{id}` or watch the stream (below).
- `live` (JSON `{connector_kind, external_id, granted_scope}`) —
  registers a live-chat source: the permission record itself.
  **Scope is mandatory** (empty → 422) and **seats cannot grant**
  (403) — the grant UI is people-only. No chunks flow yet; the
  receiver is Phase 12.
- `POST /groups/{id}/inbound-address` (admin+) — opens the
  group's email-in channel; the response shows the `token` and
  `address` **once only** (only a hash is kept — a lost token
  means re-issue). Mail delivered to `POST /api/v1/email-in/{token}`
  becomes an `email` source with `granted_by: "system:email-in"`.

**Ingest progress:** synchronous ingests (paste/upload/newsletter/
image/link) return final `stage`/`progress` in the create
response. Job-driven work streams: `GET /groups/{id}/stream`
(SSE, member+) emits one `snapshot` frame on connect, then
`job.progress` frames (`job_id`, `kind`, `status`, `progress`,
`source_id`) as the group's jobs move. Jobs also carry `progress`
in `GET /api/v1/jobs/{id}` (100 when done).

**New in Phase 3: the review queue — the most important screen
in the product.** Everything the AI drafts lands here as
*candidates*; nothing reaches a calendar until a person approves
it here. Build this screen first and build it well.

- **Ask for a run:** `POST /groups/{id}/sources/{source_id}/extract`
  (organiser+, JSON `{kinds?}` — default all four: event, task,
  decision, question) → **202 + `job_id`**. Extraction is a job:
  poll `GET /api/v1/jobs/{id}` or watch the SSE stream; candidates
  appear in the queue when it lands. A source still in stage
  `awaiting_transcription` / `awaiting_segmentation` is skipped
  (the audit says so) — drafting from un-transcribed audio never
  happens, so don't offer the button for those stages.
- **The queue:** `GET /groups/{id}/candidates` with `kind`,
  `status`, `min_confidence` filters, newest first. Each row
  carries the whole triage story: `draft` (see below),
  `confidence` (0–1, the mean of the field confidences the source
  actually backs — sort and threshold on it), `flags` (see
  below), `prompt_name` + `prompt_version` (which instructions
  made this draft), `source_id`, `job_id`, `created_by`
  (`"system:extraction"` for machine drafts — never dress a
  machine draft as a person's words).
- **Statuses:** `pending` → (`edited`) → `approved` / `rejected` /
  `merged`; `superseded` when a newer extraction run replaced an
  undecided draft. Decided rows are history: show them read-only.
  A re-run never edits or deletes them.
- **The draft shape:** `draft.fields` is the evidence map — per
  field: `value`, `confidence`, `quote` (the exact words from
  the source), `supported` (did the quote verify?). Date fields
  add `date_phrase` / `time_phrase` (the source's own words) and
  the resolver's answer: `date_readings` (ALL readings when
  ambiguous), `date_ambiguous`, `resolved_date`. Top-level
  mirrors (`draft.title`, `draft.place`, `draft.starts_date`,
  `draft.starts_at`) are conveniences for list rendering; the
  fields map is the truth. **When `date_ambiguous` is true, show
  every reading and let the reviewer pick — the backend never
  guesses, and neither should the UI.**
- **Detail:** `GET /groups/{id}/candidates/{candidate_id}` adds
  `provenance`: one row per field with its quote, `chunk_id`,
  `chunk_position`, and support verdict. An **unsupported field
  is shown, struck through or badged — never hidden**: a field
  whose quote could not be found in the source is exactly what
  the reviewer is there to catch.
- **Flags are the API's words — render them verbatim, never
  invent your own:** `low_confidence:<field>`,
  `missing_required:<field>`, `unsupported:<field>`,
  `ambiguous_date:<field>`, `unresolved_date:<field>`,
  `conflicting:<field>` (another source's candidate disagrees),
  `shared_chunk_unflagged`. They arrive pre-computed on every
  row; after any edit the API recomputes them.
- **Decisions (organiser+):** `PATCH …/candidates/{id}` with
  `{draft}` edits (status becomes `edited`, flags recomputed,
  title re-derived); `POST …/candidates/{id}/reject`;
  `POST …/candidates/{id}/merge` with `{into_candidate_id}`
  (the loser stays whole under status `merged` — show the link);
  `POST …/candidates/{id}/approve` (the Phase 1 route). Since
  Phase 4, approving an EVENT candidate **mints the published
  event in the same act**: `resulting_entity_type` is `"event"`
  and `resulting_entity_id` links to it — render the event link.
  Approving a draft whose date never resolved is refused (422,
  plain words — "edit the draft to give it a date first") and
  the candidate stays pending. Other kinds still record the
  decision only (their entities are Phase 7).
- **Queue status + the budget:** `GET /groups/{id}/candidates/status`
  → `{pending_count, paused, pause_reason, spent_today_micros,
  budget_micros}`. When a group's daily AI budget is spent,
  `paused` is true and `pause_reason` is `"budget"`: show the
  queue as **"paused — budget"** in those plain words, with the
  spend and cap. Extraction defers itself to the next UTC day —
  nothing is lost, and the UI should say waiting, not broken.
  The cap is set via `PATCH /groups/{id}` `ai_daily_budget_micros`
  (admin+; it can be set or changed, not cleared back to null).

**New in Phase 4: events + the calendar — the second most
important screen set in the product.** An approved event
candidate is now a real event, and every read below is
occurrence-expanded server-side: **the frontend does zero date
maths of its own, anywhere.**

- **The bright line:** events are `draft` / `published` /
  `cancelled`. Drafts reach NO surface below — they are a 404
  to anyone below organiser, even by id. Cancelled events stay
  visible everywhere, marked cancelled. Build your states on
  exactly those three words.
- **Event CRUD:** `POST /groups/{id}/events` (organiser+;
  direct entry — it records a manual source behind the event),
  `GET …/events` (soonest first), `GET …/events/{id}`,
  `POST …/events/{id}/publish`, `POST …/events/{id}/cancel`
  (body `{reason}` — the reason is shown with the cancellation),
  `POST …/events/{id}/clone` (201; structure copies, RSVPs and
  discussion never do; the clone is a draft).
- **Editing is optimistic-concurrency (D-15):** reads carry an
  `ETag` header (`"v<n>"`) and the organiser projection carries
  the same value in `etag`. `PATCH …/events/{id}` **requires**
  `If-Match` with the version you read: a stale version is 409
  `stale_version` whose detail names the CURRENT version — show
  a "someone changed this; re-read and re-apply" state, never
  silently retry. Every changed field lands in
  `GET …/events/{id}/changes` (old/new/changed_by/reason, in
  order) — that's your "what changed" view, verbatim. Material
  changes (time, place) set `needs_reannounce: true` on the
  organiser projection; surface it as "the group needs telling".
- **Projections (B-059):** the same event renders at three
  levels, chosen by the server from the caller's role —
  `public` (title, times, place, flyer; NO host, cost, bring
  list, or access notes — those fields are absent, not blanked),
  `member` (everything practical, incl. `access_notes`),
  `organiser` (plus `etag`, `needs_reannounce`, `created_by`,
  `candidate_id`, `cancelled_reason`, `links`). The public page
  lives on its own route: `GET /api/v1/public/groups/{slug}/events`
  (only for `public-listed` groups; anything else is a 404).
  Never try to "hide" member fields client-side on a member
  response — ask for the public route instead.
- **Recurrence:** `PUT/GET/DELETE …/events/{id}/recurrence`
  (`{rrule, exceptions?}`), `POST …/events/{id}/recurrence/exceptions`
  (`{original_start, moved_to?, cancelled}` — LOCAL ISO times in
  the event's timezone). Rules are validated at write time in
  plain words. **You never expand a rule:** reads below return
  occurrences. `GET …/events/{id}/occurrences?from&to` is the
  per-event form.
- **Agenda:** `GET /groups/{id}/agenda?from&to` (ISO datetimes;
  defaults: from now, +31 days) → `{items, next_cursor}`.
  Occurrences carry `occurrence_start`/`occurrence_end`
  rendered in the EVENT's timezone with its true offset
  (`-04:00` vs `-05:00` across DST — render these strings, do
  not recompute), `starts_at`/`ends_at` in UTC alongside, plus
  `is_exception` and `occurrence_cancelled` flags. Filters:
  `q` (title/place/host), `type`, `place`, `sub_group`.
  Pagination: pass `cursor` from the previous page's
  `next_cursor`; `limit` ≤ 200. Pages are stable.
- **Month:** `GET /groups/{id}/month?year&month` → whole
  Monday-first weeks (`weeks[][]` of `{date, in_month,
  occurrences}`) — render cells as given; days outside the
  month arrive marked `in_month: false`, no holes to fill.
- **Tonight:** `GET /groups/{id}/tonight[?date=YYYY-MM-DD]` —
  the evening (15:00 → 03:00 local), events with pinned
  coordinates first, then by start. Flyer-born events carry
  `flyer: {flyer_id, thumbnail_key, alt_text, alt_text_status}`
  — the same visible-defect law as the upload screen: an
  `awaiting_description` alt state is shown, never hidden.
- **Conflicts:** `GET /groups/{id}/conflicts?from&to` →
  `{flags, resource_note}`. Flags are `host_overlap` /
  `place_overlap` / `travel_impossible`, each naming both
  events + a plain-words `message` — render the message.
  Flags never block anything; there is no "resolve" call.
  (Back-to-back events in one room never flag — if you think
  you see one, it's a bug report, not a UI state.)
  `resource_note` says resources aren't computed yet (Phase 9).
- **iCal out:** `GET …/events/{id}/ics` (member+; a draft is a
  404) downloads one event. **Group feed:**
  `POST /groups/{id}/calendar/feeds` (organiser+, body
  `{filters}` — `statuses`, `place_contains`, `sub_group`) →
  the response carries the raw `token`, `feed_url`, and
  `webcal_url` **once only** (only a hash is stored; a lost
  URL means revoke + re-issue). `GET …/calendar/feeds` lists
  prefixes; `POST …/calendar/feeds/{feed_id}/revoke` kills a
  feed instantly. The fetch route
  `GET /api/v1/calendar/feed/{token}.ics` needs no login — the
  token is the credential; treat it like a password in the UI
  (masked, with a revoke button). Cancelled events arrive in
  feeds AS cancelled; drafts never arrive.
- **Links:** the organiser event detail carries `links`:
  `google_calendar` (a ready add-to-calendar URL — the server
  computed the times; use it verbatim) and `ics_download`.
- **Facebook drafts:** `GET …/events/{id}/facebook-draft`
  (organiser+) → `{payload, copy_text, cover}`. `payload` is
  Meta-shaped (name, start_time/end_time with true offsets,
  description, place, timezone); `copy_text` is a paste-anywhere
  text block; `cover` suggests the event's flyer. **Nothing is
  posted** — the real Facebook Event is created by a human via
  a later integration. Draft events 404 here; cancelled 409.
- **Discussion:** `GET/POST …/events/{id}/comments`,
  `PATCH/DELETE /groups/{id}/comments/{comment_id}` (your own
  only — 403 otherwise; delete is a tombstone),
  `POST …/comments/{id}/hide|unhide` (organiser+; audited).
  Member lists omit hidden/deleted entirely; organiser lists
  include them flagged (`hidden`, `deleted`) with bodies kept.
  The group SSE stream now also emits `comment.created` frames
  (`comment_id`, `event_id`, `author`, `body`) — same
  `GET /groups/{id}/stream` door as job progress.
- **RSVPs:** `PUT …/events/{id}/rsvp` `{status: coming|cant|
  maybe, guests, note}` (your own answer; an upsert),
  `DELETE …/events/{id}/rsvp`, `GET …/events/{id}/rsvps` →
  `{rsvps, summary}` (summary: capacity, confirmed_seats,
  waitlisted_parties, maybe, cant). When the room is full the
  PUT response says `waitlisted: true` + `waitlist_position` —
  show "you're #N in line" in those words. The line is strict
  arrival order: a party that doesn't fit blocks it (no
  queue-jumping), and a freed seat promotes the next in line,
  whose notification rides the job queue (delivery is a later
  phase). Answering a cancelled event is a 409.

Not yet built (later phases): tasks/decisions/polls as
entities (Phase 7), reminders, digests, real notification
delivery (Phase 11), the Facebook posting integration
(Phase 12).

## Flyers, vision, and boards (Phase 5)

- **Reading a picture**: `POST /groups/{id}/sources/{source_id}/read`
  → `202 {job_id, source_id}` (organiser+). Poll the job
  (`GET /jobs/{id}`); on a board photo the job's `progress` is
  the share of posters read so far. When it lands, the source's
  flyer has a `transcript` and the review queue holds one
  candidate per poster. Reading a text/voice source is a 422 in
  plain words.
- **Review with regions**: the candidate detail's `provenance`
  rows now carry `region: [x, y, w, h] | null` (fractions of the
  picture) alongside `supported`. Draw the box on the flyer
  image when a reviewer taps a field; `supported: false` means
  "the model claimed this but could not point at it" — style it
  as unverified, never hide it. The candidate draft also carries
  `links: [{url, kind: "qr" | "printed", region?}]` — render
  them as tappable links.
- **Boards**: one board photo becomes one child flyer per
  poster (source detail shows `flyer.parent_flyer_id` +
  `flyer.region_json`). A poster that defeats the reader is not
  lost: its source stage is `read_failed` and the wall shows it
  as such — build a "couldn't read this one" state.
- **The flyer wall**: `GET /groups/{id}/flyers?period=YYYY-MM`
  → `{period, entries: [{flyer_id, source_id, image_key,
  thumbnail_key, alt_text, alt_text_status, generated,
  created_at, review_status, event}]}` ordered dated-first.
  `review_status` is the event's status once approved,
  `pending_review` before, `read_failed` when unreadable. A
  flyer is never absent from its month — pending ones are the
  point of the wall.
- **Alt text is law**: `alt_text` may be the literal string
  `"awaiting description"` **only** together with
  `alt_text_status: "awaiting_description"` — treat that pairing
  as a visible defect state ("no description yet"), never render
  the placeholder as a description. `PATCH /groups/{id}/flyers/{id}`
  with `{alt_text}` (member+) writes a human description; the
  API refuses non-descriptions (a word or two, the filename)
  with a 422 that says why. Every event's `flyer` face
  (`{flyer_id, thumbnail_key, alt_text, alt_text_status,
  generated}`) rides detail, list, agenda, tonight, and the
  public projection.
- **Generated images**: `generated: true` on a flyer means the
  picture was machine-made for the event — the flag is in every
  projection forever; badge it. Organisers create one with
  `POST /groups/{id}/events/{id}/generate-image`
  `{description?}` → `201` event; an event that already has a
  picture answers 409.
- **Print month**: `GET /groups/{id}/print/month?month=YYYY-MM`
  → `{period, events: [rows], text}` — `text` is a complete
  plain-text month listing (every fact included) for a real
  noticeboard; the rows carry the same facts structured.
- **Duplicates**: a candidate may carry the
  `possible_duplicate` flag and `draft.duplicate_of`
  `{flyer_id, source_id, candidate_id, event_id, hash_distance}`
  — surface it as "looks like this poster again" with a link
  to the earlier one. Nothing is auto-merged, ever.
- **Voice notes** now transcribe through the same job door:
  after the `transcribe` job lands, the source is a normal text
  source (`ready`/`done`) whose chunks carry speaker labels
  where the note offered them, and it can be extracted like any
  chat paste.

## De-duplication, changes, and truth-keeping (Phase 6)

- **"Likely the same event" (B-075/B-076).** A candidate's
  detail (`GET /groups/{id}/candidates/{cid}`) now carries
  `matches`: the group's existing events scored against the
  draft (title / time / place / host components, a `score`,
  and `likely` at ≥ 0.6). Render the best match as a
  side-by-side "likely the same event" panel with a merge
  action: `POST /groups/{id}/candidates/{cid}/merge-event`
  with `{event_id}` (organiser+). The response says exactly
  what happened per field — `applied_fields` (the candidate's
  better-sourced or fuller value won), `filled_fields` (the
  event had nothing), `agreed_fields`, `kept_fields` (the
  event's value stood), and `contradiction_ids`. The machine
  proposes; only a person merges. Below the threshold there is
  no panel and approval mints a new event as before.
- **"Where did this come from?" (B-076).** Every surviving
  field of a merged (or minted) event keeps its evidence:
  `GET /groups/{id}/events/{eid}/sources` lists
  `{field, source_id, chunk_id, quote}` rows. Link field
  labels to it.
- **Contradictions (B-079).** When two sources disagree and
  neither wins, the event carries the dispute instead of
  hiding it: organiser event detail includes `contradictions`
  (also `GET /groups/{id}/events/{eid}/contradictions`,
  `?include_resolved=true` for history). Each carries
  `values: [{value, source_ids, current}]` — render them as a
  visible "sources disagree" flag with the choices; resolve
  with `POST .../contradictions/{cid}/resolve` `{value, note}`
  (the value must be one the sources said). A later source can
  also settle a dispute by weight of evidence — the merge
  response's auto-resolution shows up as the contradiction
  closing with `resolution` text.
- **Change candidates (B-077).** Extraction gains a fifth
  kind, `event_change`, requested explicitly
  (`POST .../sources/{sid}/extract` with
  `{"kinds": ["event_change"]}` — it is NOT in the default
  run). Its drafts name the event they change
  (`draft.target_event_id` / `target_title`); approval APPLIES
  the change to that event (moves keep the unmoved part; a
  cancellation cancels) and the change appears in the event's
  change feed with reason `change candidate <id>`. A change
  naming no known event carries the `change_target_unknown`
  flag and cannot be approved (422, plain words) — show the
  flag and offer edit/reject. Rejecting a change leaves the
  event standing.
- **Re-announce drafts (B-078).** After a material change,
  `POST /groups/{id}/events/{eid}/reannounce-drafts`
  (organiser+) drafts the announcement from the change
  history: the `body` names old → new for each change since
  the last draft ("Start: Friday 06 November 2026, 06:30 PM →
  07:30 PM") — render the body verbatim. 422 when there is
  nothing new to announce. Drafting does NOT clear
  `needs_reannounce`; sending is a later phase, so keep the
  "group not told yet" indicator lit until then.
- **Delete + undelete (B-080).** `DELETE
  /groups/{id}/events/{eid}` (organiser+) soft-deletes: the
  event vanishes from every list, calendar, and feed at once.
  `GET /groups/{id}/tombstones` (organiser+) lists deleted
  events and sources with `purge_after` (deleted + 30 days)
  and `restorable`. Restore with `POST
  .../events/{eid}/restore` or `POST
  .../sources/{sid}/restore` — events come back whole
  (history, links, RSVPs); sources rebuild with their
  original ids and their candidates lose the
  source-deleted stamp. After the window, restore is a 422
  that says "gone for good" — show those words.
- **Needs-attention badge (B-081).** `GET
  /groups/{id}/quality-report` (member+) returns four lists —
  `missing_place`, `missing_host` (published events only),
  `low_confidence_fields` (the open queue),
  `unresolved_contradictions` — and `needs_attention`, their
  total. The badge is that number; the lists are the work
  queue behind it.

## Planning: tasks, decisions, polls, checklists (Phase 7)

Phase 7 folds into **ZIP-Beta** (cut at the Phase 8 gate) — no
separate ZIP. The planning screens below are the group's home.

- **Group home / "state of the plan" (B-093).** `GET
  /groups/{id}/planning-digest` is one call for the home
  screen: `week_start`/`week_end` (Monday → Monday,
  group-local), `events_this_week`, `unclaimed_tasks`,
  `open_questions`, `undecided_polls` (each poll with its
  tally). Render the four sections as the home's lists; the
  counts are the badges.
- **Tasks (B-083).** `GET/POST /groups/{id}/tasks` (filters:
  `event_id`, `status`, `unclaimed`, `owner_id`), `GET
  /groups/{id}/events/{eid}/tasks`, `PATCH
  /groups/{id}/tasks/{tid}`. Every task carries two computed
  truths — build the UI on them, never recompute:
  `unclaimed` (nobody owns it — show it loudly) and `overdue`.
  `suggested_owner_label` is the AI's reading of who
  volunteered ("Ben said he'd bring it") — render it as a
  hint ("Ben mentioned this"), NEVER as the owner. `owner_id`
  is set only by a person: the create form, the assign action
  (PATCH `owner_id`, `null` unassigns), or a reviewer naming a
  member in a candidate draft before approving. Status flow:
  `open → in_progress → done`/`cancelled`, `done → open`
  reopens; other jumps return 409.
- **Approvals now mint planning too.** Approving a task /
  decision / question candidate creates the entity in the same
  act (`resulting_entity_type` says which). A task candidate
  whose draft names a non-member `owner_id` cannot be approved
  (422, plain words) — show the message and offer the draft
  editor.
- **Discussion everywhere (B-084).** ONE thread system, four
  parents. The event thread you already have
  (`/events/{eid}/comments`) is the same rows as `GET/POST
  /groups/{id}/comments/{parent_type}/{parent_id}` with
  `parent_type` in `event|task|decision|poll`. Comment bodies
  carry `parent_type` + `parent_id` always; `event_id` is set
  for event parents, null otherwise. Reuse the event thread
  component for task/decision/poll screens verbatim.
- **Decisions log (B-085).** `GET/POST /groups/{id}/decisions`;
  `?current=true` is the settled-truth view (superseded rows
  hidden). A create may name `supersedes_id` — the old row is
  kept, marked `superseded`, linked both ways; `GET
  /groups/{id}/decisions/{did}/chain` returns the whole chain
  oldest → newest for a "how we got here" view.
- **Open questions (B-086).** `GET/POST /groups/{id}/questions`,
  `PATCH` (reassign `asked_of_id`, or `{"drop": true}`),
  `POST .../questions/{qid}/resolve` — with `decision_text`
  the answer is recorded as a decision in the same call and
  the response links it (`resolution_decision_id`). The event
  screen's "what is still missing?" panel is `GET
  /groups/{id}/events/{eid}/gaps`: one flat `gaps` list
  (`kind`: `missing_field` | `open_question` |
  `unclaimed_role` | `unclaimed_task`) for the badge, plus
  `missing_fields`, `open_questions`, `unclaimed_roles`,
  `unclaimed_tasks` sections behind it.
- **Polls (B-087).** `POST /groups/{id}/polls` (`kind`:
  `choice` | `date`; `closes_at`; optional `event_id`; ≥2
  options — date options carry `starts_at`/`ends_at` and
  their label defaults to the formatted moment). `POST
  .../polls/{pid}/vote` `{option_id}` votes or CHANGES the
  caller's one current vote, and the response is the tally
  read after the vote committed: per-option `votes`,
  `total_votes`, `leader_option_ids` (two ids = a tie — say
  so), `my_option_id`. `POST .../polls/{pid}/close`
  (organiser+) freezes it. `POST .../polls/{pid}/apply-winner`
  (organiser+) applies a DATE poll's winner: moves the
  attached event or mints a new one (`{title}` optional);
  a tie or zero votes is a 409 with plain words — show them,
  never pick a winner client-side.
- **Checklists + roles (B-088).** Per event: `GET/POST
  /groups/{id}/events/{eid}/checklist` (`section`: `bring` |
  `prepare`, `quantity`), `PATCH /groups/{id}/checklist/{iid}`
  (incl. `done`), claim/unclaim via `POST
  /groups/{id}/checklist/{iid}/claim|unclaim` (claiming
  another's line is 409). Roles: `POST
  /groups/{id}/events/{eid}/roles` `{role_name, slots}` opens
  N one-person slots; `GET` lists them with `claimed` flags;
  claim at `POST /groups/{id}/roles/{sid}/claim|unclaim`.
  The shopping view is `GET /groups/{id}/supplies?start=&end=`
  (defaults to this week): lines folded across events by
  label — `total_quantity` plus the per-event breakdown —
  "buy once", sorted by label.
- **Budget-lite (B-089).** `POST
  /groups/{id}/events/{eid}/costs` `{label, amount_cents,
  paid_by_id?, paid_by_label?}`; `GET
  /groups/{id}/events/{eid}/budget` returns `lines`,
  `balances` (per-member `paid_cents`), `total_cents`.
  Amounts are **integer cents end to end** — a float
  `amount_cents` is a 422; format for display only. A
  words-only payer (`paid_by_label`, e.g. "Cash Pete") is a
  row with `member_id: null`. Deliberately no ledger and no
  payments — don't build settle-up UI against this.
- **Availability (B-090).** Organisers propose slots (`POST
  /groups/{id}/availability/slots`); members mark `POST
  .../availability/slots/{sid}/mark` `{can: true|false}`
  (re-marking updates the one mark). `GET
  /groups/{id}/availability` returns slots RANKED (most can,
  then fewest can't, then earliest) with `can_members` /
  `cant_members` name lists. Only the group's own marks
  against the group's own proposed slots exist here — no
  private calendar data is read or shown, ever.
- **Templates (B-091).** `GET /groups/{id}/templates` — the
  global starters (`is_starter: true`: meeting, club night,
  trip, fundraiser, class, match, celebration) plus the
  group's own. Save with `POST /groups/{id}/templates` or
  snapshot an event via `POST
  /groups/{id}/templates/from-event/{eid}` (organiser+).
  Instantiate with `POST
  /groups/{id}/templates/{tid}/instantiate` `{starts_at,
  title?}` (organiser+): returns the new `event_id` plus the
  built `task_ids` / `checklist_item_ids` / `role_slot_ids`.
  Instantiated tasks are unclaimed and roles unclaimed —
  templates carry shapes, not people's promises.
- **Search (B-092).** `GET /groups/{id}/search?q=` — one
  ranked list over events, tasks, decisions, questions; each
  hit has `type`, `title`, `snippet`, `score`, and
  source links (`candidate_id`, `source_id`, `event_id`)
  for "where did this come from?" navigation.

## Reminders, notifications, digests (Phase 8) — NEW in ZIP-Beta

The backend now speaks. Everything below is live and tested.

- **Inbox screen** — `GET /users/me/inbox` (the caller's own
  notifications across their groups; `?unread_only=true`),
  `POST /users/me/inbox/{id}/read`. One row per message: the
  inbox item and the email are the SAME notification (subject,
  body, status), so the two can never disagree. The group
  stream (`GET /groups/{id}/stream`) now also emits
  `notification.created` frames for the connected member's own
  inbox — kind + event + subject only; fetch the inbox for the
  body.
- **Notification preferences screen** — `GET/PUT
  /users/me/groups/{group_id}/notification-preferences`:
  switches per channel (email / in_app) per kind (invite,
  reminder, change, digest, rsvp) — all default ON — plus
  quiet hours `{start, end, timezone}` (HH:MM). A reminder
  whose moment lands in quiet hours is HELD to the quiet end
  (status `held`), never dropped. Urgent rows (an organiser's
  cancellation) are never held.
- **Event invites** — `POST /groups/{id}/events/{event_id}/invites`
  (organiser+) returns `{…, link}` — the link carries the raw
  token and is shown ONLY in this response (listings show a
  prefix). The invitee also gets an `invite` notification.
  Public, no account: `GET /invites/{token}` (the event's
  public projection + `account_offered`), `POST
  /invites/{token}/rsvp` `{status, guests, note}` → the answer
  lands as `guest:<email>` in the RSVP list. Tampered token =
  404, nothing created.
- **Change alerts** — after drafting a re-announcement
  (Phase 6), `POST /groups/{id}/events/{event_id}/reannounce-drafts/{draft_id}/approve`
  (organiser+, publish scope) sends it: exactly the RSVP'd
  people (coming + maybe) get a `change.announcement`
  notification; `needs_reannounce` clears; the draft records
  `sent_at`/`sent_by`. Approving twice is a 409.
- **Reminders** — automatic: at publish, on any edit, and on
  every RSVP change, each RSVP'd person (plus the host) gets
  one `reminder` notification per rule at its moment
  (defaults: 7 days before 09:00, 1 day before 09:00,
  morning-of 08:00 — event-local). Moving an event supersedes
  the old rows (their jobs are deleted) and writes new ones
  whose bodies carry place, bring-list, and what changed.
  Group settings (PATCH group): `reminder_rules`
  `[{kind: days_before, days: N} | {kind: morning_of}]`,
  `ai_reminder_wording` (bool) — when on, reminder bodies may
  be AI-rephrased AROUND the facts; the row keeps
  `template_body` + `wording_source` (`ai` /
  `template_fallback`) so you can show provenance if you want.
- **Weekly digest** — `GET /groups/{id}/digests`,
  `POST /groups/{id}/digest/run` (organiser+; composes +
  sends now and anchors the weekly chain). Group settings:
  `digest_weekday` (0 = Monday), `digest_time` ("HH:MM"),
  `digest_enabled`. The digest body (text + HTML stored on
  the row) sections: Coming up / Tasks needing an owner /
  Open questions / Polls awaiting your vote. Members receive
  it as a `digest` notification (inbox + email per
  preferences).
- **Deliverability (admin)** — `GET
  /groups/{id}/notification-failures` (organiser+):
  failed/dead notifications + provider mail events;
  `POST /groups/{id}/notifications/{notification_id}/resend`
  requeues one (attempts reset). Notification statuses:
  queued / held / sent / failed / dead / superseded.
- **Mail, honestly** — email leaves through ONE transport
  boundary. In dev it is the file outbox (messages as JSON in
  the outbox dir, like the magic-link mail you already know).
  The real provider driver is Phase 12 (B-133); the
  bounce/complaint receiver `POST /webhooks/mail/{provider}`
  exists in stub form now (records the event; a bounce marks
  its notification failed). Nothing in this ZIP claims a real
  email reached a real inbox.
- **ZIP-Alpha note** — no separate Alpha ZIP was cut: its
  gate passed on 2026-10-24 and it folded into this Beta,
  whose range is B-001 → B-104.

## Members, nonprofit pack, and resources (Phase 9) — NEW, folds into ZIP-RC

Sub-groups, an announcements board, volunteer shifts, hall
gear, attendance counts, roster import/export, and one
settings pack. All live and tested; contract is now
151 paths / 179 schemas.

- **Sub-groups (chapters)** — `POST/GET /groups/{id}/subgroups`
  (organiser+ to create), `PATCH/DELETE
  /groups/{id}/subgroups/{sub_id}`, enrol via `POST
  /groups/{id}/subgroups/{sub_id}/members` `{user_id, role?}`.
  Events and tasks carry `subgroup_id`: a scoped item is for
  its sub-group's members and group organisers — to anyone
  else in the group it is **absent (404)**, exactly like a
  private group. Send `subgroup_id` on event/task create or
  PATCH to scope one. Deleting a sub-group that still scopes
  anything is a 409 with counts — unscope first.
- **Announcements board** — `POST/GET /groups/{id}/announcements`
  (organiser+ posts; members read), `PATCH/DELETE` the same
  path with the announcement id. `pinned` posts sort first,
  then newest; `expires_at` hides a post from members when it
  passes (organisers can pass `?include_expired=true`). A
  member posting is a 403 — the board is organiser voice,
  separate from chat and from event discussion.
- **Volunteer shifts** — `POST /groups/{id}/events/{event_id}/shifts`
  `{role_name, headcount, starts_at, ends_at}` opens headcount
  slots over one window; claims ride the role endpoints you
  already have (`POST /groups/{id}/roles/{slot_id}/claim` and
  `…/unclaim`). **Gap report**: `GET /groups/{id}/shift-gaps?from=…&to=…`
  returns only events that still need people, `missing`
  in first-opened order, plus a ready-made `summary` line
  ("Saturday still needs a driver and a door person.") —
  show it verbatim; it is written for humans.
- **Resources** — `POST/GET/PATCH/DELETE /groups/{id}/resources`
  `{name, description, keeper_user_id?}` (the keeper is a
  member; their label shows on the item), bookings via
  `POST /groups/{id}/events/{event_id}/resource-bookings`
  `{resource_id}` (list them on the same path; unbook via
  `DELETE /groups/{id}/resource-bookings/{booking_id}`).
  **Double-booking is allowed and flagged**,
  never refused: the calendar conflicts response
  (`GET /groups/{id}/conflicts`) gains `resource_overlap`
  flags, and a booking response echoes its own `conflicts`.
  Build the UI as "booked — and heads up, it clashes", not an
  error state.
- **Attendance** — `POST/DELETE
  /groups/{id}/events/{event_id}/attendance` `{user_id}`
  (organiser marks), reads are **counts only**: `GET
  /groups/{id}/events/{event_id}/attendance` →
  `{attendee_count}`, and `GET /groups/{id}/attendance/summary?recent=3`
  → per-event counts + an average. There is deliberately
  **no per-person attendance read anywhere** — do not design
  a "who came" screen against this API; it refuses by design.
- **Member import/export** — `POST /groups/{id}/members/import`
  (admin+) with the CSV as the raw body (`name,email,sub-group`
  header; `?dry_run=true` reports without changing anything).
  The response is a report: created / already-members /
  enrolments / `subgroups_created`, plus `skipped` and
  `errors` rows named by spreadsheet row number — show it
  after an import; it is the receipt. Imported people arrive
  as **invited** members. Export: `GET
  /groups/{id}/members/export` (JSON) or `?format=csv`.
- **Settings pack** — `GET /groups/{id}/settings`
  (organiser+), `PUT` (admin+): one object — `review_min_role`,
  `review_approvals_required` (1–3 distinct approvers),
  `reminder_rules`, `digest_weekday` / `digest_time` /
  `digest_enabled`, `template_set`, `ai_daily_budget_micros`
  (null = no cap). PUT is partial: absent fields are left
  alone; a present null clears where null means something.
  **Approval UI changes**: the approve door is membership
  now — whether THIS caller may approve, and whether one
  approval decides, is the group's pack. `CandidateResponse`
  carries `approvals_recorded` / `approvals_required`, so
  render "1 of 2 approvals" from those; a recorded-but-not-
  decided approval returns the candidate still `pending`,
  and the same person approving twice is a 409.
- **Scoping boundary (honest)**: the weekly digest and the
  iCal feed stay group-wide in this phase — they are
  organiser-published broadcasts, and the digest is one
  stored row per group. Member-facing interactive reads
  (events, tasks, calendar, announcements, search) enforce
  sub-group scoping.

## The contract

- `OPENAPI.json` in this ZIP is the exact contract the tests passed
  against. `frontend_types.ts` is generated from it — use the types.
- Base path `/api/v1`. Health at `GET /health` (no auth).
- Auth: `Authorization: Bearer <access token>` (15 min) for people;
  `X-Api-Key: gpa_...` for agent seats and integrations.
- Every error is RFC 9457 problem+json with a stable `code` field —
  branch on `code`, never on message text. Codes live in
  `src/gpa/errors.py` (the registry is the contract).
- Private groups return **404** (not 403) to non-members. Build
  your "group not found" state accordingly.
- Agent seats can draft candidates and can **never** approve them —
  the API returns 403 `seat_cannot_approve`. Approval UI is for
  people (organiser+) only.

## Live sources and the bridge (Phase 11) — NEW, folds into ZIP-RC

Phase 11 connects the village to its neighbours — carefully,
and honestly about what is and is not live.

- **Live sources (Samtal, Messenger).** Register a live
  source with `POST /sources/live` (the B-032 permission
  record), then `POST /sources/{id}/sync` pulls new messages
  as transcript chunks. Each source has a sync-state record:
  cursor, last success, last attempt, and the last refusal
  **in plain words** — show it verbatim when a sync fails;
  that text is written for the user, not for a log.
  **Samtal is exercised against recorded fixtures and a test
  double in this backend's suite**; real deployment points
  the connector at the group's own Samtal with its own
  consent record. **Messenger access has not been granted**:
  a Messenger source registers like any other, but sync
  refuses with a message naming the paste/export road — the
  UI should present paste/export as *the* Messenger path,
  not as a degraded one.
- **Speakers and identity.** `GET /sources/{id}/speakers`
  lists who spoke, linked or guest (`guest: true`,
  `person_id` null). Nothing merges a speaker into a member
  except an explicit link — `POST/GET/DELETE
  /account-links` (admin-gated; link by the speaker's
  Samtal person id, `external_ref`). Render unlinked
  speakers as named guests; that is their standing, not an
  error.
- **The calendar return path.** `POST
  /sources/{id}/calendar-sync` (organiser) pushes the
  group's published events onto the hall's calendar and
  brings Samtal-side edits back as **change candidates** in
  the ordinary queue (provenance `system:samtal-calendar`) —
  nothing Samtal-side ever writes the group's events
  directly. The response counts `pushed`, `candidates`, and
  `unchanged`.
- **Inbound webhooks.** `POST /groups/{id}/webhook-endpoints`
  returns the URL token **once** — capture it in the same
  screen that created it; it is never shown again (only its
  hash is kept). Senders sign with the endpoint's derived
  secret: header `X-Gpa-Signature: t=<unix>,v1=<hmac-sha256
  hex of "<t>.<raw body>">`. A signed payload is accepted
  exactly once; replays get `409 webhook_replay`. Payload
  shapes: `{"messages": [{sender, text, sent_at?,
  sender_ref?}]}` or `{"text": …}`.
- **Integrations.** `POST/GET/PATCH/DELETE /integrations`
  stores a kind (`samtal`, `facebook_events`,
  `google_calendar`, `custom`) plus a config object that is
  **sealed at rest** — reads return only `config_keys` (the
  names), never values. `POST /integrations/{id}/health`
  records a health verdict with a plain-words `last_error`;
  kinds whose live slice lands later say so in that text.
- **Outbound webhooks.** `POST /outbound-webhooks`
  subscribes an HTTPS endpoint to `event.published`,
  `event.changed`, `event.cancelled`, `candidate.pending`;
  the signing secret is shown **once**. Deliveries arrive
  signed the inbound way (`X-Gpa-Signature` over
  `"<timestamp>.<body>"`, plus `X-Gpa-Event` and
  `X-Gpa-Delivery` headers) and are inspectable at
  `GET /outbound-webhooks/{id}/deliveries` (status,
  attempts, last error, response status) — a delivery that
  exhausts retries ends `dead` with its reason kept.
- **The agent pack.** Operations tagged **`agent`** in
  `OPENAPI.json` are the seat's working surface (sources
  read, queue read/draft, events read, ask, summaries).
  Scopes, once: `read` sees, `draft` creates, `publish`
  requests extractions/syncs, `admin` administers — and no
  scope lets a seat approve; approval is a person's act, and
  the API refuses a seat's approval with `403
  seat_cannot_approve`. `examples/secretary_seat.py` is a
  complete worked seat to build against.
- **Contract:** 174 paths / 204 schemas (was 160 / 189).

## Context pruning and group memory (Phase 10) — NEW, folds into ZIP-RC

The group's memory engine: the approved history (sources,
current decisions, approved summaries) can be asked
questions, woven into briefings and recaps, and inspected.
All live and tested; contract is now 160 paths /
189 schemas.

- **Ask the memory** — `POST /groups/{id}/ask` `{question}`
  (member+) → `{answer, verified, citations, summary_id,
  prune_run_id}`. Every citation is `{quote, chunk_id,
  position}` and was verified against its source chunk
  before the answer shipped. **When `verified` is false,
  the answer BEGINS with "I couldn't verify this"** and any
  citations carry `chunk_id: null` — render unverified
  answers visibly differently (they are labelled found
  material, not fact). Never restyle a refusal as an answer.
- **Summaries** — `GET /groups/{id}/summaries?kind=`
  (member+) lists answers, briefings, recaps, newsletters
  with `status` draft/approved/final. Drafts become memory
  only via `POST /groups/{id}/summaries/{summary_id}/approve`
  (organiser+). Approving a **newsletter** also files it as
  a source — next month's memory reads it.
- **Discussion summaries** — `POST
  /groups/{id}/summaries/discussion` `{parent_type,
  parent_id}` (organiser+) → a draft summary plus
  `candidate_ids`: the decisions/next actions it named,
  waiting in the review queue as ordinary candidates
  (`created_by: "system:summary"`, flag `from_summary`).
  A summary never writes a decision itself.
- **Event briefings** — `POST
  /groups/{id}/events/{event_id}/briefing` (member+) →
  `{summary, open_questions, role_gaps, prune_run_id}`:
  the host's woven briefing (past decisions, last recap,
  pruned history — landmines included), the model's open
  questions, and the data's unclaimed role names.
- **Recaps + newsletters** — `POST
  /groups/{id}/events/{event_id}/recap` (organiser+) drafts
  a recap from discussion + attendance count + **completed
  alt text only**; `POST /groups/{id}/newsletters/draft`
  `{year, month}` (organiser+) weaves that month's
  APPROVED recaps into a newsletter draft.
- **Memory transparency** — `GET /groups/{id}/memory/runs`
  (member+) lists every prune/ask/brief run with kept and
  dropped chunk ids, passes, model, and cost. `DELETE
  /groups/{id}/memory/runs/{run_id}` and `DELETE
  /groups/{id}/memory/runs` (organiser+) purge run records;
  **summaries are never deleted by a purge**.
- **Pruning settings** — the settings pack gains
  `prune_max_passes` (1–3), `prune_token_budget`, and
  `prune_driver` (`hosted` | `local`). Local pruning runs
  on the group's own OpenAI-compatible endpoint when the
  operator has configured one; extraction always stays on
  the process driver.

## External publishing (Phase 12) — NEW in ZIP-RC

Phase 12 takes the group's events OUT — to a Facebook Page,
a Google calendar, real inboxes, and the group's own
website — through doors a person deliberately uses. The
standing boundary, stated once for the whole phase: **no
live third-party action exists in this build.** No Facebook
Page, Google account, or mail provider is connected; every
surface below is built and proven against recorded fixtures
and stub transports, and the live smoke tests are recorded
as NOT RUN in TEST_REPORT — they await connected accounts
and the project owner's word.

- **Facebook Events.** `GET /groups/{id}/events/{eid}/facebook-draft`
  is B-058's payload (unchanged). `POST …/facebook` is THE
  PRESS (organiser + publish scope): first press creates the
  Page copy, later presses update it, and a press on a
  cancelled event carries the cancellation out. The response
  (and `GET …/facebook`) is `{status, external_id, url,
  differs, action}` — render `differs` as "the Page copy is
  behind; press to update". `DELETE …/facebook` removes the
  copy. Nothing travels without the press, ever.
- **Google Calendar.** `POST /integrations/{id}/google-connect`
  (admin) records the connection verdict on the integration;
  `POST …/google-sync` (organiser + publish) returns
  `{pushed, updated, cancelled, unchanged}`. The scope
  decision, for the UI's words: **sync is one-way OUT** — a
  Google-side edit is never read back, so never promise a
  two-way calendar. Cancellations propagate as
  cancellations; drafts never travel.
- **Mail.** Outward mail now has a real SMTP driver behind
  the B-095 boundary (deployment config; `DEPLOYMENT.md`
  carries the provider + SPF/DKIM notes). Nothing changes
  in the notification payloads the UI already renders —
  except `Notification` gains `send_messenger` +
  `messenger_status` (below).
- **Messenger notifications.** `Notification` responses now
  carry `send_messenger` (bool) and `messenger_status`
  (`"" | "sent" | "driver_unavailable" | "failed" |
  "unlinked"`). Messenger access has NOT been granted to
  this project, so in practice the status is
  `driver_unavailable` whenever the channel was asked for —
  render it as "Messenger isn't connected for this group;
  email carried this", never as a failure of the message.
  The preference switchboard (`GET/PUT
  /users/me/groups/{id}/notification-preferences`) now
  includes `messenger` switches for every category; the
  channel additionally requires the member's B-123
  Messenger account link — a switch alone cannot send.
- **Embed + public page.** `GET/PUT /groups/{id}/embed-origins`
  (organiser read, admin write) manages the register of web
  origins allowed to embed the group's page; an origin is
  `scheme://host` — no path (a path gets a plain-words 422).
  `GET /public/groups/{slug}` returns `{slug, name,
  description, events}` at the B-059 public projection.
  `GET /embed/groups/{slug}` is the widget door: it serves
  the same payload, refuses a present-but-unregistered
  `Origin` with 403, and CORS answers (including preflight)
  are made ONLY for registered origins — an unregistered
  origin gets no `Access-Control-Allow-Origin` at all.
- **Health board.** `GET /groups/{id}/integrations/health-board`
  (organiser+) returns one entry per integration:
  `{integration_id, kind, name, status, last_checked_at,
  last_success_at, last_error, advice}`. `advice` is ONE
  next step in plain words — show it verbatim beside the
  status; `last_error` keeps the outside system's own words
  as the evidence under it.
- **Reconciliation.** `POST /groups/{id}/integrations/reconcile`
  (admin) runs the comparison now and returns `{drift:
  [{event_id, title, connector, kind, external_id}]}` where
  `kind` is `differs` or `missing`. It also runs nightly on
  its own chain, notifying organisers. The UI's words
  matter here: reconciliation REPORTS, it never repairs —
  a missing copy returns at the next press/sync; a differing
  copy stands until a person settles it.

## Security, privacy, and erasure (Phase 13) — NEW, folds into ZIP-Final

Phase 13 re-attacked everything before it and added the
privacy surfaces. What the frontend needs to know:

- **`storage_key` is GONE from source responses (contract
  change).** It was an internal locator, never fetchable —
  no route serves file bytes by key. If your UI needs to
  SHOW a flyer image, the road is a media route authorised
  by flyer/source id through the view door; that route is
  not in this build, so render flyers from their metadata
  (dimensions, alt text) for now and ask us before assuming
  a bytes URL of any shape. Guessed file URLs return 404 —
  that is deliberate, and tested.
- **Group export** — `POST /groups/{id}/export` (owner,
  live session only; API keys get 403): returns a ZIP
  (`Content-Disposition` filename). Offer it as "Download
  everything this group has" in owner settings.
- **Group erasure** — `POST /groups/{id}/erase` (same
  door): body `{confirm_name, confirm_word}` where
  confirm_name is the group's EXACT name and confirm_word
  is the literal `ERASE`. A mismatch is a 422 and nothing
  is erased. Build the confirmation screen to make the
  person type the name — the API demands it anyway. The
  response is a receipt of counts; the group is then 404
  everywhere, its calendar feeds included.
- **Storage quota.** Group settings now carry
  `upload_quota_bytes` (default 104857600; `null` = no
  quota). Uploads that would cross it fail 422
  `quota_exceeded` with a plain-words detail — surface the
  detail text as-is. Show usage as the sum of file sizes
  you already list; the quota counts stored upload bytes.
- **"What do you hold about me?"** — `GET /users/me/data`:
  one payload, sections named in plain words (`account`,
  `my_groups`, `my_api_keys`, `seats_i_created`,
  `my_notifications`, `comments_i_wrote`, `my_rsvps`,
  `my_poll_votes`, `my_attendance`,
  `tasks_assigned_to_me`, `candidates_i_authored`,
  `my_linked_accounts`). Keys appear by prefix + label only
  — never render a "full key" from this payload; there
  isn't one. (Non-technical reader test, B-144: the builder
  read this payload as a member would — section names and
  the permission words below needed no glossary; the one
  wording fix made during the phase was in the digest's
  plain-text rendering, not here.)
- **Source permissions** — `GET /groups/{id}/sources/permissions`
  (any member): `[{source_id, title, kind, added_by,
  permission, added_on}]`. `permission` is the sharer's own
  recorded words, or exactly "No permission note was
  recorded for this source." — render it verbatim; it is
  the audit surface members will quote.
- **Retention** — settings carry `source_retention_days`
  (`null` = keep sources forever; the default). When set,
  raw sources older than the window are removed nightly
  (approved events and candidates stay; candidates show
  their source as deleted). `POST /groups/{id}/retention/run`
  (admin) runs the sweep now and returns counts only.
  Suggested settings copy: "Old source material is removed
  after N days. Events your group approved stay."

## Run it

```sh
uv sync
./run.sh            # or: ./run.sh --demo  (loads the demo seed)
curl http://127.0.0.1:8000/health
```

Demo account (from `seeds/demo.json`): `demo@example.org` /
`demo-password-123`. Bruno collection skeleton in `collections/` —
the health request runs against a fresh server as-is.

## Submission freeze and demo proof (Phase 14) — NEW in ZIP-Final

Phase 14 is the freeze: no new surface, and the contract is
unchanged since B-146 (187 paths, 217 schemas) — nothing here
asks the frontend to move. What this ZIP adds is proof and
paper:

- **ZIP-Final itself** (`gpa-backend-v0.5-2026-10-25.zip`,
  slice range B-001 → B-151) is the submission artifact. Its
  drill log ships inside it (`gpa-backend/FREEZE_DRILL.md`):
  unzipped on a clean profile, the demo seeds and serves cold
  in 27 seconds.
- **The demo story is final** (B-147): Riverbend Community
  Trust + Northside Hiking Club, seeded by `./demo.sh`;
  logins and the story's shape are in `submission/RUN.md`.
  The four demo flows (A flyer, B argument, C newsletter,
  D tonight) are integration tests first — if a beat is not
  in a test, it is not in the video (B-148).
- **The numbers page** `RESULTS.md` now ships at the ZIP's
  top level (it is generated, never hand-typed — B-149).
- **The submission pack** (`submission/` inside the backend
  tree) is the backend's half of the submission paperwork:
  the honest model mapping, the architecture page, run
  instructions, and the scope/waiver note. Cold readers
  start there.
- **Two drill fixes touch how you receive the work, not the
  contract:** release ZIPs now preserve executable bits
  (`demo.sh` runs straight after unzip — Beta/RC did not;
  `sh demo.sh` is the workaround there), and a cold tree now
  creates its database directory itself, so the ZIP runs
  from a bare unzip with no manual setup.
