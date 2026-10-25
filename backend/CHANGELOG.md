# Changelog — Group Planning Assistant Backend

Per-slice list for the release train (roadmap §8). Slice titles
are the roadmap's own words; phases are grouped in build order.

## Breaking changes

- **None to the HTTP contract through B-104.** Every change has
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
