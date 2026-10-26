# User guide II — Sources: every way in

*Chapter 2 of the user guide (slice B-183). For members giving
the app things to read. A **source** is anything you hand the
app — a chat, a file, a photo, a link, a voice note. Adding a
source is always a person's act, and the app drafts from sources
only after they land here; nothing in this chapter publishes
anything by itself. Approval is chapter III (B-184).*

## 1. The permission you grant, in plain words — read this first

Every source carries a **permission record**, created with it
and shown with it:

- **Who granted it** (`granted_by`) — your name/label, or
  `system:email-in` for mail that arrived through the group's
  email-in address.
- **What scope was granted** (`granted_scope`) — your own words
  for what the app may use this material for, stored verbatim.

In plain words, adding a source means: *the group may read this
material, the app may draft plans from it, and those drafts may
quote it back to reviewers as evidence.* It does **not** mean
the app may publish the material, send it anywhere, or share it
outside the group — drafts become real only through the review
queue, and a source never leaves its group: a source id from one
group answers "not found" in every other group.

Who may add: **members and above** for paste, file, newsletter,
photo, voice, and link. Two doors are narrower, on purpose:

- The **email-in address** is opened by an **admin or above**.
- A **live chat** (§9) may only be registered by a **person** —
  an agent seat is refused, even with the right role, because
  connecting a continuing feed is a human's grant to make.

## 2. Paste a chat

**Task: turn a group-chat argument into something reviewable.**

1. Copy the conversation from wherever it happened.
2. Choose **Add source → Paste chat**, paste, name the scope in
   your words, and save. [`POST /api/v1/groups/{id}/sources/paste`]
3. You should see: one source, split into **chunks** — one per
   message the parser can see. WhatsApp, Discord, Slack, and
   Messenger export shapes, and plain `Name:` lines, are all
   recognised. A wrapped or unformatted line joins the message
   above it; text before the first speaker becomes a senderless
   chunk. **Nothing you pasted is dropped.**
4. Timestamps are kept exactly as written. A clear numeric date
   becomes a real time; words like "next Friday" stay words
   until the date resolver reads them during extraction — the
   paste step never guesses a date.

*What can go wrong:* a paste with no recognisable speakers
still lands — as senderless chunks — but extraction will have
less to work with. Re-pasting the same chat makes a second
source; it does not merge them (de-duplication works on the
drafts, chapter III).

## 3. Upload a file

1. Choose **Add source → Upload** and pick the file.
   [`POST /api/v1/groups/{id}/sources/upload?filename=…`]
   Uploads are sent as the file itself, not a form.
2. Limits, stated plainly: a single upload may be up to
   **10 MiB** by default (`GPA_MAX_UPLOAD_BYTES`), and the
   group's storage **quota** (default 100 MiB, an admin dial)
   can refuse an upload that would pass it — the refusal names
   the quota and what is already used.
3. The app **sniffs the content**, whatever the filename claims:
   PDF, HTML, calendar (ICS), and plain text are read; a file
   that is really a program is refused, and a virus scan hook
   checks known-bad signatures before anything is stored.
   A scanned-image PDF with no text layer is refused in plain
   words — photograph it as a flyer (§5) instead.
4. You should see: text files become paragraph chunks; an ICS
   file becomes one chunk per calendar entry.

## 4. Upload a newsletter

1. Choose **Add source → Newsletter** and upload the HTML
   email. [`POST /api/v1/groups/{id}/sources/newsletter`]
2. You should see: one chunk per section — headings and rules
   split the letter; links are written inline as
   `anchor text (address)`; an image becomes its description
   text, or the honest placeholder `[image: no description]`.
   Scripts, styles, and the email's head are never chunked.
3. A long newsletter with twelve sections stays **twelve
   ordered sections** — that ordering is what later lets
   extraction draft one candidate per announced event.

## 5. Photograph a flyer

1. Choose **Add source → Flyer photo** and upload the picture.
   [`POST /api/v1/groups/{id}/sources/image`]
2. The app checks the picture by its bytes (PNG, JPEG, GIF, or
   WebP), turns it the right way up using the photo's own
   orientation mark, shrinks an oversized copy to at most
   2400 px, and keeps a small thumbnail.
3. You should see: the flyer listed with its description status
   **`awaiting description`**. That flag is deliberate and
   visible everywhere the flyer appears: until a reading or a
   person gives it a real description, the app treats an
   undescribed image as a **visible defect**, never a quiet
   gap. Reading the flyer into a draft is a separate step —
   chapter III and the vision chapters cover it.
4. *Sideways or blurry?* Orientation is corrected
   automatically; blur is not invented away — a reading that
   cannot see a field says so on the draft rather than guessing.

## 6. Photograph a noticeboard

1. Choose **Add source → Board photo** — the same upload with
   the board option on.
   [`POST /api/v1/groups/{id}/sources/image` with `board=true`]
2. You should see: **one** source, kind `board_photo`, stage
   `awaiting_segmentation`, with exactly one placeholder chunk.
   Nothing has been read yet, and the guide does not pretend
   otherwise.
3. Later, segmentation cuts the board into one child flyer per
   poster — each child inherits this board's permission record
   — and each poster is read on its own. Until that step runs,
   the board is a photograph, not a set of events.

## 7. Paste a link

1. Choose **Add source → Link** and paste the address.
   [`POST /api/v1/groups/{id}/sources/link`]
2. The fetch is guarded, and the guards are part of the feature:
   web addresses only (`http`/`https`); every address the link
   leads to — including through redirects, at most 5 — is
   checked again; private and cloud-metadata addresses are
   blocked; the page may be at most 2 MiB.
3. You should see: the page archived as a source — its bytes
   kept, its text chunked, and the **final** address (after
   redirects) recorded as the source's address.
4. *What can go wrong:* a blocked address is refused with
   `fetch_blocked`; a page that cannot be fetched is
   `fetch_failed`. **A failed fetch archives nothing** — there
   is no half-source to clean up.

## 8. Forward an email

1. An admin opens the channel once: **Group → Email-in** makes
   the group's private inbound address. Its token is shown
   **once** and stored only as a hash.
   [`POST /api/v1/groups/{id}/inbound-address`]
2. Forward the email to that address. It lands as an `email`
   source whose permission record reads
   `granted_by: system:email-in`, with the sender named on
   every chunk. [`POST /api/v1/email-in/{token}`]
3. *What can go wrong:* a wrong token is refused
   (`token_invalid`), the refusal itself is audit-logged, and
   nothing is archived. Treat the address like a key: anyone
   holding it can add sources to the group — an admin can
   replace it by opening a fresh one.

## 9. Record a voice note, and live chats

**Voice.** Choose **Add source → Voice note** and upload the
recording (WAV, MP3, Ogg, FLAC, or M4A, recognised by bytes).
[`POST /api/v1/groups/{id}/sources/voice`] You should see: a
`voice` source at stage `awaiting_transcription`, one plain
note chunk, and a **transcription job** — its id is in the
response and its progress is on the group's stream (§10). When
transcription lands, its chunks are speaker-labelled by the
same discipline as a pasted chat. Until then, the note is
honestly awaiting — extraction skips it and says so.

**Live chat.** Registering a continuing chat connection
[`POST /api/v1/groups/{id}/sources/live`] needs: a **person**
making the grant (§1), a non-empty scope in your words, the
connector kind, and the outside chat's id. The source lands as
`registered`; what syncs into it, and from where, is the
integrations story in the admin guide (B-188). Registering
connects nothing by itself beyond that record.

## 10. Watch progress, then revoke a source

**Progress.** Every source reports a **stage** and a
**progress** figure. Work that runs as a job — transcription
today — also appears on the group's event stream
[`GET /api/v1/groups/{id}/stream`]: one snapshot, then progress
frames, read from the durable job record. Work that happens
inside its own upload (paste, file, newsletter, photo, link)
lands already finished in its response and never passes
through a job — no stream frame is missing; there was no job.

**Task: revoke a source and know what happens to its data.**

1. An **organiser or above** deletes the source.
   [`DELETE /api/v1/groups/{id}/sources/{source_id}`]
2. What is removed, at once: the source's **chunks** and their
   **provenance** — the quotable evidence goes with the text.
3. What is **never** removed: a draft already made from it.
   The draft stays in the review queue, keeps the source's id,
   and gains a **`source_deleted_at`** stamp — a tombstone
   marker saying its evidence is gone, so a reviewer can weigh
   it accordingly. Events already approved stand.
4. **Undo:** sources are soft-deleted through a tombstone and
   can be restored whole — original chunks, provenance, and
   flyers — inside **30 days**
   [`POST /api/v1/groups/{id}/sources/{source_id}/restore`].
   After the window, restore is refused in plain words and the
   tombstone is purged. Group-level retention and erasure,
   which are stronger than this chapter's delete, are covered
   — consequences first — in the admin guide (B-188).
