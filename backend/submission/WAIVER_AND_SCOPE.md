# Scope and waiver — what is submitted, and what is not

*This page exists so a cold reader can say, without asking
anyone, what was built inside the hackathon window and what
was not (roadmap B-150's acceptance). It is written to be
quoted.*

## What is submitted

The jointly built work of this team entry, and nothing else:

- **This backend** — the Group Planning Assistant backend,
  slices B-001 → B-151 of the team's roadmap, built inside the
  window (Mon Oct 26 → Wed Nov 4, 2026) by Volmarr + Yrsa, the
  backend half of the team.
- **The team's frontend**, built by Geoffrey + Henry against
  this backend's frozen contract — their half, submitted by
  them.
- **The Samtal integration surface built together in Phase
  11** (B-123 → B-130): the connector, the calendar return
  path, and their shared seams, as they exist *inside this
  repository*. This is the one place where the submission
  touches a pre-existing project, and the next section draws
  that line precisely.

## What is NOT submitted — pre-existing projects

The following are pre-existing projects. They are **not part
of this submission**, are not offered for judging, and nothing
in this repository should be read as claiming them:

- **Samtal** — a pre-existing chat platform. This backend
  contains a *connector to* Samtal, built in the window as
  Phase 11; Samtal itself, its codebase, and its history are
  outside the submission entirely. The connector was built and
  tested against recorded fixtures and a test double only —
  this codebase has never contacted the live Samtal, and no
  Samtal address or credential exists in this repository.
- **Mythic Engineering** — a pre-existing project of the team
  members' wider work. Nothing of it is in this repository.
- **Project Aesir** — a pre-existing project, likewise outside
  the submission; nothing of it is in this repository.

## Built in the window — and the parts that were not run live

Built and tested in the window: the whole backend — identity,
groups, permissions, agent seats; ingest of chats, files,
newsletters, images, voice, links, email-in; the AI adapter
with extraction, vision, and the review queue; events,
calendar, recurrence, iCal; planning (tasks, decisions,
polls, checklists, budgets, availability, templates); the
notification outbox, reminders, and digests; the nonprofit
pack (sub-groups, announcements, shifts, resources,
attendance-as-counts, member CSV); group memory and context
pruning; the Phase 11–12 integration boundaries; the Phase 13
security, privacy, and erasure work; the demo story and its
four flows; this pack; and the freeze drill that produced
this ZIP.

Not run live, by environment or by the owner's word — recorded
here so nothing below is mistaken for a claim:

- **The live AI driver has never run against a real Meta
  endpoint.** No credential exists in this environment; the
  driver is stub-transport tested, and demos run on the
  fixture/mock drivers. See `MODEL_MAPPING.md`.
- **B-131's Facebook live smoke test is NOT RUN.** No Facebook
  Page or group account is connected for this project; the
  integration is exercised against recorded fixtures only.
- **B-133's real-inbox check is a pre-submission checklist
  item** (in `DEPLOYMENT.md`), not a run result: the SMTP
  driver is wired and configured by settings, but no mail
  provider is connected in this environment and no real digest
  has been delivered to a real inbox by this build.
- **Messenger access has not been granted to this project**
  (B-125). The slice ships its documented fallback: sync
  refuses in plain words and the paste/export road is the
  supported path; notifications complete via other channels
  with a clean `driver_unavailable` record.
- **The Bruno collections are the B-010 skeleton** — a
  `bruno.json`, a health request, and a local environment.
  They were never grown into a full collection; the contract's
  living forms are `OPENAPI.json` and `frontend_types.ts`.
- **Not yet built, and not claimed:** group content is not
  field-sealed (that is B-167, Phase 17) and the audit log is
  append-only by discipline, not yet a hash chain (B-170).
  `THREAT_MODEL.md` names both as accepted gaps. Slices after
  B-151 — deployment, scale, the later integration waves —
  are roadmap, not submission.

## The data in the box

Demo data is fictional and labelled fictional. This ZIP
contains no real user's data, no real API key, no `.env`, and
no database with real people in it (release-train rule §8.2;
the freeze drill verifies it by listing and grep).

## Licence

Licence: GNU General Public License version 2 (GPLv2).

The licence covers the jointly built work in this repository.
It asserts nothing over the pre-existing projects named above,
which keep whatever terms their owners keep for them.
