# Test Report — ZIP-Beta (v0.3, 2026-10-24)

The full suite result for THIS ZIP, from one clean run against
the tree this ZIP was built from (roadmap §8: a ZIP is a
promise — everything the handoff claims is covered here).

## Suite

- **673 passed, 0 failed, 0 errors** (4m38s, pytest, one
  warning: a Starlette TestClient deprecation notice).
- Collected = passed = 673. Reconciliation: 642 at ZIP-Alpha's
  gate (Phase 7) + 31 new Phase 8 tests = 673.
- Contract: 133 OpenAPI paths, 150 schemas — `OPENAPI.json` in
  this ZIP is the exact schema the snapshot test passed
  against; `frontend_types.ts` generated from it.

## Phase 8's new tests (31)

| File | Tests | Covers |
|---|---|---|
| test_notifications_core.py | 4 | B-095 outbox (no inline send), kill-the-mail-server retry + resend, B-102 dead-letter + failure list, bounce webhook |
| test_reminders.py | 11 | B-096 default rules at exact moments, facts in bodies, move-reschedule with no orphans, custom rules, withdrawal; B-100 quiet-hours goldens (New York + Auckland), urgent exemption; B-103 fact-preservation ship/fallback |
| test_change_alerts_invites.py | 5 | B-097 RSVP'd-only alerts, guest-at-invited-address, once-only approval; B-098 public face, guest RSVP attribution, tamper 404, member invite notification |
| test_digest.py | 4 | B-099 golden week in both formats, chain anchoring, settings re-anchor, alt-text law executable |
| test_inbox.py | 5 | B-101 inbox list/mark-read, privacy 404, inbox↔email agreement, SSE nudge, preference switch effect |
| test_migrations_phase8.py | 1 | Migration 0011 up/down/up on a Phase 7-era database |
| test_phase8_gate.py | 1 | B-104: invite → answer → reminder → move → change alert → digest mention, fake clock |

Three assertions in tests/test_rsvps.py were updated as
superseded (the Phase 4 pattern): they counted every `notify`
job as a promotion; since Phase 8 the queue carries all
notifications, so they now select promotion jobs by payload
kind. Their intent — promotions are notified, in line order —
is unchanged and still asserted.

## Evaluation tables (this ZIP)

Extraction (`python -m gpa.ai.evaluation`, fixture driver) —
within tolerance of the recorded baseline:

```
field                  precision  recall correct  pred  gold
--------------------------------------------------------------
decision:text               1.00    1.00       1     1     1
event:cost                  1.00    0.00       0     0     1
event:host                  1.00    1.00       1     0     1
event:place                 0.50    0.50       1     2     2
event:starts                1.00    1.00       2     2     2
event:title                 1.00    1.00       2     2     2
question:text               1.00    1.00       1     1     1
task:owner                  1.00    1.00       1     1     1
task:title                  1.00    1.00       1     1     1
```

Vision (`python -m gpa.ai.vision_evaluation`, fixture driver) —
overall accuracy 0.81 (baseline 0.81, tolerance 0.02):

```
type           cases  accuracy  baseline
clean              1      1.00      1.00
glare              1      0.75      0.75
handwriting        1      0.75      0.75
rotated            1      0.75      0.75
```

## Security posture in this ZIP

- Tokens (invites, API keys, magic links) are hashed at rest
  and shown once — the invite suite proves a tampered token
  creates nothing.
- The public invite face is the B-059 public projection, under
  the same leak discipline as the public calendar.
- Preferences and inboxes are person-only (seats refused);
  another person's inbox item is a 404.
- Migrations 0001–0011 each up/down/up clean; the suite's
  Alembic tests cover 0011 explicitly.

---

# Addendum — Phase 9 (B-112 gate, 2026-10-24; no ZIP)

Phase 9 cut no ZIP (it folds into ZIP-RC at B-138, per the
roadmap's train), so this addendum carries its numbers until
the RC report supersedes it.

## Suite

- **704 passed, 0 failed, 0 errors** — the reconciled clean
  run against the final Phase 9 tree (pytest, one warning: a
  Starlette TestClient deprecation notice). Told plainly, it
  took two runs to earn this line: the first gate run came
  back **703 passed / 1 failed** — the failure was in the
  B-110 test itself, not the code under it (the dry-run test
  asserted the roster was the owner alone, forgetting its own
  fixture's accepted member; the dry run had changed exactly
  nothing, as designed). The same run exposed a latent
  logging defect from the foundation: the request-id filter
  sat on the root LOGGER, whose filters never run for records
  propagated from child loggers, so third-party records
  (httpx) reached the root handler's formatter without a
  request_id and the formatter raised on them; the filter now
  rides the handler, where every emitted record meets it.
  Both fixes landed, and the re-run — temp base on the
  workspace overlay after a /tmp-full run died at 100% tmpfs,
  the standing hazard — is the clean 704 above.
- Collected = passed = 704. Reconciliation: 673 at ZIP-Beta's
  gate (Phase 8) + 31 new Phase 9 tests = 704.
- Contract: 151 OpenAPI paths, 179 schemas (was 133 / 150) —
  `OPENAPI.json` regenerated at this gate is the exact schema
  the snapshot test passed against; `frontend_types.ts`
  generated from it.

## Phase 9's new tests (31)

| File | Tests | Covers |
|---|---|---|
| test_subgroups.py | 5 | B-105 create/enrol/roles, scoping leak tests (list, detail 404, tasks, calendar), delete-refused-while-scoped 409 |
| test_announcements.py | 4 | B-106 organiser-only posting (member 403), seat refused in the service, pinned-first order, expiry + organiser include_expired |
| test_shifts.py | 4 | B-107 headcount slots with windows, claim/release via B-088, gap report golden ("Saturday still needs a driver and a door person.") |
| test_resources.py | 3 | B-108 keeper items, booking rules (cancelled 422, duplicate 409), golden double-booking → resource_overlap flag |
| test_attendance.py | 3 | B-109 marking + idempotence, counts-only reads, the refusal itself (per-person routes 404/405, no identity in aggregates) |
| test_member_io.py | 5 | B-110 messy-CSV golden (dupes, blanks, stray commas) report, dry-run changes nothing, re-import counts, CSV round-trip of a comma name, admin doors |
| test_settings_pack.py | 5 | B-111 defaults, settings change → audit row → two-approver behaviour in one test, review floor opens/closes, validation + doors, budget set/clear |
| test_migrations_phase9.py | 1 | Migration 0012 up/down/up on a Phase 8-era database |
| test_phase9_gate.py | 1 | B-112: import → flyer in → event out → scoped → shifts claimed → attendance → digest sent |

One Phase 4 assertion was updated as superseded (the Phase 8
pattern): tests/test_calendar_views.py's conflict note now
asserts "resource_overlap" — B-108 made Phase 4's deferred
resource-conflict flag real; intent unchanged.

---

# Addendum — Phase 10 (B-122 gate, 2026-10-25; no ZIP)

Phase 10 cut no ZIP (it folds into ZIP-RC at B-138, per the
roadmap's train), so this addendum carries its numbers until
the RC report supersedes it.

## Suite

- **735 passed, 0 failed, 0 errors** — the reconciled clean
  run against the final Phase 10 tree (pytest, one warning:
  a Starlette TestClient deprecation notice), temp base on
  the workspace overlay per the standing /tmp practice.
- Collected = passed = 735. Reconciliation: 704 at the
  Phase 9 gate + 31 new Phase 10 tests = 735.
- Contract: 160 OpenAPI paths, 189 schemas (was 151 / 179) —
  `OPENAPI.json` regenerated at this gate is the exact schema
  the snapshot test passed against; `frontend_types.ts`
  generated from it. The Bruno collections remain the B-010
  skeleton, unchanged (as at the Phase 9 gate).
- Two defects were found and fixed inside the phase's own
  work, both covered by its tests: two `AppError` calls
  passed a `status=` kwarg the error chassis does not take
  (the registry owns statuses — the cross-group purge test
  caught it as a 500), and the recap service's viewer-less
  discussion read met the visibility bright line (a
  viewer-less read sees no events at all); the recap now
  reads at the organiser role its door guarantees.

## Phase 10's new tests (31)

| File | Tests | Covers |
|---|---|---|
| test_pruning.py | 4 | B-113 golden 500-message corpus (recall floor 100%, kept ≤25% of tokens), approved-history-only corpus scope, PruneRun written, the not-embedding-RAG wording + import audit |
| test_migrations_phase10.py | 1 | Migration 0013 up/down/up |
| test_pruning_multipass.py | 5 | B-114 pass-2 recovery of a dropped landmine, pass cap, ceiling, token-budget stop (zero calls, all dropped, flagged), settings round-trip + refusals |
| test_ask.py | 5 | B-115 verified answer with chunk-linked citations, the unverifiable-answer acceptance (fabricated quote → the refusal), gap-pass rescue, nothing-relevant refusal, stranger 404 |
| test_summaries.py | 2 | B-116 summary → 2 pending candidates (system:summary, from_summary), zero Decision/Task rows written; empty thread refused plainly |
| test_briefings.py | 1 | B-117 golden briefing carries the August landmine; role gaps from data; the run's kept set names the landmine chunk |
| test_recaps.py | 1 | B-118 the loop closes: recap draft → approval → newsletter draft → approval → newsletter is a Source and its golden phrase survives a later prune; drafts are not memory; double approval 409 |
| test_local_driver.py | 5 | B-119 wire shape + honest provenance (local model id, zero cost), text-only refusals, config refusals, per-group selection with no silent fallback, driver-swap same-survivors |
| test_memory_runs.py | 3 | B-120 runs inspectable (kept/dropped/model/cost), purge one then all with summaries surviving, organiser door + cross-group purge is a not-found |
| test_pruning_evaluation.py | 2 | B-121 golden set scored through the engine; floors; the deliberate miss asserted by name; table renders |
| test_phase10_gate.py | 2 | B-122 the three demos (ask, briefing, recap) in mock mode (honest refusal) and live mode (live wire path, content-aware stub transport) |

## Pruning evaluation (B-121)

The golden pruning set — human-marked relevant chunks,
scored through the real engine against a recorded judge
(deliberately imperfect, so the table measures rather than
flatters):

| Question | Chunks | Relevant | Kept | Recall | Precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| Where do club nights happen? | 8 | 2 | 2 | 1.00 | 1.00 |
| What did we decide about food for the supper? | 10 | 3 | 4 | 1.00 | 0.75 |
| Who is bringing the marquee and when? | 9 | 2 | 1 | 0.50 | 1.00 |
| What warnings were given about the venue? | 12 | 3 | 3 | 1.00 | 1.00 |
| **Mean** | | | | **0.88** | **0.94** |

## The honest boundary

No real Meta model endpoint or credential exists in this
environment. The gate's "live mode" is the hosted driver's
real code path — wire shape, headers, retries, parsing —
exercised against a stub transport whose answers are
content-aware (it reads the prompt it was sent and judges
the chunks in it). Fixture and mock remain the demo drivers;
nothing in this phase pretends a real model judged anything.

---

# Addendum — Phase 11 (B-130 gate, 2026-10-25; no ZIP)

Phase 11 cut no ZIP (it folds into ZIP-RC at B-138, per the
roadmap's train), so this addendum carries its numbers until
the RC report supersedes it.

## Suite

- **768 passed, 0 failed, 0 errors** — one clean run against
  the final Phase 11 tree (pytest, one warning: a Starlette
  TestClient deprecation notice), temp base on the workspace
  overlay per the standing /tmp practice, totals read from
  the run's own report file.
- Collected = passed = 768. Reconciliation: 735 at the
  Phase 10 gate + 33 new Phase 11 tests = 768.
- Contract: 174 OpenAPI paths, 204 schemas (was 160 / 189) —
  `OPENAPI.json` regenerated at this gate is the exact schema
  the snapshot test passed against in the same run;
  `frontend_types.ts` generated from it. The Bruno
  collections remain the B-010 skeleton, unchanged.
- No defect was found in earlier phases' code. Two finds
  inside the phase's own new work, both fixed and covered:
  the outbound service's first registration imported the
  jobs module before this app's handler registry existed
  (registration moved to package import, the pattern the
  other job services use), and a B-124 test-data slip (a
  Samtal-side edit that moved only an event's start; the
  B-077 move rule correctly carried the end — the test now
  moves both, as a real edit would).

## Phase 11's new tests (33)

| File | Tests | Covers |
|---|---|---|
| test_samtal_connector.py | 6 | B-123 hall sync chunk-for-chunk vs paste; identity by link only (name-collision trap); link rules (active members, duplicate 409); cursor re-sync; unknown connector plain-words refusal; unavailable Samtal records its refusal |
| test_samtal_calendar.py | 4 | B-124 round trip (out → Samtal edit → candidate → approve → push-back); rejection leaves the event standing; Samtal-side cancellation is a candidate; drafts and foreign hall events never travel |
| test_messenger_fallback.py | 3 | B-125 access state honest; sync refuses in plain words naming paste, refusal recorded; a Messenger export through paste ingest lands identically |
| test_webhooks_in.py | 6 | B-126 signed delivery archives chunks; bad signature / stale stamp / unknown token refuse and archive nothing; replay accepted exactly once (409); revoke closes the door; neither token nor secret exists in the DB file |
| test_integrations_framework.py | 5 | B-127 config sealed at rest (marker secret absent from the DB file; internal read-back round-trips); health verdicts recorded incl. the Samtal ping refusal; tampered envelope raises; admin gates and cross-group 404 |
| test_outbound_webhooks.py | 4 | B-128 signed delivery, verified with the shown secret; retry after a 500 then success (attempts recorded); dead at the attempt cap with the reason kept; subscription filtering hears only its kinds |
| test_agent_pack.py | 3 | B-129 the `agent` tag covers the pack and nothing outside it; the scope guide names every scope and never blurs approval; the secretary-seat example runs in mock mode (hall → draft → 403 seat_cannot_approve) |
| test_migrations_phase11.py | 1 | Migration 0014 up/down/up on a Phase 10-era database |
| test_phase11_gate.py | 1 | B-130: hall → sync → extraction → approval → event → back onto the hall's calendar, the whole bridge in one story |

## The honest boundaries

- **Samtal is the author's real, live system, and this
  suite never touches it.** The connector is built and
  tested against recorded fixtures and the
  `FixtureSamtalClient` test double only. No real Samtal
  address, token, or key exists anywhere in this
  repository; a deployment points the connector at the
  group's own Samtal under its own consent record.
- **Messenger access has not been granted.** B-125 ships
  the documented fallback — the paste/export ingest path —
  and the connector refuses sync in plain words naming that
  road. No live Messenger integration is simulated,
  stubbed, or implied anywhere.
- The Facebook Events and Google Calendar integration kinds
  exist in the framework with health checks that honestly
  report "not connected yet"; their live slices are
  Phase 12's, exactly as the roadmap orders.

## Repair note — B-126 replay acceptance (2026-10-25, post-gate)

The gate numbers above stand as the gate's record. Independent
verification afterwards FAILED the suite twice — a forge-timer
run during the gate and a separate clean verification run both
read **767 passed, 1 failed**:
`test_webhooks_in.py::test_replay_is_refused_and_never_duplicates`
— the replayed delivery returned 201, its chunks appended a
second time.

**Root cause — the test's construction, proven; plus a latent
service gap.** A replay is byte-identical: same body, same
timestamp, same signature. The test's `deliver()` helper
computes `int(time.time())` fresh on EVERY call, so its
"replay" is a replay only if both calls land in the same
wall-clock second. In the failing verification run's own
access log, ~1 s elapsed under load between the endpoint's
creation (03:42:18.808) and the first delivery (03:42:19.787);
the second boundary fell between the two `deliver()` calls,
the second delivery was signed with a NEW timestamp, and the
door — correctly — accepted a distinct, valid delivery.
Recreated deterministically with the signing stamps recorded:
delivery 1 signed …393, delivery 2 signed …394 → 201. With
the stamp pinned, the same attack is refused 409 every time:
the door's exactly-once (receipt SELECT, then the receipt
written in the SAME transaction as the chunks, under
UNIQUE(endpoint, signature) in both the model and
migration 0014) held throughout.

The latent gap the investigation found in the service: two
identical deliveries truly concurrent could both pass the
receipt SELECT before either commits, and the loser's commit
then raised an unhandled IntegrityError — the constraint
fired, but as a 500, not the 409 the guarantee promises.

**Fix.** (1) The test pins one timestamp for both deliveries
(a true byte-identical replay; every assertion unchanged)
and gains a mirror test,
`test_resigned_fresh_delivery_is_not_a_replay`, pinning that
a re-signed fresh delivery lands — the receipt keys on the
signature, never on payload resemblance. (2)
`services/webhooks_in.receive_delivery` catches the
acceptance commit's IntegrityError, rolls the dead
transaction back (the loser's chunks go with it), and
refuses through the same replay path as a sequential replay
— 409, audit-logged.

**Proof.** `tests/test_webhooks_in.py`: 10 consecutive runs,
7 passed each (the file's 6 + the new mirror test). Full
suite, one clean run against the repaired tree, totals read
from the run's own report file: **769 passed, 0 failed,
0 errors** (768 at the gate + 1 new mirror test = 769).

---

# Addendum — Phase 12 (B-138 gate, 2026-10-25; ZIP-RC)

Phase 12 cuts **ZIP-RC** (the train's v0.4 release
candidate): Phases 9–12 fold into it, as promised at the
B-112 / B-122 / B-130 gates.

## Suite

- **797 passed, 0 failed, 0 errors** — one clean run
  (`./test.sh`, exit 0) against the final Phase 12 tree,
  temp base on the workspace overlay per the standing /tmp
  practice, totals read from the run's own report file
  (`/tmp/gpa-test-output.txt`: "797 passed, 1 warning in
  1919.67s"; the one warning is the Starlette TestClient
  deprecation notice).
- Collected = passed = 797. Reconciliation: 769 at the
  Phase 11 close (768 gate + the B-126 repair test) + 28
  new Phase 12 tests = 797.
- Contract: 182 OpenAPI paths, 212 schemas (was 174 / 204)
  — `OPENAPI.json` regenerated at this gate is the exact
  schema the snapshot test passed against in the same run;
  `frontend_types.ts` generated from it.

## Phase 12's new tests (28)

| File | Tests | Covers |
|---|---|---|
| test_migrations_phase12.py | 1 | Migration 0015 up/down/up (external_url, messenger columns, embed origins) |
| test_facebook_events.py | 3 | Fixture life create → differs → update → cancel → remove; plain-words refusals (no integration / incomplete config / unavailable client); member 403 + draft 404 |
| test_google_calendar.py | 4 | Connect verdict recorded; sync upserts/edits/cancellation propagation; one-way scope (a Google-side edit is never read back and is NOT silently overwritten); plain-words refusals |
| test_mail_provider.py | 5 | SMTP message shape/TLS/login/Message-ID via a recording stub; refusal → TransportError; startup validation naming both missing settings; end-to-end delivery through the SMTP driver; webhook signature (unsigned/wrong 401, correct HMAC records the bounce) |
| test_messenger_notifications.py | 4 | Opt-in needs link AND switch; driver_unavailable recorded while email carries; capture driver marks the leg sent; the quiet-hours hold is shared by every channel |
| test_embed_public.py | 5 | Register normalises/validates (paths refused); public page carries public facts only (leak check); CORS answers only for registered origins (incl. preflight both ways); embed door 403s an unregistered origin; a private group has no page anywhere |
| test_health_board.py | 3 | Broken-token advice names the reconnect/fresh-token step; unconfigured + unchecked advice; a working integration needs nothing (+ member 403) |
| test_reconcile.py | 2 | Planted drift (Facebook edit + Google deletion) reported to organisers only, outside copies untouched; settling by the deliberate roads makes the next run clean; the nightly chain anchors on connect and perpetuates itself |
| test_phase12_gate.py | 1 | B-138: one event's full outward life — draft → press → sync → move → both doors repair → clean reconcile → approved re-announce (mailbox + Messenger leg driver_unavailable) → cancellation by both doors → registered-origin embed |

## The honest boundaries

- **No live third-party action of any kind.** No Facebook
  Page/group account, Google account, or mail provider is
  connected for this project in this environment, and
  publishing/sending is the project owner's word alone.
  B-131 ships the recorded-fixture integration test; the
  roadmap's "live smoke test on a throwaway event" is
  recorded as **NOT RUN — awaits a connected account and
  Volmarr's word**. B-132 likewise: OAuth + sync built and
  proven against recorded fixtures/stub transport; the
  one-way-out scope decision is stated in the handoff.
  B-133: the provider driver is fully wired at the B-095
  transport boundary with config + SPF/DKIM deployment
  notes (DEPLOYMENT.md); the "real digest in a real inbox"
  acceptance is recorded as a **pre-submission checklist
  item, not run**. B-134: B-125's Messenger access does not
  exist, so the slice ships the clean `driver_unavailable`
  path, with the shared opt-in/quiet-hours tests proving
  the email/in-app channels carry identical rules.
- All fixtures are invented; no real credentials,
  endpoints with real tokens, or user data anywhere in
  the repo. Integration secrets ride the B-127
  sealed-config framework.
- Samtal remains fixtures-only, exactly as Phase 11 left
  it — this suite never touches the live village.

## Finds inside the phase (all fixed, all covered)

- **A latent red in the phase's own foundation:** the
  B-131 foundation commit grew the notification `CHANNELS`
  vocabulary to include `messenger`, but the preference
  schema still pinned `^(email|in_app)$`, so the Phase 8
  inbox test failed until B-134's schema half landed
  (committed with B-133, noted in that commit). The
  per-slice affected-test runs for B-131/B-132 did not
  include the inbox file — the full gate run is what
  guarantees the whole tree, and it is green.
- **Reconciliation's settings threading:** the first
  reconcile build unsealed integration configs with the
  process-default settings; a config sealed under other
  settings reads back as empty, which reported every
  Google copy "missing". `reconcile_group` now takes the
  caller's settings (the router passes the app's own; the
  nightly job uses the deployment env, which is what
  sealed them there).
- **Press/sync repair split:** an early draft let a press
  silently overwrite an EDITED outside copy — a direct
  violation of B-132's pinned one-way law ("not silently
  overwritten by us either"). The shipped rule: a copy
  DELETED outside is restored by the organiser's own
  press/sync; an edited copy stands until a person
  settles it. No defect was found in Phases 0–11 code.

---

# Addendum — Phase 13 (B-146 gate, 2026-10-25; no ZIP — folds into ZIP-Final at B-151)

Phase 13 re-attacked everything built before it: threat
model, authorisation fuzz, upload/injection/output abuse
suites, group export + hard erasure, the privacy surfaces,
a secrets/config audit (pip-audit run for real), and a
scripted backup/restore drill. This addendum was written by
the phase's builder after the forge timer had committed
B-146 (commit `aced215`, which found the slice complete in
the tree and committed it unchanged); the numbers below say
plainly whose run each came from.

## Suite

- **835 passed, 0 failed, 0 errors** at the Phase 13 gate.
  Reconciliation: 797 at the Phase 12 close + 38 new
  Phase 13 tests = 835, collected = passed. The full suite
  was run green against this tree in the main tree by the
  forge timer at the B-146 commit; independently, the
  B-146 restore drill (below) ran the ENTIRE suite in a
  clean restored tree built with a fresh `uv sync`: its own
  log's tail reads "835 passed, 1 warning in 2235.12s" (the
  one warning is the Starlette TestClient deprecation
  notice). The builder's own main-tree gate run was still in
  flight when the timer's commit landed and was stood down
  rather than trusted against a moved tree — no totals are
  claimed from it.
- Contract: 187 OpenAPI paths, 217 schemas (was 182 / 212)
  — `OPENAPI.json` regenerated at this gate (five new
  routes: group export, group erase, retention run, source
  permissions, /users/me/data; `SourceResponse.storage_key`
  removed, see Finds); `frontend_types.ts` from it; the
  snapshot test passed against it in the drill's restored
  tree run.
- Later state, for orientation: with Phase 14's first
  slices (B-147→B-149, built by the forge timer) the tree
  stands at 841 tests / 149 of 350 slices.

## Phase 13's new tests (38)

| File | Tests | Covers |
|---|---|---|
| test_threat_model.py | 6 | The in-repo THREAT_MODEL.md stays true (named tests exist, every router covered, Case #1 present); seat is nobody abroad; meta/health leak nothing; anonymous 401s |
| test_authz_fuzz.py | 6 | Two marked groups × 8 attacker principals × ~400 seeded reads/writes/id-swaps: zero 2xx, zero marker bytes, zero state change; positive control; capability surfaces refuse everyone for a private group |
| test_upload_hardening.py | 7 | EICAR/MZ/ELF/ZIP refused by content; image re-encode strips appended + tEXt payloads; per-group quota (`quota_exceeded` 422, others unaffected, null lifts); default quota shown; storage keys absent from responses; guessed file URLs 404/405; ICS door = view door (drafts refuse for every role) |
| test_migrations_phase13.py | 1 | Migration 0016 up/down/up (upload_quota_bytes backfilled 104857600, source_retention_days NULL) |
| test_injection_safety.py | 6 | One-pass prompt render (placeholder smuggling closed; benign output byte-identical across all 8 templates); poisoned corpus + obedient fixture model → at most one PENDING candidate, no publish, no mail; digest HTML escapes / text doesn't; iCal folding (exactly 1 VEVENT); JSON carries markup as data |
| test_erasure.py | 2 | Export is a real ZIP without secrets (key NAMES only); erase: member 403, API-key 403, wrong name 422 intact, correct erase → per-table counts, feeds 404, raw DB + WAL grep clean for group A and intact for group B, shared storage object survives, audit keeps only the fact |
| test_privacy_surfaces.py | 4 | /users/me/data complete against stored hashes (none appear); source permission records in plain words; retention expiry (no tombstone, candidates stamped, events stand, other group untouched); manual run + no-window honesty |
| test_secrets_audit.py | 5 | Prod refuses known-default secrets and <32-char secrets, accepts a real one; dev unaffected; access log redacts token-carrying segments (feed/invite/email-in/webhook) incl. the httpx line; no tracked .env |
| test_backup_restore.py | 1 | Backup → restore round-trip is byte-identical (rows + storage); a tampered bundle is refused as damaged and places nothing |

## Dependency audit (B-145) — ran for real, no waiver

`uv export --format requirements-txt --no-hashes
--all-groups` (38 `==` pins, 45 resolved) →
`uvx pip-audit -r <export>`, run 2026-10-25 in this
environment against the live vulnerability database:

```
No known vulnerabilities found
Name                             Skip Reason
-------------------------------- -----------------------------------------------------------------------------------------------
group-planning-assistant-backend Dependency not found on PyPI and could not be audited: group-planning-assistant-backend (0.1.0)
```

The single skip is the project package itself (not
published to PyPI) — every actual dependency was audited.
No waiver was needed.

## The restore drill (B-146) — five runs, two real defects

`scripts/restore_drill.py` plants marker data, backs it up
(`scripts/backup.py`), copies the tree clean, restores
(`scripts/restore.py`, verify-then-place), builds with a
FRESH `uv sync --extra dev`, runs the whole suite in the
restored tree, and reads the marker back through the API.

- Runs 1–3 failed at the suite step on environment leaks
  from the drill's own launcher (a leaked `VIRTUAL_ENV`,
  then the main tree's venv bin on `PATH`, then a bare
  runtime-only `uv sync` with no test runner). The script
  now scrubs all three and syncs `--extra dev`; the lessons
  are written into DEPLOYMENT.md's runbook section.
- Run 4's suite completed: 833 passed, **2 failed** — both
  failures were real defects the drill existed to catch:
  (1) `test_erase_then_grep` failed 5/5 in the restored
  tree (newer SQLite) while passing 5/5 in the main tree:
  the VACUUM's own output rides the WAL, so the raw main
  file still showed the pre-vacuum image — logically
  erased, raw bytes stale. **Fix (in B-143's service, at
  the root): erase checkpoints AGAIN after VACUUM**, so
  the on-disk file is the vacuumed image. (2) The secrets
  test demanded a git checkout; a restored/release tree
  has none. **Fix: with no git metadata, the same
  no-.env guarantee is proven by walking the tree.**
- Run 5 (final code): plant 6.0s, backup 0.5s, copy 0.9s,
  restore 0.1s, build 3.1s, **suite step ok in 2245.0s —
  835 passed**. Its verify step then failed on the DRILL
  SCRIPT's own call to a "list groups" route that does not
  exist (405) — the read-back now goes through
  `/users/me/data`, the door a person uses; re-run against
  run 5's restored tree + data it prints "marker data
  verified in the restored instance".

## Finds — defects in earlier code (all fixed at the root, all covered)

- **B-140 — `GET /api/v1/jobs/{id}` answered ANY signed-in
  caller.** A job naming a group (payload, or via its
  source/notification/webhook) is now that group's
  members-only; everyone else gets 404. Group-less
  internal jobs unchanged.
- **B-141 — `SourceResponse` exposed `storage_key`**,
  against the project's own law (D-17: storage keys never
  appear in the API). Field removed — the phase's one
  deliberate contract break, recorded in CHANGELOG's
  Breaking changes. `thumbnail_key` deliberately STAYS in
  flyer projections under a binding rule: any future media
  route authorises by flyer/source id through the view
  door, never by raw key.
- **B-142 — plain-text digests carried HTML entities**
  (`&lt;` in text mail): the digest Jinja env escaped by
  the `.j2` suffix, which both templates share. Escaping
  now follows the file kind via a named callable; HTML
  still escapes.
- **B-145 — httpx/httpcore log full request URLs at INFO**
  (feed tokens, invite tokens — a second copy of every
  token in any log). Quieted to WARNING in the app's
  logging setup; the app access log redacts token-carrying
  path segments itself.
- **B-146 — backup.py failed on nested storage
  directories** (parents not created on copy); found by
  its own round-trip test, fixed there.
- The drill-found erasure checkpoint and git-less secrets
  test fixes are recorded in the drill section above.

## The honest boundaries

- **Group content is NOT field-sealed in this build.**
  THREAT_MODEL.md names it the largest accepted gap;
  sealing is Phase 17 work (B-167), and the audit log is
  append-only by discipline, not yet a hash chain
  (B-170). Neither is claimed anywhere as built.
- Erasure/export were exercised only on groups the suite
  itself creates — no real user data exists in this
  environment.
- Retention expiry is deliberately NOT recoverable (no
  tombstone): it is a privacy act; manual source deletion
  keeps its tombstone + restore, unchanged.
- No ZIP this phase, per the train: Phase 13 folds into
  ZIP-Final at B-151.
