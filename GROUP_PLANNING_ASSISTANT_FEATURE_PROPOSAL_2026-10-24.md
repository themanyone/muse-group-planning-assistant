# Group Planning Assistant — Full Feature Proposal

**Status:** PROPOSAL — for group review, not yet agreed
**Prepared:** 2026-10-24
**Team in discussion:** Geoffrey Humphreys, Henry Kroll III, Volmarr Wyrd
**Context:** Meta Global AI Developer Hackathon
- Application: submitted by Henry Kroll III on 2026-10-24 — status "Pending"
- Application deadline: Sunday 2026-10-25, 11:59 PM ET
- Build window: Monday 2026-10-26 to Wednesday 2026-11-04, submissions due Nov 4 at 9:00 PM ET
- Winners announced: Monday 2026-11-09
- Name used on the signup form, as proposed by Henry (2026-10-24): **"group planning assistant"**

> This document answers Volmarr's request in the Hackathon Discussion group
> on 2026-10-24 — *"give me a list of everything you want this to do/have
> as an MD document"* — in reverse: it is a complete proposed list for
> Geoffrey and Henry to add to, cut, argue with, and approve.
> Nothing in here is claimed as already agreed. Features are marked:
> **[Thread]** = actually named in the Hackathon Discussion thread,
> **[Proposed]** = new proposal in this document, for the group to decide.

---

## 1. One-line pitch

A group planning assistant that reads where a group already talks —
chat, newsletters, voice notes, and flyer images — and turns it all
into real, shared plans: calendar events with discussions underneath
them, tasks, checklists, invites, and reminders, with a human always
approving what gets published.

## 2. The problem

Community groups, local clubs, nonprofits, friend groups, and small
teams do not live in project-management software. They live in chat
threads, email newsletters, paper flyers, and photos of bulletin boards.

Because of that:

- Events get announced in chat and are buried within an hour.
- Local clubs often post **only a flyer image** — no event page, no
  text at all — *"so when saturday night arrives i have no idea what
  to do."* — Geoffrey, 2026-10-21 **[Thread]**
- Dates mentioned inside long posts and newsletters never become
  calendar entries.
- Nobody can answer simple questions: What is happening this weekend?
  Who is coming? What do we need to bring? What is still undecided?
- Existing tools split the job: chat in one app, calendar in a second,
  tasks in a third, files in a fourth. Monday.com, Teamup, Discord,
  Slack, and Microsoft Teams / Copilot were all named in the thread
  as prior art with real shortcomings **[Thread]** — they summarise
  meetings, but they do not reliably turn a whole group's messy,
  multi-source talk into approved, published, reminded-about plans.

## 3. Who it is for

- **[Thread]** A local nonprofit Henry knows, which is cobbling together
  a member system where people can talk, populate an event calendar,
  post summaries, send invites and reminders, and discuss each event.
- **[Proposed]** Local clubs, societies, hobby groups, and venues that
  announce mainly by flyer.
- **[Proposed]** Friend and family groups planning trips, reunions,
  game nights, and holidays in group chat.
- **[Proposed]** Small volunteer teams, congregations, classes, sports
  teams, and neighbourhood groups with no IT staff and no budget for
  enterprise tools.
- **[Proposed]** Any group where one exhausted person currently does
  all the manual copying from chat to calendar by hand.

## 4. Design principles

1. **Conversation first, database second.** People keep talking the way
   they already talk. The assistant does the poking of the database —
   Henry noted he was *"locked in the old paradigm where someone
   manually pokes a database"* **[Thread]**; this removes that job.
2. **Nothing publishes without a human approving it.** Every extracted
   event, task, and announcement goes through a review queue first.
   This directly answers Henry's first version, ~20 years ago: the
   company loved it, but it was insecure — anybody could make pages —
   so he took it down **[Thread]**. Security and permissioned
   publishing are core features, not add-ons.
3. **Show your source.** Every extracted field carries the exact quote,
   message, or flyer region it came from. No silent invention. If the
   assistant is unsure, it says so and asks.
4. **Play nicely with outside tools.** Per Volmarr's standing
   interoperability principle: ship clean, lawful defaults; beyond
   that, give users free choice of tools and integrations. Export
   everything in open formats — iCal, Markdown, CSV, JSON — so no
   group is ever locked in.
5. **Simple on the surface, deep underneath.** The winning surface is
   one flow a non-technical person can use in a minute. The full
   system underneath can be as powerful as the group needs.
6. **Accessible by default.** Every image, including every flyer,
   gets a real text description. Body text stays readable, controls
   stay large, and nothing is communicated by colour alone.
7. **Humans and AI agents are both first-class participants.**
   An agent can be given a seat, a role, and a job — and its work
   goes through the same approval rules as everyone else's.

---

## 5. Full feature list

### 5.1 Capture — get plans in from everywhere

- [ ] **[Thread]** Paste or upload a chat export / conversation thread
- [ ] **[Thread]** Connect a live group chat (Messenger group, and other
      chat sources by API) and watch it with the group's permission
- [ ] **[Thread]** Upload / forward a newsletter (text, HTML email, PDF)
- [ ] **[Thread]** Upload a flyer image or a photo of a bulletin board
- [ ] **[Thread]** Scan newsletters and other incoming documents
      automatically for events hidden inside them — Geoffrey
- [ ] **[Proposed]** Forward an email to a group address to file it
- [ ] **[Proposed]** Paste a link (event page, post, article) to extract
- [ ] **[Proposed]** Voice note / voice memo in — a spoken plan becomes
      a written plan
- [ ] **[Proposed]** Photo of a whiteboard, notebook page, or paper list
- [ ] **[Proposed]** Recurring intake: a shared folder, inbox, or feed
      the assistant checks on a schedule
- [ ] **[Proposed]** Drag-and-drop, mobile share-sheet, and a simple
      web upload page for non-technical members

### 5.2 Event extraction engine — the heart of the app

- [ ] **[Thread]** Automatically extract events from a discussion
- [ ] **[Thread]** Extract: title, date, start/end time, place,
      host / organiser, description, who is invited
- [ ] **[Thread]** A discussion thread underneath each extracted event
- [ ] **[Proposed]** Extract also: what to bring, cost, dress / gear,
      age or access limits, RSVP deadline, ticket / signup link,
      contact person, and any decision still open
- [ ] **[Proposed]** Resolve relative dates in context — "this Saturday,"
      "next week," "the 4th" — against the date the message was sent,
      the group's timezone, and other events already on the calendar
- [ ] **[Proposed]** Detect timezones and travel time; flag an event
      that is impossible to reach from the previous one
- [ ] **[Proposed]** De-duplicate: the same event announced in chat,
      in a newsletter, **and** on a flyer becomes **one** event with
      three sources, not three events
- [ ] **[Proposed]** Detect changes and cancellations: "moved to 7pm,"
      "cancelled," "new room" update the existing event and notify
      everyone who RSVP'd — with a visible change history
- [ ] **[Proposed]** Confidence score per field, with low-confidence
      fields highlighted in the review queue
- [ ] **[Proposed]** Source quote for every field, one tap to see the
      original message or flyer region
- [ ] **[Proposed]** Multi-event messages: one long newsletter with
      twelve events becomes twelve draft events, not one blob
- [ ] **[Proposed]** Recurring events: weekly meetings, monthly clubs,
      annual festivals — with exceptions for holidays

### 5.3 Flyer and image understanding

- [ ] **[Thread]** Read flyers — Geoffrey: *"if it could read flyers
      that would be amazing"* — this is the headline feature
- [ ] **[Thread]** Generate a real event from a flyer alone, where no
      text post or event page exists anywhere
- [ ] **[Proposed]** Read a photo of a cluttered bulletin board with
      many posters on it, and separate it into individual flyers
      first, then read each one — this is where SAM 3.1 segmentation
      earns its place
- [ ] **[Proposed]** Handwriting, rotated photos, glare, and partial
      flyers: best-effort reading, with uncertain words marked
- [ ] **[Proposed]** Every flyer gets full alt text / a spoken
      description, so a member who cannot see the image hears the
      whole event
- [ ] **[Proposed]** Extract logos, QR codes, and URLs from flyers
      and make them tappable
- [ ] **[Proposed]** Poster wall / gallery view: all flyers for the
      coming month in one browsable wall, each one already a calendar
      event underneath
- [ ] **[Proposed]** Generate a clean, accessible flyer or event image
      for an event that has none (Muse Image) — optional, and always
      labelled as generated

### 5.4 Context pruning engine — Geoffrey's idea

- [ ] **[Thread]** A proxy / engine that trims a huge context down to
      a reasonable size, using the same class of LLM that will answer
      the prompt to scan the whole context for what is relevant —
      Geoffrey: *"it's not RAG — it's context pruning… RAG uses a
      simple model and arithmetic; this uses a full-fledged LLM to
      scan the entire context for relevant information"*
- [ ] **[Thread]** Re-appliable: prune, answer, prune again, in passes
- [ ] **[Proposed]** Use it inside the planning assistant to answer
      questions from months of group chat without stuffing every
      message into every prompt: "What did we decide about the hall?"
      / "Who volunteered for food last time?"
- [ ] **[Proposed]** Use it to build a tight briefing before each
      event: only the relevant decisions, people, and open questions
- [ ] **[Proposed]** Local-inference mode: pruning in passes is
      expensive per-token in the cloud, but *"much more efficient
      with local inference"* — Geoffrey **[Thread]**. Support a local
      / self-hosted model option for groups that want it
- [ ] **[Proposed]** Show the user what was kept and what was pruned,
      so the summary can be trusted and corrected

### 5.5 The group calendar

- [ ] **[Thread]** A shared group calendar populated by people talking,
      not by one person doing data entry
- [ ] **[Thread]** Generate iCal / .ics files for every event
- [ ] **[Proposed]** Month, week, day, and simple list / agenda views
- [ ] **[Proposed]** A small calendar strip that can sit at the top of
      a group's existing website or page — Henry's original design:
      *"a small calendar that sat above the menu bar"* of the site,
      linking each date to its post **[Thread]**
- [ ] **[Proposed]** Every date mentioned in a post links to that date
      on the calendar, and vice versa — Henry's original behaviour,
      rebuilt securely
- [ ] **[Proposed]** Colour / icon coding by event type, never by
      colour alone — a label always travels with it
- [ ] **[Proposed]** Filters: by sub-group, place, type, or person
- [ ] **[Proposed]** Search the calendar in plain words: "all outdoor
      events in November"
- [ ] **[Proposed]** Availability view: overlay members' "can / can't"
      answers to find a time that works, without exposing anyone's
      private calendar in detail
- [ ] **[Proposed]** Public, members-only, and private calendar views
      of the same underlying events

### 5.6 Event pages

Each event becomes a real page, not just a line on a calendar:

- [ ] **[Thread]** Summary of what will take place
- [ ] **[Thread]** A discussion thread underneath the event
- [ ] **[Thread]** Links to other deadlines and related events
- [ ] **[Thread]** What is needed for the event
- [ ] **[Proposed]** Agenda / running order
- [ ] **[Proposed]** Checklist of things to bring, prepare, book, buy
- [ ] **[Proposed]** Roles: who is hosting, speaking, driving, cooking,
      bringing the projector — and which roles are still unclaimed
- [ ] **[Proposed]** Map / address, directions link, parking and access
      notes
- [ ] **[Proposed]** Files and links attached to the event
- [ ] **[Proposed]** Photos and a recap after the event, so the page
      becomes the group's memory of it
- [ ] **[Proposed]** Clone last time's event as the starting point for
      the next one

### 5.7 Planning — tasks, decisions, and polls

- [ ] **[Thread]** General group planning, not just a calendar —
      Henry: *"Planning assistant is more general, can connect to a
      lot of things"*; the signup name reflects this
- [ ] **[Proposed]** Tasks extracted from chat the same way events are:
      "I'll book the hall," "someone needs to email the caterer"
      become assigned tasks with due dates, in the review queue first
- [ ] **[Proposed]** Simple task board (To do / Doing / Done) —
      deliberately **not** a complex Gantt system; Henry scoped the
      nonprofit need as *"nothing complex like a Gantt chart"* **[Thread]**.
      An optional timeline view can come later
- [ ] **[Proposed]** Decisions log: every "we agreed…" in chat is
      captured as a decision, linked to its source, so nobody has to
      re-argue it a month later
- [ ] **[Proposed]** Open-questions list: everything mentioned but not
      yet decided, kept visible until it is decided
- [ ] **[Proposed]** Polls and votes: pick a date, pick a place, pick
      between options — results feed straight into the event
- [ ] **[Proposed]** "What is still missing?" check: the assistant
      notices an event with no place, no host, or no start time and
      asks the group, in chat, in plain words
- [ ] **[Proposed]** Shopping / supplies list per event, merged across
      events so the group buys once
- [ ] **[Proposed]** Budget-lite per event: rough costs in, who paid,
      who owes what — no accounting suite, just the group truth

### 5.8 Invites, RSVP, reminders, and notifications

- [ ] **[Thread]** Send invites
- [ ] **[Thread]** Send reminders, including upcoming-event reminders
- [ ] **[Thread]** Notifications members can opt into — *"This page
      wants to send you updates…"* — Henry
- [ ] **[Proposed]** RSVP: Coming / Can't / Maybe, plus guests,
      plus-ones, and capacity limits with a waitlist
- [ ] **[Proposed]** Smart reminder schedule: a week before, a day
      before, and morning-of — adjustable per group and per person,
      and quiet hours respected
- [ ] **[Proposed]** Reminders carry the useful facts, not just the
      title: place, what to bring, and anything that changed
- [ ] **[Proposed]** Change alerts: if an event moves or is cancelled,
      everyone who RSVP'd hears immediately, in the channel they chose
- [ ] **[Proposed]** Channels: in-app, email, Messenger, and calendar-app
      alarms — the member chooses, the assistant does not spam all four
- [ ] **[Proposed]** A weekly digest: "Your week with the group" —
      what is on, what needs a reply, what is still undecided
- [ ] **[Proposed]** A "Saturday night" view: Geoffrey's original pain
      — open the app on the night and instantly see what is on near
      you tonight, gathered from every club flyer the group has seen

### 5.9 Publishing and integrations out

- [ ] **[Thread]** Generate iCal files
- [ ] **[Thread]** Generate Facebook Events, in addition to iCal —
      Geoffrey
- [ ] **[Thread]** Integrate with various apps, especially ones with
      APIs — Henry; and integrate with Samtal — Geoffrey
- [ ] **[Proposed]** Google Calendar: one-tap "add to calendar," and
      optional two-way sync for a group's own calendar
- [ ] **[Proposed]** Apple Calendar / Outlook via standard iCal feeds
- [ ] **[Proposed]** Samtal integration: use a Samtal hall as a live
      source conversation; events extracted there appear on Samtal's
      own calendar too — Samtal already has a calendar **[Thread]**
- [ ] **[Proposed]** Publish a draft social post for an event (text +
      image + alt text) for a human to approve and post
- [ ] **[Proposed]** Embeddable widget and a simple public events page
      for a group's website
- [ ] **[Proposed]** Print view: a clean paper list / poster of the
      month's events for a real noticeboard
- [ ] **[Proposed]** Open API + webhooks, so other tools can read
      approved events and plans and build on them
- [ ] **[Proposed]** Export everything, any time: iCal, CSV, JSON,
      Markdown. Leaving must be as easy as joining

### 5.10 Summaries, briefings, and memory

- [ ] **[Proposed]** After any discussion: a short, faithful summary —
      what was decided, what happens next, who owns what
- [ ] **[Proposed]** Before any event: a one-page briefing for the host
- [ ] **[Proposed]** After any event: a recap draft from the discussion
      and photos, for the newsletter
- [ ] **[Proposed]** Newsletter drafting: turn the month's events and
      recaps into a ready-to-send newsletter draft
- [ ] **[Proposed]** Ask the group memory: plain-language questions
      answered from the group's own approved history, with sources —
      powered by the context pruning engine in §5.4, not blind search
- [ ] **[Proposed]** "Last time we did this" — planning a repeat event
      surfaces how the last one went, what it cost, and what to fix

### 5.11 Members, roles, and nonprofit / ERP-lite

- [ ] **[Thread]** Member system for a group / nonprofit: members can
      talk, populate the calendar, and take part — Henry's anchor case
- [ ] **[Proposed]** Member directory with roles: admin, organiser,
      member, guest — and sub-groups / chapters
- [ ] **[Proposed]** Volunteer signup: shifts and roles on an event
      that members claim, with gaps made visible
- [ ] **[Proposed]** Attendance history: who came, purely for the
      group's own planning — never for ranking or shaming members
- [ ] **[Proposed]** Simple resource list: the hall, the projector,
      the van — what exists, who has it, when it is booked
- [ ] **[Proposed]** Announcements board separate from chat, so the
      important post does not drown
- [ ] **[Proposed]** Deliberately stops short of a full ERP: no
      payroll, no accounting ledger, no complex project charts in
      the core product

### 5.12 Collaboration and AI participants

- [ ] **[Proposed]** A collaborative, ChatGPT-like shared interface —
      Henry: he has used chat-style coding / research tools, *"but
      neither are like the idea I just had of a collaborative
      ChatGPT-like interface"* **[Thread]** — a room where several
      people **and** the assistant work on the same plan together,
      live, instead of each person having a private chatbot
- [ ] **[Proposed]** AI agents can be invited into a group with a
      named role (secretary, flyer-reader, reminder-keeper) and
      clear permissions
- [ ] **[Proposed]** Every agent action is visible, attributed, and
      reversible; agents publish nothing without the same human
      approval a new member would need
- [ ] **[Proposed]** Samtal as a living example: Samtal is Volmarr's
      human + agent chat hall, already live, where agents and humans
      socialise, work, and play together, with a calendar, discussion
      boards, and outside-tool integrations **[Thread]** — the
      planning assistant should connect to it, not copy it

### 5.13 Security, privacy, and trust

- [ ] **[Thread]** Permissioned publishing — the direct fix for the
      insecurity that killed Henry's first version
- [ ] **[Proposed]** Roles enforced everywhere: only approved roles
      can publish, invite outsiders, or change an event
- [ ] **[Proposed]** Private by default: a group's conversations are
      read only with the group's permission, and only the parts the
      group connects
- [ ] **[Proposed]** Drafts vs. published: extracted material starts
      as a private draft; nothing leaves the group until approved
- [ ] **[Proposed]** Audit log: who approved what, what changed, when
- [ ] **[Proposed]** Delete means delete: a group can remove a source,
      an event, or its whole history
- [ ] **[Proposed]** Rate limits, spam and abuse protection on any
      public page or form
- [ ] **[Proposed]** Clear data statement in plain words: what is
      stored, where, and who can see it

### 5.14 Accessibility and inclusion

- [ ] **[Proposed]** Real alt text on every image and generated image
- [ ] **[Proposed]** Flyer content always also available as plain text
- [ ] **[Proposed]** Works at 125–200% text scaling; large tap targets;
      readable contrast; nothing signalled by colour alone
- [ ] **[Proposed]** Plain-language mode: short sentences, no jargon,
      for groups whose members are not technical
- [ ] **[Proposed]** Timezone, date-format, and language respect per
      member; event times always shown with their place / timezone
- [ ] **[Proposed]** Low-bandwidth and older-phone friendly core flow

### 5.15 Admin and group settings

- [ ] **[Proposed]** Group profile: name, description, home timezone,
      usual meeting place
- [ ] **[Proposed]** Which sources the assistant may read, per source,
      changeable at any time
- [ ] **[Proposed]** Review-queue settings: who can approve, whether
      one approver is enough, auto-approve for trusted posters (off
      by default)
- [ ] **[Proposed]** Reminder defaults, digest day, quiet hours
- [ ] **[Proposed]** Templates: meeting, club night, trip, fundraiser,
      class, match, celebration — a new event starts from the last
      good one

---

## 6. How the required Meta models are used

| Capability | Meta model | Used for |
|---|---|---|
| Reading flyers and photos of boards | Muse Image / vision | §5.3 — flyer text, layout, QR codes, board photos |
| Separating many posters in one photo | SAM 3.1 | §5.3 — segment a cluttered bulletin board before reading each flyer |
| Extracting events, tasks, and decisions from chat and newsletters | Muse Spark / Muse Wonder | §5.2, §5.7 — structured extraction with source quotes |
| Resolving "this Saturday," times, and places | Muse Spark / Muse Wonder | §5.2 — relative dates, timezones, conflicts |
| Summaries, briefings, recaps, reminder wording | Muse Spark / Muse Wonder | §5.10, §5.8 |
| Context pruning | Muse-class LLM as the scanner | §5.4 — Geoffrey's pruning engine; the scanner is the same class of model that will answer |
| Generating an optional event image | Muse Image | §5.3 — only where an event has no image, and labelled |

Honest note: SAM 3.1 is load-bearing only in the bulletin-board case.
Everywhere else it is a helper, not the foundation.

## 7. Core user flows

**Flow A — The flyer (the headline demo)**
1. A member photographs a flyer, or a whole bulletin board.
2. The assistant separates and reads the flyers, and drafts an event
   for each one, with sources and confidence.
3. An organiser approves one in the review queue in seconds.
4. The group gets: a calendar entry, an .ics file, a draft Facebook
   Event, an event page with a discussion thread, and reminders set.

**Flow B — The chat that becomes a plan**
1. A group argues happily in chat about a trip for forty messages.
2. The assistant drafts: the event, a task list with owners, a
   decisions log, and a list of what is still undecided.
3. The group approves, edits two things, and carries on talking —
   underneath the event this time.

**Flow C — The newsletter**
1. A monthly newsletter arrives with twelve events buried in prose.
2. Twelve draft events appear in the review queue, de-duplicated
   against events the group already has from chat and flyers.
3. Approving them populates the calendar and the weekly digest.

**Flow D — Saturday night**
1. A member opens the app on Saturday night.
2. They see what is on tonight, near them, from every source the
   group follows — including clubs that only ever posted a flyer.

## 8. Hackathon build slice — Oct 26 to Nov 4

The full list above is the product vision. The contest build must
be a thin, complete, working slice of it — simple enough to finish
and demo, honest enough to be real:

**Must have (the demo path, end to end)**
1. Ingest three sources: a pasted conversation, a newsletter text,
   and a flyer image.
2. Extract structured events with source quotes and confidence.
3. A human review queue: approve, correct, reject.
4. Emit: an event page with summary and discussion thread, an .ics
   download, and draft Facebook Event text.
5. One reminder flow, shown working.
6. One bulletin-board photo with at least two flyers separated
   and read (the SAM 3.1 moment).

**Should have, if time allows**
7. Task and decision extraction from the same conversation.
8. A Google Calendar "add" link beside the .ics download.
9. A weekly digest page.
10. Connecting one live conversation — a Samtal hall — as a source.

**Explicitly out of the contest build**
Full ERP features, payments, a Gantt chart, native mobile apps,
two-way sync with every calendar, and a public launch of any kind.

## 9. After the hackathon — roadmap sketch

- **Phase 2:** Live chat connections, change / cancellation detection,
  RSVP and capacity, smart reminders on real channels, polls.
- **Phase 3:** The full context pruning engine as the group's memory;
  collaborative planning room; Samtal integration in depth.
- **Phase 4:** Nonprofit / club pack — members, volunteers, resources,
  announcements, embeddable public calendar.
- **Phase 5:** Local-inference option, open API and webhooks,
  templates library, multi-language support.

## 10. What makes this different

- **Monday.com / Teamup / Slack / Discord / Teams** organise work a
  person has already entered. This assistant does the entering —
  from talk, paper, and pictures — and asks a human only to approve.
- **Meeting summary tools summarise a meeting.** This plans the next one:
  event, tasks, roles, supplies, invites, and reminders included.
- **A normal calendar holds what you typed.** This calendar also
  holds what your group said, emailed, and pinned to a board.
- **Flyer-only groups are invisible to every competitor named in the
  thread.** Reading flyers is the difference between "another
  calendar app" and a tool a whole local scene can actually use.

## 11. Risks and unknowns

- Flyer reading quality on bad photos — mitigated by confidence
  scores, source display, and the human review queue.
- Date / timezone mistakes are the most damaging error class —
  mitigated by showing the resolved date beside the original words
  ("this Saturday" → Saturday 2026-10-31) before approval.
- Privacy expectations differ per group — mitigated by per-source
  permission, drafts-by-default, and plain-words data statements.
- Scope creep — the full vision is large by design. The contest slice
  in §8 is the discipline: nothing outside it ships before Nov 4.
- The competition's Official Rules were not yet published when this
  proposal was written; they must be read and accepted by the team
  before the build starts.

## 12. Questions for Geoffrey and Henry

1. Is the headline demo the flyer, the chat, or the newsletter?
   This proposal bets on the flyer, with chat as the second act.
2. Who is the nonprofit, may its story be told in the application
   and demo, and may it be named?
3. Geoffrey's context pruning engine: is it in the contest build,
   or is it the Phase 3 memory layer? This proposal puts a simple
   version in the vision and the full engine after the contest.
4. Which one live source do we connect first — a Messenger group,
   a Samtal hall, or only uploads for the demo?
5. Project name: "group planning assistant" describes it for the
   form. Do we want a real name before Nov 4?
6. Who owns which slice in the build week — extraction, flyer
   reading, review queue / event pages, or exports / reminders?

## 13. Source note

Built from the Hackathon Discussion Messenger thread (Geoffrey
Humphreys, Henry Kroll III, Volmarr Wyrd, 2026-10-21 to 2026-10-24,
209 messages), Henry's and Geoffrey's application messages of
2026-10-24, and the earlier plan at
`HACKATHON_EVENT_CALENDAR_PLAN_2026-10-21.md`. Direct quotes are the
speakers' own words from that thread. Everything marked [Proposed]
is new in this document and is an offer for discussion, not a claim
about what anyone has already agreed.
