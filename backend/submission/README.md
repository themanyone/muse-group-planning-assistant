# Submission pack — Group Planning Assistant, backend half

*The backend's half of a team entry to the Meta Global AI
Developer Hackathon (build window Mon Oct 26 → Wed Nov 4, 2026).
Volmarr + Yrsa built this backend; Geoffrey + Henry build the
frontend against its frozen contract (`OPENAPI.json`, beside this
pack in the release ZIP). This pack is the cold reader's door:
what the backend is, what model it talks to, how it is put
together, how to run it, and exactly what is — and is not —
being submitted.*

## The product in one breath

Every plan a group makes is already hiding in the noise — a chat
thread, a newsletter, a photographed flyer. This backend fishes
the plan out, writes it down as a **draft with its receipts**
(every value carries the exact quote it was read from), and then
waits. **The machine drafts; a human decides.** Nothing becomes
a real event, task, or decision until a person in the group
approves it in a review queue.

## What is in this pack

| Document | What it answers |
|---|---|
| `MODEL_MAPPING.md` | Which Meta model does what — the honest mapping, tier by tier, driver by driver, including what has never run live |
| `ARCHITECTURE.md` | How the backend is put together — one diagram and a walking tour of every block in it |
| `RUN.md` | How to run it cold from the release ZIP, and the four demo flows as a script |
| `WAIVER_AND_SCOPE.md` | What was built in the window, what was not, and what is not being submitted — read this before judging scope |

Beside the pack, in the same ZIP: `RESULTS.md` (the evaluation
numbers, generated from the shipped corpora, never hand-typed),
`TEST_REPORT.md` (the full suite result for this ZIP, phase by
phase, finds and boundaries included), `FRONTEND_HANDOFF.md`,
`CHANGELOG.md`, and `gpa-backend/FREEZE_DRILL.md` (the freeze
drill log: this ZIP unzipped on a clean profile, demo run cold,
timings recorded).

## What the backend does, in plain words

- **Reads anything the group already has** — pasted chats
  (WhatsApp / Discord / Slack / Messenger shapes), uploaded
  files, newsletters, flyer photos and corkboard photos, voice
  notes, links, email-in, and live sources through guarded
  connectors.
- **Extracts drafts, never facts** — events, tasks, decisions,
  and open questions land in a review queue with per-field
  quotes, confidences, and quality flags; dates are resolved
  by a deterministic resolver, not guessed.
- **Publishes only on a person's say-so** — approval mints the
  real thing in the same transaction: calendar events with
  recurrence and iCal feeds, discussion, RSVPs with a strict
  waitlist, reminders and digests through a transactional
  outbox, invites for guests.
- **Keeps one truth** — later sources merge field by field by
  source support; contradictions are raised, not hidden;
  deletes are tombstones inside a restore window.
- **Remembers** — group memory is a pruning engine over
  approved history (it judges relevance chunk by chunk; it is
  deliberately *not* embedding RAG), with citations verified
  quote by quote before an answer is allowed to speak.
- **Reaches outward through doors that fail honestly** — the
  Samtal bridge, Facebook Events, Google Calendar, and a real
  SMTP mail driver, each behind a client boundary with a
  recorded-fixture double and a plain-words unavailable
  default. See `WAIVER_AND_SCOPE.md` for what has never run
  live, and why.
- **Survives attack and erasure** — a written threat model,
  an authorisation fuzz suite, upload hardening, a poisoned-
  corpus suite, group export, and hard erasure that leaves
  the raw database file greppably clean.

## The state of the build

Phases 0–14 of the roadmap are built: **151 of 350 slices**
(B-001 → B-151). The contract frozen in this ZIP is 187
OpenAPI paths / 217 schemas. The full test suite result for
this exact tree is in `TEST_REPORT.md`; the evaluation numbers
are in `RESULTS.md`.

## Licence

Licence: GNU General Public License version 2 (GPLv2).

The licence is decided and stated the same way in the
repository README and in `pyproject.toml`. The scope note —
whose work this is, and what pre-existing work is *not* part
of the submission — is `WAIVER_AND_SCOPE.md`.
