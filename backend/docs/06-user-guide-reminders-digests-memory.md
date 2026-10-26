# User guide VI — Reminders, digests, and group memory

*Chapter 6 of the user guide (slice B-187). The chapter
about being kept up to date without being nagged, and about
asking the group's own past a question — with the honest
limits of what the AI may and may not do, stated where you
meet them. Your inbox is
[`GET /api/v1/users/me/inbox`]: everything the group sent
you, in the app, whether or not an email also went out.*

## 1. Reminders — who gets them, and when

**Task: know that a coming event will remind you, and
change the rhythm if your group wants a different one.**

1. Reminders are **materialised**, not worked out at the
   last minute: when an event is published, one reminder
   per rule is written for each person who has answered
   **coming or maybe** (an RSVP, chapter IV), and for the
   host. No RSVP, no reminder — the app does not remind
   people who never said they were coming.
2. The default **reminder rules** are three: **7 days
   before** at 09:00, **1 day before** at 09:00, and the
   **morning of** the event at 08:00 — all in the event's
   own timezone, not yours and not the server's. An
   organiser can replace the list in group settings
   [`PATCH /api/v1/groups/{id}`] with any set of
   days-before rules (1–365 days) plus the morning-of rule.
3. A reminder's words come from the event's own facts —
   its formatted start, its place, what to bring — and, for
   a moved event, what changed since last time. Nothing in
   a reminder is invented.
4. **The event moved? The old reminders die with the old
   time.** Editing, moving, or losing an RSVP supersedes
   the outstanding reminders and deletes their queued
   sends in the same act that makes the change, then writes
   fresh ones for the new facts. You will never be reminded
   to be somewhere at a time the event no longer has.
5. Your own switches still govern (chapter I): a channel
   you turned off stays off, and **quiet hours hold**
   reminder-category messages until they end — held, never
   dropped. One exception, by design: **urgent** messages
   are never held, and a cancellation alert is urgent,
   because cancelling is an organiser's deliberate act and
   people must not travel to a dead event.

### Optional AI wording — and its leash

A group may opt in to AI-polished reminder wording
(`ai_reminder_wording` in group settings). The leash is
mechanical, not a hope: the AI draft ships **only if** the
formatted start, the place, and every bring item survive
in it **verbatim**. If any fact is altered or missing,
the plain template ships instead, and the record keeps
both versions and says which one was sent
(`wording_source`). Friendlier words, never different
facts.

## 2. Change alerts — a move is announced, not whispered

When a published event changes in a way that matters
(start, end, place, timezone), it is marked
**needs re-announcement** (chapter IV). Nothing is sent at
that moment. An organiser reviews the draft announcement —
built from the actual changes, old → new in the event's
timezone, never a bare "updated" — and **approves** it
[`POST /api/v1/groups/{id}/events/{event_id}/reannounce-drafts/{draft_id}/approve`].

Approval sends the alert to exactly the people the event
affects: the members who answered coming or maybe, and
invited guests at their invited address. It clears the
needs-re-announcement mark and stamps who sent it and
when. Approving twice is refused — an alert is sent once.

## 3. The weekly digest

**Task: get one email-and-inbox summary instead of a
drip of notices.**

1. The **digest** is composed once a week from two honest
   parts: the planning digest (chapter V — this week's
   events, unclaimed tasks, open questions, undecided
   polls) and next week's published events. It is stored
   as composed — what was sent is what you can re-read
   [`GET /api/v1/groups/{id}/digests`].
2. The schedule is a group setting: weekday (Monday by
   default), time of day, and an on/off switch
   (`digest_weekday`, `digest_time`, `digest_enabled`).
   Saving the settings re-anchors the schedule; each run
   queues its own successor, so the chain survives without
   anyone remembering it.
3. An organiser can run one now
   [`POST /api/v1/groups/{id}/digest/run`]. Every active
   member gets one digest notification — through the
   channels and quiet hours of chapter I, like everything
   else.
4. The digest's pages carry no images, and a check runs on
   every render that any image present would need real
   alt text — a description a screen reader can read, or
   the render fails. An undescribed picture never rides
   along silently.

## 4. Asking the group memory

**Task: ask what the group decided, agreed, or learned —
and get an answer you can check.**

Ask a question
[`POST /api/v1/groups/{id}/ask`]. A member act — and an
agent seat may ask too; the memory belongs to the group.

What happens, in order, is the whole honesty of the
feature:

1. **The corpus is approved history only.** Memory reads
   sources shared for group history, the group's
   **current decisions**, and **approved summaries** —
   never drafts, never the unreviewed queue, never chat
   nobody agreed to share. If it was not approved, it is
   not memory.
2. **Pruning, not search-by-similarity.** A full model
   reads the corpus chunk by chunk and judges relevance
   to your question; it may take a second pass over what
   it dropped. This is deliberately **not embedding RAG**:
   no vector index decides what your question means. A
   token budget caps the work, checked before each pass —
   if the budget runs out, the run says so
   (`budget_stopped`) instead of quietly pretending it
   read everything.
3. **The draft answer is built from the survivors only**,
   and **every citation is verified** against the words
   it claims to quote. A claim whose quote cannot be
   found in the corpus is marked unsupported.
4. **If nothing verifies, the answer is a refusal**, led
   by the words **"I couldn't verify this"** — with any
   found material shown and clearly labelled, its
   citations empty. A memory that cannot prove an answer
   does not dress a guess up as one.

## 5. Summaries, briefings, and recaps — drafts until approved

The memory grows through three doors, and **every one of
them produces a draft a person must approve**:

- **Summarise a discussion**
  [`POST /api/v1/groups/{id}/summaries/discussion`]
  (organiser): the summary is stored as a draft summary,
  and the decisions and next actions it names arrive as
  **review-queue candidates** (chapter III) — never as
  written decisions or owned tasks. Hidden or deleted
  comments never enter a summary.
- **Brief an event**
  [`POST /api/v1/groups/{id}/events/{event_id}/briefing`]:
  the host's pack — the structured present (decisions
  touching the event, open questions, unclaimed role
  slots, the latest approved recap) woven with the pruned
  history that matters to this event. A warning the group
  recorded months ago about this venue is exactly what
  pruning exists to surface.
- **Recap an event**
  [`POST /api/v1/groups/{id}/events/{event_id}/recap`]
  (organiser): drafted from the event's discussion, its
  attendance **count** (counts only — chapter on
  attendance privacy is in the admin guide's pack), and
  its flyers **only where the alt text is complete** — a
  picture whose description is still "awaiting
  description" is left out, not described by guesswork.

Approving a draft
[`POST /api/v1/groups/{id}/summaries/{summary_id}/approve`]
(organiser) is the only door from draft to memory. One
loop closes here on purpose: approving a **newsletter**
draft — woven from a month's approved recaps
[`POST /api/v1/groups/{id}/newsletters/draft`] — also
files it as a source, so next month's memory can read
this month's letter. Read the group's summaries, drafts
included, at [`GET /api/v1/groups/{id}/summaries`].

## 6. Seeing what the memory engine did

Transparency is a surface, not a promise. Every prune,
ask, and briefing run is listed
[`GET /api/v1/groups/{id}/memory/runs`] (member) with the
chunk ids it **kept** and **dropped**, how many passes it
took, the model it used, and what it cost. An organiser
can purge one run record, or all of them
[`DELETE …/memory/runs/{run_id}`, `DELETE …/memory/runs`].
Purging deletes the receipts, never the summaries — the
group's own words are not the engine's bookkeeping.

A group's daily AI budget (set in group settings) caps
all of this together with extraction: at the cap, work
defers to the next day and the status surface says
**paused, budget**, in plain words, with the spend and
the cap. Nothing fails silently for money.

## 7. What the AI may and may not do — the honest list

**The AI may:** read what a person gave it (a source, a
flyer, a thread), draft candidates, summaries, briefings,
recaps, and friendlier reminder wording under the
verbatim-facts leash, judge which approved history is
relevant to a question, and keep a complete, inspectable
record of every run.

**The AI may not:**

- **Approve anything.** Approval — of a candidate, a
  summary, an announcement — is a person's act, always.
  An agent seat cannot approve even holding a publish
  scope, and the memory engine has no approve door at all.
- **Assign work to a person.** Suggested names stay
  labels until a person makes them owners (chapter V).
- **Invent a fact it cannot point at.** Unverifiable
  claims are marked unsupported; an unverifiable answer
  is a refusal, not a guess.
- **Read unapproved history as memory.** Drafts, hidden
  comments, and unshared sources are outside the corpus.
- **Spend past the group's budget**, or hide that it
  stopped: deferral and `budget_stopped` are stated.
- **Send anything on its own.** Reminders, alerts, and
  digests are materialised by people's acts — publishing,
  approving, scheduling — and delivered by the outbox
  under each member's own preferences.

Where a live AI endpoint is not connected, all of the
above runs on the recorded/mock drivers and says so —
the drivers change what the words are worth, never what
the rules are.
