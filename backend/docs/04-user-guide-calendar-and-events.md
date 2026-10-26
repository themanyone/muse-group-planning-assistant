# User guide IV — Calendar and events

*Chapter 4 of the user guide (slice B-185). For everyone who
shows up. An **event** here is a published one — minted by
approval (chapter III) or entered directly by an organiser.
Draft events are visible to organisers only: below that role
a draft answers as if it did not exist, on every screen in
this chapter.*

## 1. The three views

**Agenda** [`GET /api/v1/groups/{id}/agenda`] — the coming
events as a list, already expanded: a weekly event appears
once per time it actually happens, never as a rule you must
decode. Search words match title, place, and host; type,
place, and sub-group filters narrow the list; paging is by
cursor, so the list never skips or repeats as events land.

**Month** [`GET /api/v1/groups/{id}/month`] — whole
Monday-first weeks covering the month, each day's events in
place, days outside the month marked as such. The grid maths
is done for you; what you see is the month.

**Tonight — the Saturday-night view**
[`GET /api/v1/groups/{id}/tonight`] — "what is on tonight?"
answered in the group's own timezone: the evening runs
**15:00 today to 03:00 tomorrow**, local. Events with a
pinned location come first, then by start time. An event
born from a flyer carries its picture's thumbnail and
description status with it — an `awaiting description` flyer
stays a visible defect here too (chapter II).

## 2. The event page

Open any event [`GET /api/v1/groups/{id}/events/{event_id}`]
and you see it at **your** level: the server builds public,
member, and organiser views itself, and fields above your
level — access notes, host, cost, what to bring — are
**absent** from your view, not blanked out. A public-listed
group's public page [`GET /api/v1/public/groups/{slug}/events`]
exists only for groups that chose that visibility.

The page also carries the event's links (§5), its flyer if
it has one, its open contradictions if you are an organiser
(chapter III, §8), and its full **change history**: every
edit is recorded field by field, and an edit touching time,
place, or timezone marks the event as needing a
re-announcement, so nobody is quietly moved.

## 3. Discussion

Every event has a **discussion** thread
[`GET/POST /api/v1/groups/{id}/events/{event_id}/comments`].
Post, edit your own words, delete your own (a deleted
comment leaves a tombstone line, not a gap). An organiser
may hide a comment — hidden words are invisible to members,
visible to organisers marked as hidden, and the hiding is
audit-logged. New comments also arrive on the group's live
stream for signed-in members. The same thread system serves
tasks, decisions, and polls (chapter V) — learn it once.

## 4. RSVP and the waitlist

**Task: say you are coming — with your whole party.**

1. Open the event and answer
   [`PUT /api/v1/groups/{id}/events/{event_id}/rsvp`] —
   coming, maybe, or not, with your party size. Changing
   your answer replaces it; withdrawing removes it
   [`DELETE …/rsvp`]. The list is at [`GET …/rsvps`].
2. **Capacity is honest.** Parties are seated in arrival
   order. When the event fills, further parties join the
   **waitlist** — and the waitlist is **strictly
   next-in-line**: if the party at the head needs more seats
   than freed up, nobody behind them jumps the line, however
   small their party.
3. When a seat frees — an answer changes, someone withdraws,
   or an organiser raises the capacity — the head of the line
   is promoted in order and **told**: a promotion sends a
   notification (chapter VI), except a promotion caused by
   your own answer change, which your own screen already
   told you.
4. **Guests:** an organiser can invite by link (chapter VI);
   a guest answers through the invite with their email as
   their name — no account needed, and a tampered link
   simply finds nothing.

## 5. Take the event with you: .ics, Google, the feed

- **Download .ics** [`GET …/events/{event_id}/ics`] — one
  file for this event, importable by any calendar app. A
  cancelled event's file says `CANCELLED`; it never pretends
  the event vanished.
- **Add to Google Calendar** — the event page's links carry a
  ready-made Google add link, built by the server with the
  true times and offsets; one click opens Google's form
  filled in. (Continuing two-way sync with a group's Google
  calendar is an organiser's integration — admin guide,
  B-188.)
- **The calendar feed** [`POST …/calendar/feeds`] — a
  subscribable address your calendar app polls: create one,
  and its token is **shown once and stored only as a hash**;
  filters on the feed (statuses, a place word, a sub-group)
  narrow what it carries. Revoking a feed
  [`POST …/calendar/feeds/{feed_id}/revoke`] kills the
  address instantly. A moved occurrence rides the feed as a
  moved occurrence; a cancelled one as an exception — your
  app shows the truth without a re-subscribe.

## 6. Print view

**Task: a paper month for the noticeboard.**
[`GET /api/v1/groups/{id}/print/month?month=YYYY-MM`] — one
row per event carrying every fact in words: title, the
group-local date and time, place, address, host, cost, what
to bring, and the description — rendered as text you can
print as it stands. If a fact is missing from the event, it
is missing from the paper: printing invents nothing.

## 7. When events collide

The conflicts view [`GET /api/v1/groups/{id}/conflicts`]
names three kinds, computed over the times events actually
happen: the same **host** double-booked, the same **place**
double-booked, and **travel-impossible** pairs (too far apart
for the time between them). Overlap is strict — back-to-back
events in the same room never flag. Booked **resources**
(chapter V's neighbours in the nonprofit pack) flag their
own overlaps in the same view. A flag is information for a
person to act on; the app never moves an event by itself.

## 8. Organisers: edit, cancel, clone, repeat

- **Edit** [`PATCH …/events/{event_id}`] needs the version
  you read (an `If-Match` of the event's current version):
  if someone else saved first, you get a plain refusal
  naming the current version — re-read, then re-apply.
  Nothing silently overwrites a colleague's edit.
- **Cancel** [`POST …/events/{event_id}/cancel`] takes a
  reason, keeps the event visible as cancelled, and marks
  it for re-announcement; a cancelled event refuses edits.
- **Clone** [`POST …/events/{event_id}/clone`] copies the
  shape — including a repeating rule — but never its RSVPs,
  discussion, or history; the clone is born a draft.
- **Recurrence:** a repeating event carries its rule
  (weekly, monthly by day, and kin), and every screen in
  this chapter shows **occurrences, never the rule**; one
  occurrence can be moved or cancelled as an exception
  without touching the rest. Deleting an event is a soft
  delete — restorable whole inside 30 days, as sources are
  (chapter II).
