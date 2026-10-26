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

# Addendum — Phase 14 (B-151 gate, 2026-10-25; ZIP-Final)

Phase 14 froze the build and proved the demo: the demo data
became the story (B-147), the demo script became tests (B-148),
the numbers page became generated (B-149), the submission pack
was written (B-150), and the freeze drill unzipped ZIP-Final
on a clean profile and ran the demo cold (B-151). B-147 →
B-149 were built by the forge timer; B-150 and B-151 by this
phase's builder. The numbers below are from the builder's own
gate run, against the final code tree (commit `d951f81`).

## The four demo flows (read these first)

B-148's acceptance: every click in the submission video script
is first an integration test. These are the four.

| Flow | The story | The test | Driver |
|---|---|---|---|
| A — the flyer | A photographed poster is read, the draft waits in the review queue with region provenance, a person approves, the event publishes with the flyer attached and lands on the agenda | `tests/test_phase5_gate.py` (Phase 5 gate) | fixture |
| B — the argument | The hiking club's 40-message trip argument is pasted in and extracted into 1 event + 3 tasks + 2 decisions + 2 questions; the reviewer corrects the place and confirms the volunteered kayak owner by naming the member (the AI never assigns an owner); approval mints them all; the conversation continues in the minted event's thread | `tests/test_phase7_gate.py` (Phase 7 gate) | fixture |
| C — the newsletter | The Trust's 12-section newsletter becomes exactly 12 drafts; re-extraction supersedes, never doubles; a reviewer edits a date, approves, and the event lands on the agenda | `tests/test_demo_flows.py` (B-148) | fixture |
| D — Saturday night | The tonight view: the pinned event first, this evening's events in order, tomorrow's excluded | `tests/test_demo_flows.py` (B-148) | fixture |

## Suite

- **853 passed, 0 failed, 0 errors** at the Phase 14 gate,
  from the builder's own full run against the final code
  tree: the log's tail reads "853 passed, 1 warning in
  2516.95s" (the one warning is the Starlette TestClient
  deprecation notice). Reconciliation: 841 at the B-149
  checkpoint (Yrsa's verified run) + 8 (B-150 pack coherence)
  + 3 (B-151 release-ZIP guard) + 1 (B-151 cold-tree
  regression) = 853, collected = passed.
- Contract: **unchanged** — 187 OpenAPI paths, 217 schemas.
  Phase 14 added documents, one release-script behaviour,
  and one chassis guarantee; no route moved.
- Two earlier gate attempts were stood down rather than
  trusted, and are claimed for nothing: the first when the
  tree moved under it (the drill's fixes were landing), the
  second when the B-151 drill found the database defect in
  `db.py` mid-run. The totals above are the third run,
  launched against `d951f81` and read from its own log.
- State: **Phase 14 complete — 151 of 350 slices.**

## Phase 14's new tests (12)

| File | Tests | Covers |
|---|---|---|
| test_submission_pack.py | 8 | The submission pack cannot drift: documents exist; the decided licence is stated in its ruled words on the cover and the waiver; the superseded licence posture appears nowhere in the pack; the waiver names Samtal, Mythic Engineering, and Project Aesir and the one Phase 11 carve-out; the model mapping carries its never-ran-live statement and quotes the shipped tier defaults from `.env.example`; the architecture page carries its diagram and tour; the cover and run pages point at evidence files that exist |
| test_release_zip.py | 3 | The release script carries Unix modes into the archive (executable and plain mappings), and the tree's entry-point scripts are executable at the source |
| test_foundation.py | +1 | A `Database` opens on a path whose parent directory does not exist yet — the cold-tree regression (below) |

B-147 → B-149's six tests (demo data, Flows C/D, the numbers
page's byte-identical regeneration) were counted at the
B-149 checkpoint and are part of the 841 above.

## The freeze drill (B-151) — four runs, two real defects

The full log ships inside ZIP-Final at
`gpa-backend/FREEZE_DRILL.md`. In brief: **run 1** failed at
`./demo.sh` — Permission denied: the release script wrote
every ZIP entry at 0644, so the entry-point scripts landed
non-executable (a packaging defect shared by ZIP-Beta and
ZIP-RC; fixed in the script, `53ad1c5`). **Run 2** failed at
the seed — "unable to open database file": a cold tree has
no `data/`, and the `Database` chassis never created the DB
file's parent directory (the server had always been rescued
by storage/outbox creation making it as a side effect; fixed
in `db.py`, `d951f81`). **Run 3** failed in the drill script
itself, which read back through a route that does not exist
(corrected to `/users/me/data`; no artifact change). **Run 4
was green end to end**: unzip 0 s, fresh `uv sync` 2 s, seed
20 s (7 users, 3 groups, 2 sources, 8 events, 1
announcement), boot + health 4 s, login as the demo owner,
and the story read back — 27 seconds cold, total.

The drilled candidate (sha256 `609d0484…`, built from
`d951f81`) and the shipped ZIP differ only in documentation:
the shipped ZIP was rebuilt by the same script at the gate-
papers commit so this report and the drill log could be
inside it. No code, test, or contract file differs.

One process find, recorded plainly: the permission guard's
first commit (`53ad1c5`) landed while its own test was red —
the test used a 1970 timestamp the ZIP format refuses, and
the committing chain read pytest's exit code through a
`tail` pipeline, masking the failure. The test was defective,
not the fix; it was corrected in `05ec22b` and every gate
number in this addendum comes from runs launched after that
correction.

## The honest boundaries

- **ZIP-Beta (v0.3) and ZIP-RC (v0.4) carry the permission
  defect** run 1 found: their `demo.sh` / `run.sh` /
  `test.sh` land non-executable after unzip (workaround:
  `sh demo.sh`, or `chmod +x`). ZIP-Final supersedes both.
- The standing Phase 11–13 boundaries stand unchanged: no
  live third-party action has ever been taken by this
  codebase; B-131's Facebook live smoke test is NOT RUN;
  B-133's real-inbox check is a pre-submission checklist
  item in DEPLOYMENT.md; Messenger ships the B-125 fallback;
  the live AI driver has never run against a real endpoint;
  the Bruno collections remain the B-010 skeleton; group
  content is not field-sealed (B-167) and the audit log is
  not yet a hash chain (B-170).
- The submission pack discloses the same boundaries in its
  own words (`submission/WAIVER_AND_SCOPE.md`): only the
  jointly built work is submitted; Samtal, Mythic
  Engineering, and Project Aesir are pre-existing and are not
  part of the submission, the sole carve-out being the
  Phase 11 Samtal integration surface as it exists in this
  repository.


# Addendum — Phase 15 (B-156 gate, 2026-10-25; deployment & operations)

Phase 15 made the backend operable by someone who is not
its author: promotable to PostgreSQL, installable from a
script, frontable by a proxy from a written guide,
observable at /health with a one-page runbook, and
upgradable in place with a plain-words rollback. No ZIP
this phase (§8's train ended at ZIP-Final).

## Environment truth table — what "tested" meant here

| Claim | How it was proven | Extent |
|---|---|---|
| PostgreSQL parity | Real PostgreSQL **16.2** server process; binaries from the `pgserver` pip wheel (v0.1.4), run as a throwaway cluster as user `pgdev`, port 54329, trust auth on 127.0.0.1 | Parity file 4/4 (migration chain up/down/up; mover over the full demo story with per-table count+hash verification; core flow + group erasure; sealed-secret absence). Full suite in PG mode at this gate — totals below. Mid-gate, the build VM was reset under the work: the first SQLite gate run died at ~68% (all green to that point), the `pgdev` user and group traversal went with the reset, and the cluster (binaries + data, preserved in the workspace) was relocated to a `pgdev`-owned tree at `/tmp/pgrun` and restarted — same server, same version, same route. Both gate suites were then run from the top. |
| Installer (B-153) | `deploy/install.sh` run end-to-end into a throwaway prefix on this VM: real venv from the lock, env file stat-proven **0640 root:service-group**, zero template tokens left, `systemd-analyze verify` silent on all four GPA units, installed tree booted and answered /health ok | **NOT RUN: installs on a real Raspberry Pi-class machine and on a real cloud VM** — no such machines are reachable from this VM. The exact step that performs them: `sudo deploy/install.sh /path/to/unpacked-release` on the target, then `systemctl start gpa gpa-worker` and `curl http://127.0.0.1:8000/health`. |
| Proxy guide (B-154) | Real **Caddy v2.11.7** (official binary) in front of a real backend, TLS from Caddy's internal CA, guide followed literally | Proven: /health 200 over HTTP/2 + HSTS header; 11 MiB body → **413 from Caddy**; feed request reached the app (404) with the access log recording `calendar/feed/REDACTED` and the token string nowhere in the log. **NOT RUN: nginx** — no nginx binary is obtainable here (apt has no network); the example ships reviewed, and PROXY.md says so. Completes where nginx exists with: `nginx -t -c deploy/nginx.conf.example` then the same three curl assertions. |
| Runbook (B-155) | All five failures reproduced as tests (`tests/test_runbook.py`, 5 passed) | Mail: failing transport → failure recorded → transport fixed → resend delivered. Quota: budget cap → queue `paused: budget` → cap raised → extraction proceeded. Disk: a real **1 MB tmpfs** mounted under storage, filled until writes failed and /health degraded, freed and healed. Token: `access_token_minutes=-5` (zero still validates inside its birth second) → 401 → fresh login 200. Job stuck: stale `running` row → /health `stuck_running ≥ 1` → the real CLI listed it unchanged, `--apply` requeued it, the worker drained it to done. |
| Upgrade drill (B-156) | `scripts/upgrade_drill.py` between the two real release ZIPs | **PASS.** v0.4 installed fresh (1.7 s), marker user+group written through the v0.4 API (8.8 s), `scripts/upgrade.py` ran backup → migrate → smoke → swap (12.8 s), proof (8.1 s): migration stamp none → `0016_phase13_privacy`, marker intact and logging in through the upgraded tree, previous tree whole, bundle verified and carried. First drill run failed at the final boot and caught two real defects, both fixed at root (editable-install `.pth` orphaning; path-bound venv shebangs — see the B-156 commit). |

## Defects this phase found in earlier work

1. **Erasure deadlocked on PostgreSQL** (B-152's parity
   run): the request session's storage-sweep read
   transaction held locks the compact connection's
   `VACUUM (FULL)` needed; each waited on the other
   (pg_stat_activity showed the pair; the test hung nine
   minutes pre-fix, passes in ~24 s post-fix). `erase_group`
   now ends the session's transaction before compacting.
   SQLite's locking never formed the circle.
2. **/health could 500 while reporting health** (B-155's
   tests): a database answering SELECT 1 without a schema
   (mid-migration) raised out of the unguarded jobs query.
   The jobs check now reports error and marks the service
   down.
3. **A swapped-in tree would not boot** (B-156's drill,
   twice over): uv's default editable install names the
   staging path in a `.pth`, and console-script shebangs
   name the venv's interpreter absolutely — both die when
   the tree is renamed into place. Staged/serving venvs
   build `--no-editable`, and upgrade.py rebuilds the
   serving venv at its final address after the swap.
4. **Deep /health leaked the engine to anonymous callers**
   (this gate's SQLite run — its one failure, against
   Phase 13's `test_meta_and_health_leak_nothing`): B-155's
   checks published `engine: "sqlite"` on the
   unauthenticated surface the threat model pins to
   liveness + version only. Fixed forward in `9d8d7cd`:
   the deep checks are disclosed to signed-in callers
   (resolved softly through `deps.get_principal`); the
   anonymous answer is the original minimal one, and the
   threat test passes unchanged. Because the fix changed
   behaviour, both gate suites were re-run from the fixed
   tree — the totals below are from those runs, not the
   first attempt.
5. **PG-mode isolation vs module-scoped worlds** (this
   gate's PG run): conftest's per-test TRUNCATE wiped the
   one module-scoped world in the suite
   (test_authz_fuzz.py) — and no function-scoped reset can
   precede a module fixture, since broader scopes build
   first. Isolation now mirrors SQLite's own granularity:
   a session-scoped reset before anything is built,
   per-test resets elsewhere, and the shared-world module
   entered empty and left alone (`f9ce3a2`). One more
   skip-list miss was caught the same hour:
   `test_sqlite_pragmas` is a SQLite-file test by nature
   (`9263ca4`).
6. **Alembic's fileConfig silenced the app's loggers**
   (this gate's PG run): migrations/env.py used the
   default `disable_existing_loggers=True`, so the
   mover's in-process upgrade disabled the access logger
   for the rest of the process — visible only on PG,
   where the parity tests actually run in-process.
   `disable_existing_loggers=False` now (`612ba7d`).

## Gate totals (final tree, both engines)

The suite was run in two halves per engine (files a–m,
then n–z, each from its own log, back-to-back against the
same tree) after the build VM's resets killed two
single-run attempts mid-flight; the halves' logs are the
record (`~/workspace/.verify-tmp/p15-gateH1/H2.log`,
`p15-gatePG1c/PG2b.log`).

- **SQLite: 877 passed, 4 skipped, 0 failed**
  (558 + 319; the 4 skips are the parity tests standing
  down without a PG admin URL, by design).
- **PostgreSQL: 865 passed, 16 skipped, 0 failed**
  (543 + 322; the 16 skips are the named SQLite-file tests
  whose guarantees the parity file re-proves, and the
  parity tests run here — the 12-test passed delta is
  exactly the 12-test skip delta).
- **Collected on both engines: 881 = 853 (Phase 14 gate)
  + 28 new** (parity 4, packaging 4, proxy 3, health 5,
  runbook 5, upgrade 7). Contract unchanged: 187 paths /
  217 schemas. Progress: **156/350 slices, Phases 0–15
  complete.**

# Addendum — Phase 16 (B-160 gate, 2026-10-25; scale and second integrations)

Phase 16 scaled what Phase 15 made operable: the job
system claims work safely under many instances with an
external-queue option behind the same door, two more live
sources joined the framework as drivers, Google gained a
queue-bound inbound road, and the API got a developer pack
an outsider can actually build on.

## Environment truth table — what "tested" meant here

| Claim | How it was proven | Extent |
|---|---|---|
| DB claim protocol (B-157) | Real threads, real sessions: 4 worker threads drain 24 jobs (audit-row probe: exactly one run per job); a barrier-synchronised 4-way claim storm on ONE job (exactly one winner); a claim stepping over a head row held by another instance's uncommitted claim | The exactly-once discipline is the DB's (conditional UPDATE + lost-race/collision-as-clean-outcome), proven on SQLite; the same code path runs on PostgreSQL in the gate's parity run |
| arq/Redis adapter (B-157) | **RAN end to end against a real Redis**: no Redis is packaged for this environment, so redis-stable was compiled from source in the verify sandbox (gcc present; no Docker anywhere). A throwaway copy of the tree (`uv sync --extra queue`) submitted one job through the REAL `ArqJobQueue` → real Redis (127.0.0.1:6399) → real arq worker on `jobqueue.worker_settings` → `run_gpa_job` → the shared handler registry; the handler's written effect landed exactly once and Redis recorded `arq:result:<id>` | The suite itself proves the adapter's translation at an injected pool double (CI needs no Redis); the live run is a one-time slice proof recorded here, reproducible in any deployment with: `uv sync --extra queue`, start any Redis, `GPA_JOB_QUEUE=arq GPA_REDIS_URL=redis://… gpa-arq-worker` |
| Discord source (B-158) | Recorded-fixture driver behind the B-126 boundary: sync → chunks + speaker refs + cursor; speakers resolve only through account links; health check through the framework | **Live Discord leg NOT RUN** — no Discord account/bot may be connected in this environment. The exact later step: create a `discord` integration record with the bot token in its config (the B-032 permission record), run the connector check, then a live sync |
| Slack source (B-158) | Same, through the slack boundary (ts-as-id mapping at the boundary) | **Live Slack leg NOT RUN** — same reason. Later step: a `slack` integration record with the workspace bot token, connector check, live sync |
| Google inbound (B-159) | Fixture double through the REAL pull door and the REAL review queue: edit drafted → event unmoved → approval moved it; cancellation drafted as cancellation; our own unsynced edit drafted nothing (three-way compare); second pull deduplicated | **Live Google leg NOT RUN** — no Google account is connected here (the standing Phase 12 boundary). Later step: with a real client installed at the boundary, `POST …/google-pull` after any Google-side edit |
| Python SDK (B-160) | Generated from the frozen contract; the suite regenerates it and requires byte-identical output; stdlib-only proven by AST import audit; the acceptance example ran as a separate process against a REAL booted uvicorn server (scratch DB, seeded over HTTP) and printed the seeded public event | Registry publication N/A for the SDK (it is a file, vendored by copying) |
| TS types package (B-160) | `npm pack` run by the suite on a staged copy: tarball contains `package/index.d.ts` + `package/package.json`; index.d.ts byte-identical to `frontend_types.ts` | **npm registry publish NOT RUN** — publishing is the project owner's step: `npm login` once, then `npm publish sdk/typescript` (package is publish-shaped: name `gpa-types`, version 0.5.0, GPL-2.0-only) |

## Defects this phase found (all fixed at the root, all covered)

1. **arq's `create_worker` import** (B-157's live leg):
   `gpa.arq_worker` imported it from arq's top level; in
   arq 0.28 it lives in `arq.worker`. The console entry
   would have died on its first real run — the boundary
   tests could not see it; the live run did.
2. **arq function-name registration** (same leg): the
   worker settings registered the dispatcher under its
   Python name while submissions address it as `gpa_job`;
   the real worker reported "function not found" and the
   job sat unrun. Registered with `arq.func(..., name=…)`
   now; proven by the same live run completing.
3. **API keys are not Bearer tokens** (B-160's round-trip
   test): keys travel in the `X-API-Key` header by design
   (deps keeps the doors apart), but the developer pack's
   first SDK draft and DEVELOPER.md both said
   `Authorization: Bearer gpa_…` and were refused
   `token_invalid`. Generator template, generated SDK, and
   doc corrected; the test pins the header by using it.

## Phase 16's new tests (27)

- B-157: test_job_race.py (3 — four-thread drain with an
  audit-row exactly-once probe, barrier claim storm on one
  job, claim stepping over a held head row) +
  test_jobqueue.py (7 — delegation, unknown backend, arq
  refusals, translation at a fake pool, dispatch,
  failure-raises, unknown kind).
- B-158: test_second_wave_sources.py (7 — both platforms'
  fixture sync, unavailable refusals recorded, link-only
  speaker resolution, framework health).
- B-159: test_google_inbound.py (5 — the acceptance,
  three-way discipline, outside cancellation, missing
  copy, plain refusal).
- B-160: test_developer_pack.py (5 — byte-identical regen,
  stdlib-only + sugar, npm pack, key round trip, the
  booted-server acceptance).

## Gate totals (final tree, both engines)

Totals are read from the runs' own logs
(`~/workspace/.verify-tmp/p16-full-sqlite.log`,
`p16-full-pg2.log`, `p16-parity.log`) — never from a
pipeline's exit code.

- **Collected: 908 = 881 (Phase 15 gate) + 27 new**
  (job_race 3, jobqueue 7, second-wave sources 7,
  google-inbound 5, developer-pack 5).
- **SQLite: 904 passed, 4 skipped, 0 failed** in a single
  full run (36 m 40 s). The 4 skips are the PostgreSQL
  parity tests standing down without an admin URL, by
  design — the same 4 as every gate since B-152.
- **PostgreSQL: 892 passed on the engine, 16 skipped,
  0 failed.** Two runs, stated separately because they
  were separate: the full suite in PG mode
  (`GPA_TEST_PG_URL`, 7 m 49 s) gave **888 passed, 20
  skipped** — the 20 being the 16 named SQLite-file tests
  of `PG_FILE_TESTS` (counted exactly: that file set skips
  16 in PG mode) plus the 4 parity tests, which key on
  their own `GPA_TEST_PG_ADMIN_URL`. The parity file run
  with that URL against the same tree and the same server
  passed **4/4** (migration chain up/down/up, the mover,
  core flow + erasure, sealed-secret absence). 892 + 16 =
  908, and 904 + 4 = 908: both engines reconcile.
- **The discarded first PG attempt** (log
  `p16-full-pg.log`): 888 setup errors, 0 passed — the
  build environment reset mid-gate and took `/tmp/pgrun`
  (the cluster) and the `pgdev` user with it, the same
  failure Phase 15's gate met. `pg-bootstrap.sh` rebuilt
  the cluster from the workspace-preserved binaries and
  data, and the PG gate was re-run from the top; no number
  from the dead run is used anywhere above.
- Contract: **188 paths / 218 schemas** (B-159's one
  additive route). Progress: **160/350 slices, Phases 0–16
  complete.**

# Addendum — Phase 17, B-175 chaos pass (2026-10-25; phase gate is B-180)

The crash-correction harness's invariant report, as the
roadmap requires it to ride in the release's test report.
Generated by `scripts/chaos_report.py` (legs + loop re-run
there; the same checks are `tests/test_chaos.py` in the
suite) and copied from `CHAOS_REPORT.md` at the repo root —
numbers below are that run's own output, 2026-10-25.

**Invariant** (judged after every fault, on ground healed
by startup recovery, B-172): no committed user entry is
lost, no half-written entity is visible, and every
interrupted job is resumed or visibly failed — never
silently gone.

**SIGKILL subprocess legs: 6/6 hold.** ingest_after_commit,
ingest_mid_transaction, extraction_mid_job,
approval_mid_transaction, upload_mid_session,
digest_mid_job — each a real subprocess that reached its
kill point (marker file) and died by SIGKILL; the database
was then reopened cold, recovered, and judged below the
API (integrity + foreign-key PRAGMAs, tear probes).

**In-process pass: 100/100 hold** (seed 175; one fresh
deployment per run): commit 18/18, rollback 17/17,
job-recovery 17/17, disk-full 19/19, read-only 29/29.

Power-loss profile, honestly: a SIGKILL inside an open
transaction is the same event as a power cut for committed
rows under WAL + synchronous=FULL, so the mid-transaction
legs are the fsync-window legs; no VM/container rig is
used (the project's no-Docker law). Disk-full is injected
at the storage boundary (ENOSPC inside the transaction);
read-only is a creator-based `mode=ro` engine open.


# Phase 17 gate — B-180, the addendum proof (2026-10-26)

The Phase 17 gate drill (`tests/test_phase17_gate.py`) and
its full log live in `PHASE17_DRILL.md`, which ships inside
the gate ZIP (`gpa-backend-v0.6-2026-10-26.zip`). One
unbroken story: a flyer arrives over a resumable upload, a
worker dies mid-vision-job, startup recovery requeues the
job, the candidate is approved over the internal encrypted
API, raw-disk greps find no plaintext user data, and the
audit chain verifies. Drill + durability + architecture +
field-encryption batch: **14 passed**. Vision / flyer /
tombstone / erasure / Phase 5 gate batch: **45 passed**.

The drill found two root defects in earlier phases' code,
both fixed in the gate commit (`b0d7ec2`): chunked-upload
completion refused image bytes (every assembled file went
to the text/PDF ingest), and `Flyer.transcript` rested in
plaintext while its chunk twin was sealed — now sealed as
its own field with one opening funnel.

Full-suite numbers below are read from the runs' own log
files against the final gate tree (`50b15bd`):

- **Collected: 1,033 on both engines.**
- **SQLite: 1,026 passed, 7 skipped, 0 failed.** Run as
  two declared back-to-back halves (668 + 358 passed,
  ~26 min each) after two single full runs were killed by
  environment resets at ~62% and ~78% with zero failures
  to the cut; the halves method is the counting law's own.
  The 7 skips are the PostgreSQL parity tests standing
  down without an admin URL, by design.
- **PostgreSQL: 1,006 passed, 27 skipped, 0 failed** in
  PG mode (10 m 38 s), plus the parity file run with the
  admin URL against the same tree and server: **7/7
  passed** — **1,013 passed on the engine**. The 27 skips
  are the named SQLite-bound file tests of `PG_FILE_TESTS`
  standing down in PG mode. 1026 + 7 = 1006 + 27 = 1033:
  both engines reconcile.
- **PG environment, honestly:** the workspace-preserved
  cluster copy tore (its `global/pg_filenode.map` was
  missing — it had been copied from a running postmaster),
  so this gate's cluster was rebuilt fresh with `initdb`
  from the preserved pgserver binaries — same PostgreSQL
  16.2, same port, same trust-local setup the Phase 15
  gate established.

Three further gate repairs landed before those numbers
were taken, each found by the gate itself:

1. **Test repair (`0ed3dcd`).** The first PG-mode run of
   the Phase 17 tests failed four tests written against
   SQLite assumptions (raw-file greps for sealed fields, a
   SQLite-only backup refusal, an orphan planted through
   a no-foreign-key connection PostgreSQL refuses
   server-side). They moved to `PG_FILE_TESTS` with three
   parity twins added to `test_postgres_parity.py`, which
   now proves seven ways; the parity mover's raw user
   insert was also aged by B-174's NOT NULL columns and
   now speaks the current schema.
2. **Production fix (`66f88fb`).** B-170's audit-chain
   extension raced on PostgreSQL: two concurrent writers
   could both see no chain head and both insert id=1
   (`UniqueViolation` on `audit_chain_head_pkey`, failing
   roughly half of concurrent-writer runs), or hash
   against the same previous link and silently fork the
   chain. On SQLite the database write lock had hidden
   it. The head is now read FOR UPDATE and created inside
   a nested transaction so a lost creation race re-chains
   onto the winner's head. Concurrent writers: 5/5 on PG
   (was ~1/2); audit batches green on both engines.
3. **Test isolation fix (`50b15bd`).** The PG per-test
   reset truncates the model tables but never touched
   `alembic_version` — not a model table — so B-172's
   unknown-revision test, which plants
   `9999_from_the_future`, poisoned every later test on a
   reused suite database: healthy databases scored
   "refuse" and the planting test itself died on
   DuplicateTable. Fresh-database runs were green, which
   is why two earlier PG gate runs missed it. The reset
   now drops the table too, mirroring the fresh SQLite
   file it promises; the recovery file passes 5/5 twice
   back-to-back on the same reused, previously poisoned
   database.

- Contract: **201 paths / 229 schemas** (B-174's routes;
  unchanged at this gate — the drill and its fixes add no
  routes).
- Named waivers (also in `PHASE17_DRILL.md`): **second OS
  NOT RUN** — Linux-only builder; the drill is
  platform-pure and B-177's CI matrix defines the
  identical suite on Windows and macOS. **Coverage
  floors unmeasured** at this gate and left unstated in
  QUALITY_PIPELINE.md rather than invented.
- Progress: **180/350 slices, Phases 0–17 complete.**
