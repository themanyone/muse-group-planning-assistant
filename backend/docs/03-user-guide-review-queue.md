# User guide III — The review queue: the trust workflow

*Chapter 3 of the user guide (slice B-184). For the people who
decide. Everything the app drafts — from a pasted chat, a
newsletter, a flyer photo — waits in the **review queue** as a
**candidate**: a draft with its evidence attached. Nothing in
the queue is real yet. A candidate becomes real only when a
person approves it here, and an agent seat can never approve —
that refusal (`seat_cannot_approve`) is enforced in the
service, not just hidden in the buttons.*

## 1. Open the queue and read a candidate

**Task: find the drafts that need a decision.**

1. Choose **Review queue** in your group.
   [`GET /api/v1/groups/{id}/candidates`] The queue is newest
   first; filter it by kind (event, task, decision, question,
   and the change kind of §7), by status, or by a minimum
   confidence. Queue health — how many wait, and whether
   extraction is paused on the group's AI budget — is at
   [`GET /api/v1/groups/{id}/candidates/status`].
2. Open one candidate. [`GET …/candidates/{candidate_id}`]
   On screen you should see, top to bottom: the **draft**
   itself (title, and the fields the reader found — a date, a
   place, a host, a cost), the **confidence**, the **flags**
   (§3), the **provenance** (§2), and the record of who or
   what made the draft — a person's name, or a machine author
   such as `system:extraction`, plus the prompt name and
   version that produced it. A draft remembers its making.
3. Statuses you will meet: `pending` (waiting), `edited` (a
   person changed it — still waiting), `approved`, `rejected`,
   and `merged` (§6). Until approval mints the real thing,
   the candidate's `resulting_entity` fields stay empty —
   approval fills them, in the same act.

## 2. Provenance: the quote under every claim

**This is the trust workflow's heart: no claim without its
evidence shown.**

- Each field of the draft carries its **provenance quote** —
  the exact words from the source the field was read from,
  and which chunk of which source they came from. Read the
  quote, not just the field: the field is the app's reading,
  the quote is the ground it stands on.
- A field whose words could **not** be found in the source
  is marked **`supported: false`**. It keeps its value on
  screen — nothing is hidden from you — but it carries no
  provenance and its confidence does not count. Treat an
  unsupported field as a rumour: correct it from the source
  yourself, or reject the draft.
- **Flyer regions:** a draft read from a flyer photo carries,
  per field, the **region** of the picture the words were
  read from (a bounding box). A field whose claimed region is
  not a valid box is `supported: false` in the same way —
  the box is shown, labelled with its verdict.
- If the draft's source has since been deleted (chapter II),
  the draft stays, keeps the source's id, and shows its
  **`source_deleted_at`** stamp: its quotable evidence is
  gone, so weigh what remains accordingly.

## 3. Confidence and flags, in plain words

**Confidence** is the reader's own score for the draft as a
whole. It informs; it never decides. The **flags** are
computed by the server — a client never sends them — from a
closed vocabulary, each naming the field it concerns:

- `low_confidence:<field>` — the reader itself was unsure.
- `missing_required:<field>` — a field this kind cannot
  publish without is absent or unsupported.
- `unsupported:<field>` — a value with no quote under it (§2).
- `ambiguous_date:<field>` — the date words honestly read two
  ways ("this Friday", once the day has passed, reads as last
  Friday or next); both readings were considered, and the
  draft does not pretend to know which you meant.
- `unresolved_date:<field>` — date words the resolver does
  not know at all. Unknown is stated, never guessed.
- `conflicting:<field>` — another source's draft in this
  group says a different value for the same thing; the flag
  stands on **both** drafts.
- `shared_chunk_unflagged` — one piece of source text was
  used by more than one draft without the sharing being
  flagged, so check the neighbours before approving.
- `possible_duplicate` — a flyer that looks like one already
  read (same picture, near enough, and the same drafted title
  or date) points at its elder with a `duplicate_of` note:
  a merge suggestion (§6), never a silent second event,
  never an automatic merge.

## 4. Edit a draft

**Task: correct what the reader got wrong, before deciding.**

1. Open the candidate and choose **Edit**.
   [`PATCH …/candidates/{candidate_id}`, organiser or above]
2. Change the fields that are wrong — a place misread, a date
   the ambiguous flag warned about. Save.
3. You should see: the status becomes **`edited`**, and the
   flags are **recomputed by the server** from your edited
   draft — a fix you actually made clears its flag; a flag
   you did not address stays.
4. An edited draft approves exactly like a pending one.
   Editing is not approving: the decision is still §5's act.

## 5. Approve or reject

**Task: make it real — or send it away.**

1. **Approve** [`POST …/candidates/{candidate_id}/approve`].
   What happens in that one act: the candidate is approved
   **and the real thing is minted in the same transaction** —
   an event candidate becomes a published event, a task /
   decision / question candidate becomes that record — and
   the candidate's `resulting_entity` now names it. If the
   mint refuses (an event draft with no resolved start date,
   in plain words), the whole approval rolls back and the
   candidate stays pending for you to fix.
2. Who may approve is the **group's own review policy**: the
   door is membership, and the service enforces the group's
   `review_min_role` (the lowest role that may approve) and
   `review_approvals_required` (how many **distinct**
   approvers a candidate needs). With more than one required,
   each approval is recorded (`approvals_recorded` of
   `approvals_required` shown on the candidate) and the
   candidate stays pending until the count is met; approving
   twice yourself is refused — the second must be a second
   person. An **agent seat can never approve**, at any role,
   with any key scope.
3. **Reject** [`POST …/candidates/{candidate_id}/reject`]
   sends the draft away without minting anything. Rejection
   of a change candidate (§7) leaves the event it targeted
   standing, untouched.

*What can go wrong:* approving an event draft whose date
never resolved is refused in plain words — edit the draft
(§4) with a real date and approve again. Nothing is
half-published: the mint and the approval are one act.

## 6. Merge: two drafts, or a draft into an event

**Task: fold a second telling into the truth you already
have.**

- The queue shows likely kin: a candidate's detail carries
  its **matches** — other candidates and events scored on
  title, time, place, and host together, best first, each
  component shown, the strong ones marked `likely`. A bare
  title resemblance alone can never propose a match.
- **Merge two candidates**
  [`POST …/candidates/{candidate_id}/merge`]: the loser is
  kept whole under its `merged` status with `merged_into_id`
  naming the survivor — merged away is not deleted away.
- **Merge a candidate into an event**
  [`POST …/candidates/{candidate_id}/merge-event`]:
  field by field — an empty field fills, a fuller form of
  the same fact refines, agreement is recorded, and genuine
  disagreement is settled by **support**: the value backed
  by more distinct sources wins. An unsupported claim never
  moves anything, and an ambiguous or unresolved start is
  skipped, never guessed. Every source behind the candidate
  becomes a source link on the event, so the event can
  answer, per field, where each fact came from
  [`GET …/events/{event_id}/sources`].

## 7. Change candidates: news about something already real

A later source sometimes says *"the picnic moved to 7:30"* —
that drafts as an **`event_change`** candidate, a kind you
ask extraction for on purpose; it is not part of the default
run, so a first announcement never drafts "changes" out of
itself. At draft time the change is linked to its target by
name; a target it cannot name carries the
`change_target_unknown` flag.

Approving a change candidate applies it inside the approval
act: a start move carries the end by the same amount, a
cancellation cancels with the source's words as the reason,
and a part that cannot move stays while the moved part is
kept. A change with no target, a gone or already-cancelled
event, an unbacked claim, or an unresolved time is refused
plainly and wholly, and the candidate stays pending.

## 8. Contradictions: when sources disagree and neither wins

If a merge meets two values with **equal support**, the
event keeps its current value and a visible **contradiction**
is raised instead of a silent choice: both values, the
sources behind each, the current one marked
[`GET …/events/{event_id}/contradictions`].

**Task: settle a contradiction.**

1. Open the event's contradictions and choose the value that
   is true.
   [`POST …/events/{event_id}/contradictions/{contradiction_id}/resolve`]
2. You may only pick one of the values actually said —
   anything else is refused. The picked value is applied
   through the event's change history, like any edit.
3. A value that later arrives better-sourced settles its own
   open contradiction automatically — check the list after
   big merges; some of your work may already be done.
