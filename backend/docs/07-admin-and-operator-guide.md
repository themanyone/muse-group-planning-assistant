# Admin + operator guide

*The Phase 18 admin chapter (slice B-188). For group
admins and whoever runs the server. The rule of this
chapter, per its roadmap line: **every dangerous button is
documented with its consequence before its instructions.**
Read the bold consequence first. If you do not accept it,
do not follow the steps under it.*

*Group administration that is not dangerous — creating the
group, inviting people, the role ladder, ordinary settings
— is chapter I. This chapter is the heavier machinery.*

## 1. The settings pack — all the dials, one door

[`GET /api/v1/groups/{id}/settings`] reads the pack;
[`PUT …/settings`] (admin) changes it. The pack is the
review policy (`review_min_role`,
`review_approvals_required`), the reminder rules, the
digest weekday/time/enabled switches, the template set,
the upload quota, source retention days, and the AI daily
budget (`ai_daily_budget_micros`, where **null means no
cap**).

It is validated as one schema and applied as **one
transaction: one bad dial changes nothing** — you never
land half a settings change. Every change is audited
(`group.settings_updated`) with the old and new value of
each field. Present fields govern, including an explicit
null; absent fields are left alone. Digest changes
re-anchor the weekly digest chain (chapter VI) in the
same act.

**Consequence to know before you turn two dials:**
lowering `review_approvals_required` or `review_min_role`
weakens the review queue's door for every future
candidate, not just the next one; and setting the AI
budget to null removes the cap entirely — work stops
being deferred for money, because there is no limit to
hit.

## 2. Agent seats — giving an AI a chair

**Consequence first: a seat acts in the group under its
own name, and everything it does is attributed to the
seat — never silently to you.** Its key is **shown once**
at creation and kept only as a hash afterwards; lose it
and you mint a new seat, you do not recover the old key.
A seat can draft, read, post, and plan at its role — and
a seat **can never approve** (`seat_cannot_approve`,
enforced in the service, not just hidden in a menu): the
one approval law has no agent exception.

Create [`POST /api/v1/groups/{id}/seats`] (admin): name,
role, and scopes (the API-key ladder of chapter I —
read / draft / publish / admin; publish never implies
approve). List [`GET …/seats`] shows seats and prefixes,
never keys. Grant a live-chat source to a person, never
to a seat (chapter II): a seat also cannot announce
(announcements are a person's act) or hold the owner role.

## 3. Integrations — the outward doors

An integration record
[`POST /api/v1/groups/{id}/integrations`] connects the
group to Samtal, Facebook Events, Google Calendar, or a
custom kind. **Its whole config is sealed at rest**
(encrypted and tamper-checked); reads show config **key
names only**, never values. The health board
[`GET …/integrations/health-board`] (organiser) reports
each integration's last recorded verdict plus one plain
advice sentence — it reads records and pings nothing.

**Consequences, kind by kind:**

- **Facebook / Google — the press is a person's.**
  Nothing posts itself. The first press/sync
  [`POST …/integrations/{id}/google-sync`, and the event's
  facebook door] creates the outside copy; later presses
  update it; a cancellation is carried out by the same
  doors. Google sync is **one-way out**: a Google-side
  edit is never silently read back — it arrives, if at
  all, as a review-queue change candidate
  [`POST …/integrations/{id}/google-pull`] that a person
  approves or rejects. Where no real account is
  connected, the driver is honestly **unavailable** and
  says so; nothing fakes a live post.
- **Reconciliation never writes outward.** The nightly
  `reconcile` comparison (run it by hand:
  [`POST …/integrations/reconcile`], admin) reports
  `differs` / `missing` copies to organisers as one
  drift notification. A deleted outside copy is restored
  only by an organiser's own press; an edited copy stands
  until a person settles it.
- **Samtal** syncs a hall's messages in as a source and
  published events back onto the hall's calendar
  (chapter II); Samtal-side calendar edits return as
  change candidates, never as direct writes.
- **Webhooks in** [`POST …/webhook-endpoints`]: the URL
  token is **shown once** and kept hashed; the signing
  secret is derived, stored nowhere, and checked over
  `"<timestamp>.<body>"` inside a 300-second window;
  a replayed delivery is refused (`webhook_replay`).
- **Webhooks out** [`POST …/outbound-webhooks`]:
  subscriptions to published / changed / cancelled /
  pending events; the secret is **shown once** and kept
  sealed; deliveries are inspectable
  [`GET …/outbound-webhooks/{id}/deliveries`] and die at
  the job attempt cap rather than retrying forever.

## 4. The AI budget — the money dial

Usage is a ledger, not a guess: every model call records
its model and cost into the group's daily total. The cap
is `ai_daily_budget_micros` in the settings pack
(§1). At the cap, extraction and memory work **defers to
the next UTC midnight** as a fresh job and the status
surface reports paused / budget with spend and cap in
plain words. Raising the cap is the only way to un-pause
early — there is no "run it anyway" button, on purpose.

## 5. Reading the audit log

[`GET /api/v1/groups/{id}/audit`] (admin) is the group's
append-only record: actor label, act, and the row it
touched, written **in the same transaction** as the act
itself — an act that did not commit left no audit row,
and an audit row means its act did. The log is a hash
chain; [`GET …/audit/verify`] walks it and says whether
it still verifies. A chain that fails verification is
evidence of tampering with the record itself — treat it
as an incident, not a bug report. Seat acts appear under
the seat's own label (§2); machine acts under their
`system:` labels.

## 6. Member data requests

A member's own data is theirs to read without asking an
admin: [`GET /api/v1/users/me/data`] returns their
profile, memberships, and what they have put in — **no
password hashes, no token material, by construction**
(the service never selects them). A member's sources and
the permissions they granted are listed at
[`GET /api/v1/groups/{id}/sources/permissions`]. Member
import and export (admin) are chapter-sized tools of
their own in the nonprofit pack: import arrives people
as **invited**, never silently overwrites a name a
person already has, and a dry run does the identical
work and rolls it back — a dry run happened to nobody
and leaves no audit row. Export
[`GET …/members/export`] is CSV or JSON of memberships
in name order.

## 7. Data export — the whole group in one ZIP

**Consequence first: the export is the group's whole
archive in readable form** — domain JSON, the calendar
as iCal, the stored files as themselves. Whoever holds
the ZIP holds the group's history; store it like the
group itself. What it never contains, by column rule:
password and token hashes, and integration secrets —
configs export as key **names only**.

[`POST /api/v1/groups/{id}/export`] — and the door is
deliberately narrow: **the group's owner, signed in as a
person.** An API key cannot export, however wide its
scopes; a seat cannot hold the owner role at all. The
export's own audit row commits before the download
answers (commit-before-answer), so the act is on record
even if the download is abandoned.

## 8. Group erasure — the button with no undo

**Consequence first, in full: erasure is hard, total,
and irreversible.** Every row the group owns is deleted
(the plan is computed by following the group's foreign
keys to a fixpoint, so nothing is left pointing at
nothing), stored files no other group references are
swept, and the database is compacted so the words do not
linger in free pages — afterwards, the raw database file
itself greps clean. There is no restore, no tombstone,
no 30-day window: those mercies exist for events and
sources (chapters II and IV), **not** for a group. The
audit log keeps exactly one row: the fact that the group
was erased, and by whom. **Export first (§7) if there is
anything you may ever want again.**

[`POST /api/v1/groups/{id}/erase`] — same narrow door as
export (owner, live person session, no API keys), plus a
**double confirmation**: the request must carry the
group's **exact name** typed back and the word **ERASE**.
A mismatch refuses in plain words with the group
untouched — a mistyped confirmation has never erased
anything.

## 9. The operator's standing reminders

- Backups, restore drills, upgrades, and the runbook for
  the five real failures are the deployment chapter
  (B-199) and `RUNBOOK.md`; erasure (§8) is not a backup
  strategy and a backup bundle is not an export (§7) —
  they answer different questions.
- Source retention (`source_retention_days` in the
  settings pack) hard-removes expired sources without a
  tombstone and stamps their candidates
  `source_deleted_at`; approved events are spared. Set it
  deliberately: retention is erasure on a timer, at
  source scale.
- Notification failures are an organiser surface, not a
  log line: [`GET …/notification-failures`] lists them
  with attempts, and a resend is a person's press.
