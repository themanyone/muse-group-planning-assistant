# 12 — Glossary, Decisions, and FAQ

**For:** everyone. If any document in this set uses a word you do
not know, it is defined here in plain words. If any choice in the
system seems odd, the decision behind it is explained here — all 46
of them, D-01 → D-46, in the order the team made them. And the
questions people actually ask are answered at the end, honestly,
including the ones whose answer is "that is deliberately not
decided yet".

---

## Part 1 — Glossary

**Agent seat** — an AI participant in a group, with a name, a role,
and a key, labelled as an agent everywhere it appears. A seat is a
citizen of the app, not a hidden bot: everything it does is
attributed to it in the audit.

**API key** — a secret token a seat or integration presents instead
of a password (`X-Api-Key`). Shown once at issue, stored only as a
hash, revocable in one action. See also **scope**.

**Approval law** — the rule that nothing the AI drafts becomes real
until a person (or a seat holding an approver's role, where the
group allows) approves it in the review queue. There is no setting
that switches this off, and no agent-only bypass anywhere.

**Audit chain** — the tamper-evident history: every consequential
act is a row that carries the hash of the row before it, so edited
history is detectable and the first tampered row can be named
(Doc 08 §6).

**Candidate** — a draft in the review queue: an event, task,
decision, or question the AI (or a member) has *proposed*, with its
confidence marks and its evidence attached. A candidate is not a
fact; approval is what makes it one.

**Change candidate** — a proposed *edit* to something already real
(an event moved, a place changed), arising for example from a newer
source or an edit made in Google Calendar. It waits in the queue
like any candidate; the original stays untouched until a human says
yes.

**Claim audit** — the release-gate check (B-295) where an auditor
who wrote none of the claims samples them across the handoff, the
docs, and the results page, and traces each to its evidence. An
untraceable claim blocks the release.

**Commit-before-answer** — the durability rule that a handler must
commit its database transaction *before* sending its response, so
anything a client is told is already true on disk (Doc 08 §10.2).

**Context pruning** — Geoffrey's engine: instead of feeding a model
a group's whole history, it selects the chunks relevant to the
question at hand — deliberately *not* off-the-shelf retrieval (RAG);
a chosen, auditable selection whose every run leaves a `PruneRun`
record of what was kept, what was pruned, and why.

**Cursor pagination** — how lists are walked: instead of "page 3",
the API hands back an opaque `next_cursor` meaning "continue after
here", so items inserted while you scroll never duplicate or
vanish mid-list (Doc 09 §4).

**Data key** — the per-group encryption key that seals that group's
fields and wraps its file keys. Held only wrapped under the
**master key** (Doc 08 §2).

**Digest** — the group's weekly summary: what is on, what needs a
reply, what is still undecided — by email, in-app, or both.

**Doctor** — the built-in diagnostician (D-45): a command, a TUI
screen, an admin API operation, and an MCP tool that all run the
same health checks (install, database, keys present — never values,
disk, clock, backups, job queue, model adapter), explain each
finding as *what we found · what it means · what to do*, and offer
safe repairs only with per-fix consent, after a backup, audit-logged.

**Envelope encryption** — sealing data under a key that is itself
sealed under another key, like a letter in an envelope in a safe:
a field is sealed under its group key; the group key is wrapped
under the master key. Stored values wear the versioned prefix
`enc:v1:` (Doc 08 §3).

**Evidence pack** — the file that closes every slice
(`evidence/packs/B-###.md`): the hypothesis and threshold written
before the build, the real acceptance output, the measurements,
and links to the evidence registry (Doc 11 §3).

**Evidence registry** — the index (`EV-###`) connecting every claim
the project makes — in docs, handoffs, results — to the test run,
benchmark, drill log, or user-evidence record that backs it
(B-281). A claim with no entry is not shipped.

**Fixture driver** — one of the three AI drivers (D-18): it replays
recorded real model responses, so demos and evaluations are
reproducible offline. Its siblings are the **mock** driver
(deterministic canned answers) and the **live** driver (the real
Meta Model API).

**Golden corpus** — a versioned set of test inputs (chats,
newsletters, flyers, a board photo, a poisoned source) with human
gold labels saying what the right answer is — twice-labelled, with
the agreement score published. AI quality is measured against these,
never asserted (Doc 11 §1.1).

**Guest** — the smallest role: sees only what they were invited to,
usually one event, and can RSVP to it.

**Help id** — a stable name attached to every screen, field,
command, and error, which resolves to that thing's help page on
every door (D-44). An error you can look up is an error you can fix.

**Host app** — an application that embeds the planning capability
instead of merely calling it (D-42): Samtal is the named first
host. The core also runs standalone and as an in-process library.

**Idempotency-Key** — a random key a client attaches to a create
request, one per user intention. Retrying with the same key returns
the original result instead of creating a duplicate — the reason
double-taps and offline-queue flushes can never double-book
(Doc 09 §6).

**Internal encrypted API** — how the system's own parts talk to each
other: typed operations, envelope-encrypted, signed, sent under
per-module identities with least-privilege scopes, replay-protected,
audit-logged without payloads (Doc 05). There is no second, quieter
channel.

**Magic link** — the password-free sign-in: you give an email, you
receive a one-time, short-lived link, you tap it, you are in
(Doc 09 §2.1).

**Master key** — the deployment's root encryption key. It lives in
the environment or OS keyring — never in the database, the repo, a
backup, or a log — and wraps every group key (Doc 08 §2).

**MCP** — the Model Context Protocol: the standard way AI agents
connect to tools, resources, and prompts. This product's MCP server
(Doc 13) exposes every domain, not a token handful of tools.

**Parity** — the law (D-35) that every capability exists on every
door: if a human can do it in a UI, an authorised caller can do it
in the API, in MCP, and in the terminal — machine-checked per
release, so a missing door fails the build by name.

**Principal** — anyone or anything that can act in the system and
be held responsible for it: a human member *or* an agent seat. The
word exists so the rules can be written once, for both kinds of
citizen, equally.

**Problem+json** — the one error shape (RFC 9457): `{type, title,
status, code, detail, errors[]}`. The stable `code` is what
software branches on; the plain-words `title`/`detail` are what
humans read (Doc 09 §5).

**Provenance** — two honest records with one name. *On a
candidate:* which exact quote or flyer region each field came from.
*On the code:* which team member authored each file, in which
slice, affirmed as their own original work (Doc 11, Phase 27).

**Queue** — always the **review queue**: the group's front door,
where candidates wait for a human yes, a fix, a no, or a merge.

**Recovery codes** — (1) *Account:* one-time codes that restore a
member's sign-in if they lose their second factor. (2) *Owner:*
codes escrowing the master key under scrypt, so a lost master key
is a bad day, not the end of the group's data (Doc 08 §5). Context
always says which kind is meant.

**Release train** — the fixed rhythm of ZIP releases (ZIP-0 →
Alpha → Beta → RC → Final) by which the frontend crew receives the
backend: scripted builds, frozen contracts, honest handoff notes
(Doc 11 §7).

**Review queue** — see **queue**. Its settings (who may approve,
one approver or two) are Doc 02 A.3.

**Role** — a member's standing in a group: owner > admin >
organiser > member > guest. A seat holds a role exactly as a human
does. Role decides *what you may do*; scope decides *what your key
may do*; the tighter wins.

**Scope** — what an API key is allowed to attempt: `read`, `draft`,
`publish`, `admin` — least first. Checked on every call, alongside
the holder's role.

**Sealed** — encrypted at rest with the envelope scheme. A sealed
field in a raw database reads as `enc:v1:…` noise (Doc 08 §3).

**Service identity** — a module's own cryptographic identity for the
internal API: its certificate, key id, and granted scopes. Modules
cannot impersonate each other (Doc 05).

**Slice** — one numbered unit of the roadmap (B-001 → B-340), sized
to be picked up cold, built, tested, and closed with an evidence
pack. Bug reports and handoffs refer to slice ids.

**Source** — anything handed to the app to read: a pasted chat, a
newsletter, a flyer or board photo, an email, a link, a voice note,
a connected live chat. Adding one is always a recorded act of a
named person (Doc 01 §4).

**SSE** — Server-Sent Events: the one-way live stream (`candidate.
created`, `event.changed`, …) that makes screens feel alive. An
enhancement, never a requirement (Doc 09 §8).

**Watermark** — a position in a group's change history. Live
surfaces carry one; on reconnect the client presents it and the
server replays exactly the gap — a dropped stream heals itself
instead of silently losing events (Doc 09 §8).

**ZIP** — the release artifact itself: the whole backend, runnable,
with its frozen contract, test report, and signed manifest
(Doc 11 §8).

---

## Part 2 — The 46 decisions, in plain words

These are the defaults the builders work to, so nobody has to
re-decide settled questions mid-build. **Any team member can
overturn any of them in the group chat** — until then they are the
law of the build. Each entry says what was decided and what it
means for you. (The binding table is §2 of the roadmap; each
decision also has an architecture decision record with its evidence
and its reversal trigger, B-294.)

### The foundation (D-01 → D-10)

- **D-01 · Python 3.12+.** The whole backend is Python — the team's
  shared language, and the language of everything around it.
- **D-02 · FastAPI.** The web framework writes the API contract
  (OpenAPI) from the code itself, so the contract can never drift
  from the implementation.
- **D-03 · Uvicorn.** A standard, boring, proven server to run it.
  Boring is a compliment for infrastructure.
- **D-04 · Pydantic v2 schemas, one module per domain.** The same
  schemas validate what the API receives, what the AI produces, and
  what the tests use as fixtures — one shape, checked everywhere.
- **D-05 · SQLite (WAL mode) for the hackathon.** The database is
  one file, zero setup — the demo runs on any machine, including a
  Raspberry Pi, in minutes.
- **D-06 · PostgreSQL later, same models.** Growing up is a
  deployment change, not a rewrite: the same data models run on
  PostgreSQL when a deployment outgrows one box.
- **D-07 · SQLAlchemy 2.0 + Alembic migrations.** Database changes
  are versioned, reviewable migrations shipped in every ZIP — never
  a hand-edited production database.
- **D-08 · uv + a committed lockfile.** One command installs the
  exact, tested set of dependencies. What was tested is what runs.
- **D-09 · Jobs live in a database table.** Background work
  (reminders, extraction, digests) is rows in the database worked
  by an in-process worker — no Redis, no Celery, fewer moving
  parts, and jobs survive a restart because they were never only
  in memory.
- **D-10 · The scheduler is the job system.** Reminders and digests
  are just jobs with a `run_at` time and a 30-second tick.

### Identity, contract, and safety of the wire (D-11 → D-24)

- **D-11 · Magic links + optional password; JWT + rotating
  refresh; API keys for agents.** Sign-in that a non-technical
  nonprofit member can actually do, sessions that expire quickly
  and rotate safely (Doc 09 §2), and keys for the callers who are
  not people.
- **D-12 · REST `/api/v1`, JSON, OpenAPI 3.1.** The frontend
  generates its client from the contract; nobody hand-copies field
  names from chat messages.
- **D-13 · problem+json errors with a stable `code`.** Software
  branches on codes that are never renamed; humans read the
  plain-words title and detail (Doc 09 §5).
- **D-14 · Cursor pagination.** Lists stay stable while new items
  arrive — no duplicates, no vanishing rows mid-scroll.
- **D-15 · Idempotency-Key on creates; ETag/If-Match on updates.**
  Double-taps and retries can never double-create; two editors can
  never silently overwrite each other (Doc 09 §6–§7).
- **D-16 · Server-Sent Events for live updates.** One-way live
  updates are all this product needs; SSE is far simpler than
  WebSockets and heals itself with watermarks (Doc 09 §8).
- **D-17 · A storage interface, local disk first.** Flyers and
  uploads never leak a filesystem path into the API, and an
  S3-compatible driver can replace local disk later without the
  API noticing.
- **D-18 · One AI adapter, three drivers.** Live Meta models,
  recorded fixtures, and a mock — behind one interface, so the
  demo and the tests run offline and the domain code has no vendor
  lock-in (Doc 09 §9).
- **D-19 · Muse Image reads; SAM 3.1 separates board posters.**
  Each model does the job it is actually good at; SAM is not
  load-bearing anywhere else, and that honesty is written into the
  submission.
- **D-20 · Standard iCal (RFC 5545).** One feed per group plus
  per-event downloads — every calendar app on earth eats this
  (and Doc 15 covers the deeper per-app conformance).
- **D-21 · UTC in storage, named timezones on everything.** A
  datetime without a timezone is treated as a bug, because time
  errors are this product's most damaging error class.
- **D-22 · Relative dates resolve in a named, tested module.**
  "This Saturday" must resolve the same way in the demo, in tests,
  and at 11 p.m. on a real Saturday — never as a side effect of a
  prompt.
- **D-23 · pytest everywhere; a slice is done when its tests pass.**
  Unit, integration, contract, and golden-AI suites — inspect,
  build, test, repair, continue.
- **D-24 · No Docker. Ever.** Native installs, `uv` environments,
  plain run scripts, the platform's own service manager. A strict
  standing rule of the team, with no exceptions and no "just for
  the demo".

### The licence, and the craft laws (D-25 → D-34)

- **D-25 · The licence is deliberately undecided — and it is the
  business side's decision.** The code ships in the ZIP with **no
  licence granted and none assumed**: until the team decides, the
  notice reads "to be decided by the team — all rights reserved",
  and nothing anywhere calls the project open source, proprietary,
  or anything else. *Whose decision is it?* Geoffrey and Henry are
  the team members familiar with business matters — the licence is
  **their side of the table to settle**, in the team's own time.
  The builders' (Volmarr's and Yrsa's) job is narrower and purely
  preparatory: keep the code's provenance clean (D-46) and every
  option open, so that whatever Geoffrey and Henry decide can
  simply be *done* — no untangling, no one's claims to clear, no
  assumptions made in either direction beforehand.
- **D-26 · Heavily commented code.** A header on every file saying
  what it is for; a docstring on every module, class, and function
  saying what it does and why. The backend reads like a book,
  because the people inheriting it deserve that.
- **D-27 · Windows, macOS, and Linux are all first-class.**
  `pathlib` only, no OS assumptions, the full suite runs on all
  three — the team and the judges do not all run the same machine.
- **D-28 · A crash must not lose entered data.** WAL +
  `synchronous=FULL`, one transaction per mutation,
  commit-before-answer, autosaved drafts, resumable uploads,
  verified backups (Doc 08 Part 10). Entered data is sacred.
- **D-29 · User data is encrypted at rest.** Envelope encryption
  with keys outside the database (Doc 08), so a stolen disk or
  copied database reads as noise — proven by grep tests against
  the raw files, not asserted.
- **D-30 · Real modules, with published surfaces.** Each domain is
  a module with one public interface; no module reaches into
  another's tables or private code, and architecture tests enforce
  the boundaries — any part can be replaced without the rest
  noticing.
- **D-31 · The parts talk over an internal encrypted API.**
  Encrypted, signed, authenticated, replay-protected — zero trust
  *inside* the box too (Doc 05).
- **D-32 · The system checks and repairs itself.** Boot recovery,
  an hourly watchdog, stuck-job resurrection, orphan sweeps —
  with the double-verdict guard so a healthy database is never
  "healed" aside (Doc 08 §11).
- **D-33 · Production gates, or the ZIP does not ship.** Typed,
  linted, formatted, comment-gated, all suites green, security
  suite green, SBOM + hashes + signed manifest in every release.
  "Production level" is a checklist, not a compliment (Doc 11 §9).
- **D-34 · Documentation ships in every ZIP and is gated like
  code.** This set: user guide, admin/operator guide, the diagram
  atlas, the per-file code atlas — a release fails if a code file
  lacks its atlas entry or a diagram does not render.

### One product, many doors (D-35 → D-40)

- **D-35 · Humans and AI agents are equal principals.** A seat has
  the same capabilities, the same data, and the same approval law
  as a human of the same role — and API parity is total and
  machine-checked. No human-only back door; no agent-only shadow
  system.
- **D-36 · MCP is a core surface, not an extra.** Tools, resources,
  subscriptions, and prompts for every domain, over stdio and
  streamable HTTP, authenticated and scope-checked per call
  (Doc 13).
- **D-37 · A full terminal app ships with the backend.** TUI +
  scriptable CLI, for humans and AIs equally — an *option* beside
  the team's frontends, never a replacement for them and never a
  lesser surface (Doc 14).
- **D-38 · Multi-user and remote are the design case.** Concurrent
  humans and seats with optimistic-locking convergence, watermark
  resync, offline-tolerant clients, and named deployment profiles
  whose unsafe configurations the server refuses at startup, in
  plain words.
- **D-39 · Universal platform support is a requirement.** Linux
  (x86_64 + ARM64), Windows, macOS (Intel + Apple Silicon), and
  Raspberry Pi as a named first-class server target; iPhone and
  Android reached by web/PWA, push, and camera uploads; no
  dependency ships unless it installs cleanly on every Tier-1
  target (Doc 11 §5).
- **D-40 · It speaks the group's existing tools' languages.**
  Google, Apple/iCloud, Outlook, Yahoo, Thunderbird, Samsung
  calendars (iCal, CalDAV, per-app links, two-way sync that brings
  outside edits back as review-queue candidates) and Microsoft
  365/Office, Google Workspace, LibreOffice, iWork, WPS, Zoho,
  OnlyOffice documents (DOCX/XLSX/PPTX, ODF, PDF, CSV, Markdown) —
  with honest limits written where users meet them (Doc 15).

### Proof, embedding, friendliness, provenance (D-41 → D-46)

- **D-41 · Evidence-based development.** Nothing is built, kept,
  or claimed on opinion: hypothesis and threshold before the
  build, an evidence pack to close, every shipped claim traced to
  an evidence id and graded — **proven** (measured here),
  **supported** (external source, cited), **assumed** (labelled,
  with the test that would settle it). Only *proven* claims may be
  stated as fact (Doc 11 §3).
- **D-42 · Built to be built into other apps.** The planning
  capability runs standalone, as an in-process library, and hosted
  per host app — with host identity bridging (no second password),
  embed widgets, SDKs, and a plugin protocol. Samtal is the named
  first host; any other app gets the same documented road (Doc 16).
- **D-43 · User-friendliness is a release gate.** Plain words,
  golden tasks in few steps with no dead ends, errors that say
  what happened + what to do + how to undo it, teaching empty
  states, forgiveness built in, comfort options — and a usability
  evidence threshold a release must meet or wait.
- **D-44 · Massive built-in help.** Every screen, field, command,
  and error carries a help id; one content source renders into
  web, terminal, CLI, API, and MCP resources; a goal-based
  "I want to…" finder; interactive walkthroughs; all of it
  offline-available and versioned with the app — a volunteer
  should never need to leave the app, or ask a developer, to
  learn it.
- **D-45 · A built-in Doctor.** The app diagnoses itself — install,
  versions, database integrity, keys present (values never
  printed), disk, clock, profile safety, ports, certificates,
  backup freshness, job queue, model adapter — through the CLI,
  a TUI screen, an admin API operation, and an MCP tool. Findings
  in plain words with fixes attached; repairs only safe, only
  per-fix-consented, always after a backup, always audit-logged;
  and one redacted support bundle a user can hand to a human
  (see **Doctor** in the glossary).
- **D-46 · All the code is the team's own original work — so the
  licence stays the team's free choice.** Nothing copied, pasted,
  ported, adapted, or vendored from any other project, book, post,
  or snippet. Third-party software enters only as unmodified,
  declared, *installed* dependencies under permissive licences —
  recorded in the SBOM, never as borrowed code inside the tree —
  and the standard library is preferred wherever it suffices. A
  per-file provenance register, an originality affirmation on
  every contribution, and per-release similarity scanning keep
  "it is all our own work" a claim with evidence behind it
  (B-331 → B-340). This is the builders' whole contribution to the
  licence question: provenance so clean that the decision —
  Geoffrey and Henry's to make, as the team's business side,
  whenever they choose — is a one-meeting decision. **No
  assumptions are made about the outcome in either direction:**
  no document, package metadata field, file header, handoff note,
  demo, or listing may state or imply a licence before the team
  has decided — not "open", not "closed", not an SPDX identifier —
  and the provenance linter fails any file that presumes one.

---

## Part 3 — FAQ

**Is this open source?**
That is deliberately not decided — and nobody should guess in
either direction. The code is entirely the team's own original
work (D-46), so the licence is Geoffrey and Henry's to settle as
the team's business side, whenever they choose (D-25). Until then
the honest notice is: *"Licence: to be decided by the team — all
rights reserved."* Anything you read anywhere claiming otherwise —
"open", "proprietary", an SPDX id — is wrong, and if it is in one
of our files it is a bug; tell us.

**Who wrote the code? Can we really choose any licence later?**
The team did — every line, written for this project, with a
per-file provenance register and an originality affirmation on
every contribution (B-332/B-333). Dependencies are installed
packages with their own recorded licences, never borrowed code in
the tree. That is exactly what makes a later licence choice (or
change) a decision instead of an archaeology project.

**Does the app need Docker?**
No. Never (D-24). It installs natively on Linux, Windows, macOS,
and Raspberry Pi OS, runs under the platform's own service
manager, and the demo runs from one script. There is no container
runtime anywhere in the design.

**Can an AI approve its own drafts?**
Only under the same rule as a human: a seat may approve if it holds
an organiser role *and* the group allows self-approval — which is
off by default, so normally a **second** principal must approve
(Doc 02 A.3/A.5). A draft-scoped seat can never approve at all.
There is no agent-only override in the codebase (B-203).

**Is our group chat read automatically?**
No. The app reads only what the group gives it: sources a named
person adds, or a live chat an admin explicitly connects — with
the grant recorded and visible to members, and disconnect stopping
reading immediately (Doc 01 §4).

**Is it end-to-end encrypted?**
No — and we say so plainly rather than implying it (Doc 08 §9).
Data is encrypted **at rest**: a stolen disk, copied database, or
leaked backup reads as noise. The server can read what it serves,
which is what makes extraction and group memory possible.

**What happens if the server crashes while I'm typing?**
Your form is autosaved server-side as a draft as you go; a crash
loses at most the last few seconds, and reopening — on any device —
offers the draft back. Committed work survives even a power cut:
WAL + `synchronous=FULL` + commit-before-answer (Doc 08 §10).

**Can it really run on a Raspberry Pi?**
Yes — the Pi is a named Tier-1 target with its own resource
profile, budgets measured on real Pi-class hardware, and its own
installer path (B-253, Doc 11 §5). What software cannot fix is a
worn SD card; the installer says so and offers the encrypted
remote backup.

**What if the Meta model API is down, or we have no key yet?**
Switch the driver: `fixture` replays recorded real responses,
`mock` answers deterministically. The entire demo runs offline on
either (Doc 09 §9). When live access lands, only configuration
changes.

**Can Geoffrey and Henry change a backend decision?**
Yes — that is §2's standing rule: any team member can overturn any
default in the group chat. The decision gets an updated ADR
(B-294) naming the new evidence and the reversal of the old, so
the *history* of why is never lost.

**The AI read our flyer wrong. Now what?**
That is the product working as designed: the wrong reading is a
draft in the queue with its uncertainty flagged and the flyer
region highlighted (Doc 01 §5). Fix it, approve the fix — and the
failure becomes a new golden-corpus fixture, so that exact mistake
is tested against forever after (Doc 11 §1.1).

**Will it work with our calendar / office suite?**
Almost certainly — D-40 and Doc 15 cover the popular calendars
(Google, Apple, Outlook, Yahoo, Thunderbird, Samsung…) and office
suites (Microsoft 365, Google Workspace, LibreOffice, iWork, WPS,
Zoho, OnlyOffice) app by app, with the honest limits written down
(iWork formats, for example, are export roads, not import ones).

**Can our group's plans move into Samtal — or out of it?**
Yes, by design (D-42, Doc 16): Samtal is the first host app, and a
whole group can be ported between a standalone deployment and an
embedded one, provenance intact. Leaving is as real as joining, in
both directions, between our own apps most of all.

**Do deployments talk to each other? Will ours federate?**
No (B-249). Each deployment is sovereign; members reach *their*
server remotely, but servers never federation-speak to each other.
It is a deliberate non-goal — do not promise it to a user or a
judge, and no one here will promise it to you.

**What does the Doctor actually do — is it safe to let it fix
things?**
It checks the installation's health and explains every finding in
plain words: *what we found · what it means for you · what to do*
(D-45). Repairs are a short list of provably safe fixes, each
asking your consent individually, each preceded by a backup and
written into the audit chain — and it will never delete data,
regenerate keys, or "heal" a database that has not been proven
corrupt twice (Doc 08 §11.1). Its one-file support bundle redacts
secrets automatically, and that redaction is itself a tested,
release-blocking property.

**I prefer a terminal / I am an AI with only a shell. Am I a
second-class user?**
No — parity is law here (D-35). The terminal app is a first-class
door with the whole product in it, the service runs happily in the
background with no terminal attached at all, and an agent can drive
everything through the CLI's `--json` mode, the API, or MCP — same
operations, same approval law, same audit (Doc 13, Doc 14).
