# User guide V — Planning: tasks, decisions, and the rest

*Chapter 5 of the user guide (slice B-186). For groups
getting things done between events. Everything here can be
created directly by a person, and most of it can also arrive
as a draft through the review queue (chapter III) — approval
mints the same records either way. One search
[`GET /api/v1/groups/{id}/search`] finds events, tasks,
decisions, and questions together, and the planning digest
[`GET /api/v1/groups/{id}/planning-digest`] is the week at a
glance: this week's events, unclaimed tasks, open questions,
and undecided polls.*

## 1. Tasks — and the owner law

**Task: give a job a name, a due date, and — by a person's
choice — an owner.**

1. Create [`POST /api/v1/groups/{id}/tasks`] or pick one from
   the list [`GET …/tasks`], filterable by status, by event,
   and to **unclaimed** only. A task shows `overdue` and
   `unclaimed` worked out fresh on every read — never stored,
   never stale. A due date that is a day only is due at the
   **end of that day**, local time.
2. Status moves forward — open, in progress, done (which
   stamps when it finished) — and a task can be cancelled
   from open or in-progress, or reopened from done.
3. **The owner law, in plain words:** extraction often drafts
   a volunteer's name ("Sam said he'd bring the kayaks").
   Those words land only as a **suggested owner label**. A
   task's real `owner_id` is set solely by a person's act —
   creating the task naming a member, assigning it
   [`PATCH …/tasks/{task_id}`], or a reviewer putting a real
   member into the draft before approving. The AI
   never assigns a silent victim: nobody finds themselves
   owning a job because a chat mentioned their name.

## 2. The decisions log

A **decision** [`POST /api/v1/groups/{id}/decisions`] is the
group's settled truth, kept apart from the chat it came
from. Minds change, and the log handles it honestly: a new
decision can **supersede** an old one, and the old one is
kept, pointing forward, the new one pointing back — the
chain reads root to current
[`GET …/decisions/{decision_id}/chain`]. The
`?current=true` view is the settled truth only. Superseding
a decision that is not the current one is refused: history
branches are settled in order, by people.

## 3. Open questions and the gaps list

An **open question** [`POST /api/v1/groups/{id}/questions`]
is asked of the group; naming who must answer it is a
person's edit, never the AI's. A question resolves (the
answer recorded), or is **dropped** — a settled state, not a
deletion — and resolving may mint the decision it produced
in the same act, linked to the question.

**Task: see everything an event still lacks.** The **gaps**
list [`GET /api/v1/groups/{id}/events/{event_id}/gaps`]
folds it into one place: missing fields (no place yet, no
host yet), the event's open questions, its unclaimed roles,
and its unclaimed tasks. Half-planned stops being a feeling
and becomes a checklist.

## 4. Polls

**Task: let the group choose — a thing, or a time.**

1. Create [`POST /api/v1/groups/{id}/polls`] as a **choice**
   poll (options you name) or a **date** poll (options that
   are real moments; the label defaults to the formatted
   moment). A poll can attach to an event.
2. Vote [`POST …/polls/{poll_id}/vote`]: one current vote per
   person — voting again **changes** your vote, it never
   doubles it. Closing time ends voting by the clock;
   closing early freezes the result
   [`POST …/polls/{poll_id}/close`].
3. **Apply the winner** (organiser)
   [`POST …/polls/{poll_id}/apply-winner`]: on an attached
   event, the event **moves** to the winning time through
   the ordinary edit door — history and re-announcement ride
   along; with no attached event, one is minted from the
   winner. A tie, or a poll with no votes, is **refused in
   plain words** — the backend never picks a winner for you.

## 5. Checklists, role slots, and supplies

**Checklists** [`POST /api/v1/groups/{id}/events/{event_id}/checklist`]
are the bring-and-prepare lines for an event, quantities
included. **Claiming a line is a one-person promise**: claim
for yourself [`POST …/checklist/{item_id}/claim`]; claiming
another person's line — or taking a line already held — is
refused. A line can be held by a member, or claimed under an
agent seat's label.

**Role slots** are the same promise shaped as jobs: "driver",
"door person" — one row per person needed, claimed and
released through the same door
[`POST /api/v1/groups/{id}/events/{event_id}/roles`].

**Supplies** [`GET /api/v1/groups/{id}/supplies`] answers
"what is the group bringing this week?" — every checklist
line across the week's non-cancelled events, folded by name
(so three events' "cups" add up), with the per-event
breakdown kept visible underneath the totals.

## 6. Budget-lite

Per-event money, kept deliberately small — add a cost line
[`POST /api/v1/groups/{id}/events/{event_id}/costs`], read the
summary [`GET …/events/{event_id}/budget`]:
who paid, for what, how much. Amounts are **integer cents
end to end** — the form takes cents, the store keeps cents,
balances sum exactly; there are no rounding surprises
because there is no rounding. A payer is a member, or words
("guest cash") with no member attached. The summary sorts by
who paid most. This stops at group truth by design: it is
not a ledger, and it moves no money.

## 7. The availability finder

**Task: find the evening everyone can make.**

1. An organiser proposes slots
   [`POST /api/v1/groups/{id}/availability/slots`].
2. Each member marks **can** or **can't** against the
   proposed slots only [`POST …/availability/slots/{slot_id}/mark`]
   — marking again updates your mark.
3. The ranking is deterministic and shown as computed:
   most cans first, then fewest can'ts, then earliest start.
   **The privacy boundary is the shape:** only marks against
   proposed slots exist. The app reads nobody's private
   calendar, stores no free/busy, and shows nothing you did
   not mark here.

## 8. Templates

A **template** [`GET /api/v1/groups/{id}/templates`] is a
plan you can stamp out again: an event shape, its tasks (due
as day-offsets from the event), its checklist lines, and its
roles. Save one from a real event
[`POST …/templates/from-event/{event_id}`] — claims become
open slots again; nobody is silently re-volunteered — then
**instantiate** it on a new date
[`POST …/templates/{template_id}/instantiate`]: the event,
tasks, checklist, and roles are built through the same doors
as direct entry, all unclaimed. Seven starters ship with
every group — **meeting, club night, trip, fundraiser,
class, match, celebration** — seeded once, by name, ready to
adapt.
